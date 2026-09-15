from __future__ import annotations

import json
import uuid

import pytest

from factory_dispatcher.edocs_plan import HAR_ARE_IDS, HAR_GOVES_ID, HAR_SEFAZ_ID, build_plan
from factory_dispatcher.edocs_workflow import EdocsSubmissionWorkflow, EdocsWorkflowError


def _pdf(path):
    path.write_bytes(b"%PDF-1.4\n% test\n")
    return str(path)


def _plan(tmp_path):
    manifest = {
        "producerName": "Eunice Mariano Gonçalves",
        "cpf": "101.517.397-73",
        "ie": "114066574",
        "municipality": "Aracruz",
        "files": {
            "procuracao": {
                "path": _pdf(tmp_path / "proc.pdf"),
                "captureMode": "nato-digital-copia",
            },
            "termoAdesao": {
                "path": _pdf(tmp_path / "termo.pdf"),
                "captureMode": "nato-digital-copia",
            },
            "documentosPessoais": {
                "path": _pdf(tmp_path / "docs.pdf"),
                "captureMode": "digitalizado",
            },
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return build_plan(path)


class FakeApi:
    def __init__(self):
        self.uploads = []
        self.captures = []
        self.forwardings = []
        self.events = {}
        self.user_id = str(uuid.uuid4())

    def current_user_id(self):
        return self.user_id

    def patriarcas(self):
        return [{"id": HAR_GOVES_ID, "nome": "GOVES"}]

    def organizacoes(self, _patriarca_id):
        return [{"id": HAR_SEFAZ_ID, "nome": "SEFAZ"}]

    def setores(self, _orgao_id):
        return [{"id": HAR_ARE_IDS["ARACRUZ"], "nome": "ARE ARACRUZ"}]

    def upload_pdf(self, path):
        value = f"2026/9/15/{len(self.uploads)}.temp"
        self.uploads.append((str(path), value))
        return value

    def capture_citizen_file(
        self,
        *,
        mode,
        temporary_id,
        file_name,
        restriction,
        credential_capturer,
        validate_first,
    ):
        event_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        self.captures.append((mode, temporary_id, file_name, event_id))
        self.events[event_id] = {"situacao": "Concluido", "idDocumento": document_id}
        return event_id

    def create_forwarding(
        self,
        *,
        subject,
        message,
        responsible_id,
        destination_ids,
        document_ids,
        send_email_notifications,
        restriction,
    ):
        event_id = str(uuid.uuid4())
        forwarding_id = str(uuid.uuid4())
        self.forwardings.append(
            (subject, message, responsible_id, list(destination_ids), list(document_ids))
        )
        self.events[event_id] = {
            "situacao": "Concluido",
            "idEncaminhamento": forwarding_id,
        }
        return event_id

    def poll_event(self, event_id):
        return self.events[event_id]

    @staticmethod
    def event_document_id(event):
        return event["idDocumento"]

    @staticmethod
    def event_forwarding_id(event):
        return event["idEncaminhamento"]


def test_submit_resumes_without_duplicate_writes(tmp_path):
    api = FakeApi()
    plan = _plan(tmp_path)
    workflow = EdocsSubmissionWorkflow(api, tmp_path / "state.json")

    first = workflow.submit(plan)
    second = workflow.submit(plan)

    assert first["forwardingId"] == second["forwardingId"]
    assert first["status"] == "SENT"
    assert len(api.uploads) == 3
    assert len(api.captures) == 3
    assert len(api.forwardings) == 1


def test_uncertain_capture_refuses_blind_retry(tmp_path):
    api = FakeApi()
    plan = _plan(tmp_path)
    workflow = EdocsSubmissionWorkflow(api, tmp_path / "state.json")
    state = workflow._state_for(plan)
    state["documents"]["procuracao"]["temporaryUploadId"] = "2026/9/15/x.temp"
    state["documents"]["procuracao"]["captureMutationUncertain"] = True
    workflow._save(state)

    with pytest.raises(EdocsWorkflowError, match="uncertain"):
        workflow.submit(plan)


def test_changed_package_cannot_reuse_checkpoint(tmp_path):
    api = FakeApi()
    plan = _plan(tmp_path)
    workflow = EdocsSubmissionWorkflow(api, tmp_path / "state.json")
    workflow._state_for(plan)

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    manifest["message"] = "Changed message"
    (tmp_path / "manifest2.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    changed = build_plan(tmp_path / "manifest2.json")
    with pytest.raises(EdocsWorkflowError, match="different package"):
        workflow.submit(changed)
