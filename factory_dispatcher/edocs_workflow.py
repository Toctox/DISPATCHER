from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .edocs_api import EdocsApi, organizational_restriction
from .edocs_plan import SubmissionPlan, plan_as_dict


class EdocsWorkflowError(RuntimeError):
    pass


class EdocsSubmissionWorkflow:
    """Resume-safe package submission.

    Every institutional write is checkpointed before and after the request. If a
    transport exception happens after a write may have reached E-Docs but before
    the returned event id is durably stored, the state is marked uncertain and
    automatic retry is refused. This deliberately favors manual reconciliation
    over duplicate legal acts.
    """

    def __init__(self, api: EdocsApi, state_path: str | Path) -> None:
        self.api = api
        self.state_path = Path(state_path).expanduser().resolve()

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise EdocsWorkflowError("checkpoint must be a JSON object")
        return data

    def _save(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, self.state_path)

    def _initial_state(self, plan: SubmissionPlan) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "planFingerprint": plan.fingerprint,
            "plan": plan_as_dict(plan),
            "responsibleId": None,
            "destinationVerified": False,
            "documents": {
                item.role: {
                    "path": str(item.path),
                    "sha256": item.sha256,
                    "captureMode": item.capture_mode,
                    "temporaryUploadId": None,
                    "captureEventId": None,
                    "documentId": None,
                    "captureMutationUncertain": False,
                }
                for item in plan.files
            },
            "forwardingEventId": None,
            "forwardingId": None,
            "mutationUncertain": False,
            "status": "PREPARED",
        }

    def _state_for(self, plan: SubmissionPlan) -> dict[str, Any]:
        state = self._load()
        if not state:
            state = self._initial_state(plan)
            self._save(state)
            return state
        if state.get("schemaVersion") != 1:
            raise EdocsWorkflowError("unsupported checkpoint schema")
        if state.get("planFingerprint") != plan.fingerprint:
            raise EdocsWorkflowError(
                "checkpoint belongs to a different package; use a new state file"
            )
        return state

    def _resolve_destination(self, plan: SubmissionPlan) -> None:
        patriarca = EdocsApi.resolve_named_agent(
            self.api.patriarcas(), contains=("GOVES",)
        )
        orgao = EdocsApi.resolve_named_agent(
            self.api.organizacoes(patriarca.id), contains=("SEFAZ",)
        )
        EdocsApi.resolve_named_agent(
            self.api.setores(orgao.id),
            contains=("ARE", plan.municipality),
            expected_id=plan.destination.id,
        )

    def submit(self, plan: SubmissionPlan) -> dict[str, Any]:
        state = self._state_for(plan)
        if state.get("status") == "SENT":
            return state
        if state.get("mutationUncertain"):
            raise EdocsWorkflowError(
                "previous forwarding mutation is uncertain; reconcile manually before retry"
            )

        if not state.get("responsibleId"):
            state["responsibleId"] = self.api.current_user_id()
            self._save(state)

        if not state.get("destinationVerified"):
            self._resolve_destination(plan)
            state["destinationVerified"] = True
            self._save(state)

        for item in plan.files:
            doc = state["documents"][item.role]
            if doc.get("captureMutationUncertain"):
                raise EdocsWorkflowError(
                    f"capture mutation for {item.role} is uncertain; reconcile manually before retry"
                )

            if not doc.get("temporaryUploadId"):
                doc["temporaryUploadId"] = self.api.upload_pdf(item.path)
                state["status"] = "UPLOADED"
                self._save(state)

            if not doc.get("documentId"):
                if not doc.get("captureEventId"):
                    # Validate before arming the write-ahead barrier: validation itself
                    # does not create the institutional document.
                    self.api.validate_capture(
                        mode=item.capture_mode,
                        temporary_id=doc["temporaryUploadId"],
                        file_name=item.path.name,
                        restriction=organizational_restriction(),
                        credential_capturer=True,
                    )
                    # If capture is accepted but the response is lost, do not blindly
                    # create a second legal document on the next run.
                    doc["captureMutationUncertain"] = True
                    self._save(state)
                    event_id = self.api.capture_citizen_file(
                        mode=item.capture_mode,
                        temporary_id=doc["temporaryUploadId"],
                        file_name=item.path.name,
                        restriction=organizational_restriction(),
                        credential_capturer=True,
                        validate_first=False,
                    )
                    doc["captureEventId"] = event_id
                    doc["captureMutationUncertain"] = False
                    state["status"] = "CAPTURE_SUBMITTED"
                    self._save(state)

                event = self.api.poll_event(doc["captureEventId"])
                doc["documentId"] = EdocsApi.event_document_id(event)
                state["status"] = "CAPTURED"
                self._save(state)

        if not state.get("forwardingId"):
            if not state.get("forwardingEventId"):
                state["mutationUncertain"] = True
                self._save(state)
                event_id = self.api.create_forwarding(
                    subject=plan.subject,
                    message=plan.message,
                    responsible_id=state["responsibleId"],
                    destination_ids=[plan.destination.id],
                    document_ids=[
                        state["documents"][item.role]["documentId"] for item in plan.files
                    ],
                    send_email_notifications=True,
                    restriction=organizational_restriction(),
                )
                state["forwardingEventId"] = event_id
                state["mutationUncertain"] = False
                state["status"] = "FORWARDING_SUBMITTED"
                self._save(state)

            event = self.api.poll_event(state["forwardingEventId"])
            state["forwardingId"] = EdocsApi.event_forwarding_id(event)
            state["status"] = "SENT"
            self._save(state)

        return state

    def reconcile_capture_event(self, role: str, event_id: str) -> dict[str, Any]:
        state = self._load()
        documents = state.get("documents") or {}
        if role not in documents:
            raise EdocsWorkflowError(f"unknown role {role!r}")
        doc = documents[role]
        if doc.get("captureEventId") and doc["captureEventId"] != event_id:
            raise EdocsWorkflowError("capture event id already differs")
        doc["captureEventId"] = event_id
        doc["captureMutationUncertain"] = False
        self._save(state)
        return state

    def reconcile_forwarding_event(self, event_id: str) -> dict[str, Any]:
        state = self._load()
        if state.get("forwardingEventId") and state["forwardingEventId"] != event_id:
            raise EdocsWorkflowError("forwarding event id already differs")
        state["forwardingEventId"] = event_id
        state["mutationUncertain"] = False
        self._save(state)
        return state
