from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from googleapiclient.errors import HttpError

from .engine import Dispatcher
from .errors import ContractError
from .models import ACTIVE_STATES, DispatchJob, DispatchState, isoformat


_CONTINUATION_AGENT = "ORCHESTRATOR"
_DECISION_AGENT = "DECISION"
_ALLOWED_PLAN_AGENTS = {
    "DOMAIN",
    "ARCHITECTURE",
    "REDTEAM",
    "QA",
    "SECURITY",
    "CONCURRENCY",
    "ORCHESTRATOR",
    "DECISION",
    "EVIDENCE",
    "RECOVERY",
    "E2E",
    "DEPENDENCY",
    "RELEASE",
    "MAINTENANCE",
    "REGISTRAR",
    "CODEX_IMPLEMENTER",
}
_ALLOWED_EXECUTORS = {"CHATGPT", "CODEX"}
_WORKSTREAM_HEADERS = (
    "workstreamId",
    "changeId",
    "targetRef",
    "status",
    "priority",
    "targetState",
    "requiredSources",
    "instructions",
)


def _slug(value: str, maximum: int = 48) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").upper()
    return (cleaned or "WORK")[:maximum]


class AutonomousDispatcher(Dispatcher):
    """Mechanical continuation layer around the proven one-shot Dispatcher.

    It never chooses product semantics or overrides gates. On IDLE it can only:
    publish DECISION artifacts to the immutable decision folder, materialize a
    strictly validated ORCHESTRATOR dispatch plan, enqueue one ORCHESTRATOR
    continuation checkpoint for staged work, or seed one ORCHESTRATOR planning
    checkpoint from an explicitly ACTIVE WORKSTREAMS row.
    """

    def tick(self, *, dry_run: bool = False) -> dict[str, Any]:
        result = super().tick(dry_run=dry_run)
        if dry_run or not self.settings.writes_enabled or result.get("outcome") != "IDLE":
            return result

        factory = self.gateway.read_factory_config()
        jobs = self.gateway.read_queue()

        published = self._publish_decision_results(jobs)
        if published:
            jobs = self.gateway.read_queue()

        materialized = self._materialize_pending_orchestrator_plan(jobs, factory)
        if materialized:
            launched = super().tick(dry_run=False)
            launched["autonomousAction"] = "DISPATCH_PLAN_MATERIALIZED"
            launched["materializedJobs"] = materialized
            launched["publishedDecisions"] = published
            return launched

        continuation = self._ensure_orchestrator_continuation(jobs, factory)
        if continuation:
            launched = super().tick(dry_run=False)
            launched["autonomousAction"] = "ORCHESTRATOR_CONTINUATION_ENQUEUED"
            launched["continuationDispatchId"] = continuation
            launched["publishedDecisions"] = published
            return launched

        planner = self._ensure_workstream_planner(jobs, factory)
        if planner:
            launched = super().tick(dry_run=False)
            launched["autonomousAction"] = "WORKSTREAM_PLANNER_ENQUEUED"
            launched["plannerDispatchId"] = planner
            launched["publishedDecisions"] = published
            return launched

        if published:
            result["publishedDecisions"] = published
        return result

    def _config_mapping(self) -> dict[str, str]:
        values = self.gateway.sheets.spreadsheets().values()
        rows = values.get(
            spreadsheetId=self.gateway.spreadsheet_id, range="CONFIG!A1:D200"
        ).execute().get("values", [])
        return {
            str(row[0]): str(row[1])
            for row in rows[1:]
            if len(row) >= 2 and str(row[0]).strip()
        }

    def _append_queue_row(self, values: list[Any]) -> None:
        if len(values) != 30:
            raise ContractError("autonomous queue row must contain exactly 30 values")
        self.gateway.sheets.spreadsheets().values().append(
            spreadsheetId=self.gateway.spreadsheet_id,
            range="QUEUE!A:AD",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        ).execute()

    def _upload_request(self, factory, dispatch_id: str, payload: dict[str, Any]) -> str:
        return self.gateway.upload_text(
            factory.requests_folder_id,
            f"DISPATCH_REQUEST__{dispatch_id}.json",
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        )

    def _publish_decision_results(self, jobs: list[DispatchJob]) -> int:
        config = self._config_mapping()
        decisions_folder = config.get("CANONICAL_03_DECISIONS_ID", "").strip()
        if not decisions_folder:
            return 0
        published = 0
        drive = self.gateway.drive
        for job in jobs:
            if (
                job.agent_id != _DECISION_AGENT
                or job.status != DispatchState.RESULT_STAGED
                or not job.receipt_drive_id
            ):
                continue
            receipt = self.gateway.read_json_artifact(job.receipt_drive_id)
            for item in receipt.get("producedArtifacts", []):
                if not isinstance(item, dict):
                    continue
                source_id = str(item.get("stagingDriveId") or "").strip()
                artifact_id = _slug(
                    str(item.get("artifactId") or "FACTORY-POLICY-DECISION"), 100
                )
                if not source_id:
                    continue
                metadata = drive.files().get(
                    fileId=source_id, fields="id,name,mimeType", supportsAllDrives=True
                ).execute()
                source_name = str(metadata.get("name") or f"{artifact_id}.json")
                query_name = source_name.replace("'", "\\'")
                query = (
                    f"'{decisions_folder}' in parents and name = '{query_name}' "
                    "and trashed = false"
                )
                existing = drive.files().list(q=query, fields="files(id)", pageSize=2).execute()
                if existing.get("files"):
                    continue
                drive.files().copy(
                    fileId=source_id,
                    body={"name": source_name, "parents": [decisions_folder]},
                    fields="id",
                    supportsAllDrives=True,
                ).execute()
                published += 1
        return published

    def _materialize_pending_orchestrator_plan(self, jobs: list[DispatchJob], factory) -> int:
        materialized = 0
        existing_dedupes = {job.dedupe_key for job in jobs if job.dedupe_key}
        orchestrators = sorted(
            (
                job
                for job in jobs
                if job.agent_id == _CONTINUATION_AGENT
                and job.status == DispatchState.RESULT_STAGED
                and job.receipt_drive_id
            ),
            key=lambda item: item.row_number,
        )
        for orchestrator in orchestrators:
            receipt = self.gateway.read_json_artifact(orchestrator.receipt_drive_id)
            dispatch_plan = receipt.get("dispatchPlan")
            if not isinstance(dispatch_plan, dict) or dispatch_plan.get("present") is not True:
                continue
            plan_id = str(dispatch_plan.get("stagingDriveId") or "").strip()
            if not plan_id:
                raise ContractError(
                    "ORCHESTRATOR receipt declares dispatchPlan without stagingDriveId"
                )
            plan = self.gateway.read_json_artifact(plan_id)
            if plan.get("artifactType") != "DISPATCH_PLAN":
                raise ContractError("dispatchPlan artifactType must be DISPATCH_PLAN")
            if str(plan.get("changeId") or "").strip() != orchestrator.change_id:
                raise ContractError("dispatchPlan changeId mismatch")
            jobs_raw = plan.get("jobs")
            if not isinstance(jobs_raw, list) or len(jobs_raw) > 20:
                raise ContractError("dispatchPlan jobs must be an array of at most 20 items")
            for index, planned in enumerate(jobs_raw, start=1):
                if not isinstance(planned, dict):
                    raise ContractError("dispatchPlan job must be an object")
                agent = str(planned.get("agentId") or "").strip().upper()
                executor = str(planned.get("executorType") or "CHATGPT").strip().upper()
                if agent not in _ALLOWED_PLAN_AGENTS or executor not in _ALLOWED_EXECUTORS:
                    raise ContractError("dispatchPlan requested an unsupported agent/executor")
                dedupe = str(planned.get("dedupeKey") or "").strip()
                if not dedupe:
                    plan_identity = str(plan.get("planId") or plan_id)
                    identity = plan_identity + ":" + str(index)
                    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
                    dedupe = f"AUTO-{orchestrator.change_id}-{_slug(agent, 20)}-{digest}"
                if dedupe in existing_dedupes:
                    continue
                digest = hashlib.sha256(dedupe.encode()).hexdigest()[:8].upper()
                dispatch_id = (
                    f"D-{_slug(orchestrator.change_id, 24)}-"
                    f"{_slug(agent, 20)}-{digest}-{index:02d}"
                )
                root_run_id = str(
                    planned.get("rootRunId")
                    or f"R-{_slug(orchestrator.change_id, 24)}-AUTO-{digest}"
                )
                target_ref = str(planned.get("targetRef") or orchestrator.target_ref)
                task_type = str(planned.get("taskType") or "CONTINUATION_WORK")
                side_effect = str(planned.get("sideEffectClass") or "READ_ONLY")
                retry_policy = str(
                    planned.get("retryPolicy") or "RECONCILE_BEFORE_RETRY"
                )
                max_attempts = int(planned.get("maxAttempts") or 1)
                priority = int(planned.get("priority") or 10)
                depends_on = planned.get("dependsOn") or []
                if not isinstance(depends_on, list):
                    raise ContractError("dispatchPlan dependsOn must be an array")
                required_sources = planned.get("requiredSources") or []
                if not isinstance(required_sources, list):
                    raise ContractError("dispatchPlan requiredSources must be an array")
                instructions = str(planned.get("instructions") or "").strip()
                if not instructions:
                    raise ContractError("dispatchPlan job instructions are required")
                expected_pattern = str(
                    planned.get("expectedArtifactPattern")
                    or f"{orchestrator.change_id}-{agent}-*.json"
                )
                request = {
                    "schemaVersion": 1,
                    "artifactType": "DISPATCH_REQUEST",
                    "dispatchId": dispatch_id,
                    "rootRunId": root_run_id,
                    "generation": orchestrator.generation + 1,
                    "agentId": agent,
                    "changeId": orchestrator.change_id,
                    "taskType": task_type,
                    "executorType": executor,
                    "targetRef": {"kind": "DRIVE_ARTIFACT", "id": target_ref},
                    "causedBy": [
                        orchestrator.dispatch_id,
                        str(plan.get("planId") or plan_id),
                    ],
                    "completion": {"terminalReceiptRequired": True},
                    "retryPolicy": retry_policy,
                    "sideEffectClass": side_effect,
                    "maxAttempts": max_attempts,
                    "maxExecutionMinutes": int(planned.get("maxExecutionMinutes") or 45),
                    "dependsOn": depends_on,
                    "readinessRule": "ALL_DEPENDENCIES_SUCCEEDED",
                    "status": "READY" if not depends_on else "WAITING_DEPENDENCIES",
                    "createdAt": isoformat(datetime.now(UTC)),
                    "requiredSources": required_sources,
                    "instructions": instructions,
                    "allowedWrites": {
                        "stagingOnly": True,
                        "expectedArtifactPattern": expected_pattern,
                        "receiptRequired": True,
                    },
                    "dedupeKey": dedupe,
                }
                request_id = self._upload_request(factory, dispatch_id, request)
                now = isoformat(datetime.now(UTC))
                self._append_queue_row(
                    [
                        dispatch_id,
                        root_run_id,
                        orchestrator.generation + 1,
                        agent,
                        orchestrator.change_id,
                        task_type,
                        target_ref,
                        "READY" if not depends_on else "WAITING_DEPENDENCIES",
                        priority,
                        dedupe,
                        json.dumps(depends_on, separators=(",", ":")) if depends_on else "",
                        0,
                        max_attempts,
                        "",
                        "",
                        "",
                        "",
                        retry_policy,
                        side_effect,
                        request_id,
                        "",
                        "",
                        "",
                        "",
                        "",
                        now,
                        "",
                        "",
                        "",
                        now,
                    ]
                )
                existing_dedupes.add(dedupe)
                materialized += 1
        return materialized

    def _ensure_orchestrator_continuation(
        self, jobs: list[DispatchJob], factory
    ) -> str | None:
        # Do not invent planning work while execution/dependency work is still pending.
        pending_states = {DispatchState.READY, DispatchState.WAITING_DEPENDENCIES}
        if any(job.status in ACTIVE_STATES or job.status in pending_states for job in jobs):
            return None

        config = self._config_mapping()
        prompt_id = config.get(
            "ORCHESTRATOR_PROMPT_DOC_ID", "1lM9ObuTA108GnAIDzqzZ-ciK0tPPvtJUXj1YEZAs_Yg"
        )
        start_here = config.get(
            "START_HERE_ID", "1qjQt-8NvqyAog5MJnT8hV4rYejWhp3uh5u-FAdn_R3k"
        )

        changes = sorted({job.change_id for job in jobs if job.change_id})
        for change_id in changes:
            staged = [
                job
                for job in jobs
                if job.change_id == change_id
                and job.agent_id != _CONTINUATION_AGENT
                and job.status == DispatchState.RESULT_STAGED
                and job.receipt_drive_id
            ]
            if not staged:
                continue
            identity = "|".join(
                f"{job.dispatch_id}:{job.receipt_drive_id}"
                for job in sorted(staged, key=lambda item: item.row_number)
            )
            digest = hashlib.sha256(identity.encode()).hexdigest()[:12].upper()
            dedupe = f"AUTO-CONTINUE-{change_id}-{digest}"
            if any(job.dedupe_key == dedupe for job in jobs):
                continue
            latest = max(staged, key=lambda item: item.row_number)
            dispatch_id = f"D-{_slug(change_id, 24)}-ORCHESTRATOR-{digest}"
            root_run_id = f"R-{_slug(change_id, 24)}-CONT-{digest}"
            receipt_refs = [
                {
                    "kind": "DRIVE_ARTIFACT",
                    "id": job.receipt_drive_id,
                    "title": f"Receipt {job.dispatch_id}",
                }
                for job in staged
            ]
            request = {
                "schemaVersion": 1,
                "artifactType": "DISPATCH_REQUEST",
                "dispatchId": dispatch_id,
                "rootRunId": root_run_id,
                "generation": max(job.generation for job in staged) + 1,
                "agentId": _CONTINUATION_AGENT,
                "changeId": change_id,
                "taskType": "AUTONOMOUS_CONTINUATION",
                "executorType": "CHATGPT",
                "targetRef": {"kind": "DRIVE_ARTIFACT", "id": latest.target_ref},
                "causedBy": [job.dispatch_id for job in staged],
                "completion": {"terminalReceiptRequired": True},
                "retryPolicy": "RECONCILE_BEFORE_RETRY",
                "sideEffectClass": "READ_ONLY",
                "maxAttempts": 1,
                "maxExecutionMinutes": 45,
                "dependsOn": [],
                "readinessRule": "ALL_DEPENDENCIES_SUCCEEDED",
                "status": "READY",
                "createdAt": isoformat(datetime.now(UTC)),
                "requiredSources": [
                    {
                        "kind": "DRIVE_ARTIFACT",
                        "id": factory.bootstrap_doc_id,
                        "title": "FACTORY_EXECUTOR_BOOTSTRAP-v1",
                    },
                    {
                        "kind": "DRIVE_ARTIFACT",
                        "id": start_here,
                        "title": "START_HERE_PROJECT_FACTORY",
                    },
                    {
                        "kind": "DRIVE_ARTIFACT",
                        "id": prompt_id,
                        "title": "PROMPT_ORCHESTRATOR",
                    },
                    *receipt_refs,
                ],
                "instructions": (
                    "Autonomous continuation checkpoint. Reconstruct the current CR state from "
                    "canonical governance, the exact terminal receipts and their staged artifacts. "
                    "Consolidate evidence without erasing dissent. If a decision is needed, plan a "
                    "DECISION agent job; do not silently choose HUMAN_ONLY classes. If further "
                    "governed work is needed, produce exactly one DISPATCH_PLAN JSON in this attempt "
                    "staging with artifactType=DISPATCH_PLAN, planId, changeId, rationale, "
                    "evidenceRefs and jobs[]. Each jobs[] item must contain agentId, executorType, "
                    "taskType, targetRef, instructions, dedupeKey, sideEffectClass, retryPolicy, "
                    "priority, maxAttempts, requiredSources and expectedArtifactPattern. In the "
                    "terminal receipt set dispatchPlan.present=true and dispatchPlan.stagingDriveId "
                    "to that plan file. If no further work is legitimately actionable, set "
                    "dispatchPlan.present=false and explain why in the ORCHESTRATOR artifact. Never "
                    "bypass gates or implement product code."
                ),
                "allowedWrites": {
                    "stagingOnly": True,
                    "expectedArtifactPattern": (
                        f"{change_id}-ORCHESTRATOR-CONTINUATION-*.json"
                    ),
                    "receiptRequired": True,
                },
                "dedupeKey": dedupe,
            }
            request_id = self._upload_request(factory, dispatch_id, request)
            now = isoformat(datetime.now(UTC))
            self._append_queue_row(
                [
                    dispatch_id,
                    root_run_id,
                    request["generation"],
                    _CONTINUATION_AGENT,
                    change_id,
                    "AUTONOMOUS_CONTINUATION",
                    latest.target_ref,
                    "READY",
                    100,
                    dedupe,
                    "",
                    0,
                    1,
                    "",
                    "",
                    "",
                    "",
                    "RECONCILE_BEFORE_RETRY",
                    "READ_ONLY",
                    request_id,
                    "",
                    "",
                    "",
                    "",
                    "",
                    now,
                    "",
                    "",
                    "",
                    now,
                ]
            )
            return dispatch_id
        return None

    def _read_active_workstreams(self) -> list[dict[str, Any]]:
        """Read an optional WORKSTREAMS sheet without making it a deployment prerequisite."""
        values = self.gateway.sheets.spreadsheets().values()
        try:
            rows = values.get(
                spreadsheetId=self.gateway.spreadsheet_id,
                range="WORKSTREAMS!A1:H500",
            ).execute().get("values", [])
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status in {400, 404}:
                return []
            raise
        if not rows:
            return []

        headers = [str(value).strip() for value in rows[0]]
        missing = [header for header in _WORKSTREAM_HEADERS if header not in headers]
        if missing:
            raise ContractError(f"WORKSTREAMS is missing headers: {', '.join(missing)}")

        workstreams: list[dict[str, Any]] = []
        for row_number, raw in enumerate(rows[1:], start=2):
            padded = list(raw) + [""] * (len(headers) - len(raw))
            row = dict(zip(headers, padded, strict=True))
            if str(row.get("status") or "").strip().upper() != "ACTIVE":
                continue
            workstream_id = str(row.get("workstreamId") or "").strip()
            change_id = str(row.get("changeId") or "").strip()
            target_ref = str(row.get("targetRef") or "").strip()
            if not workstream_id or not change_id or not target_ref:
                raise ContractError(
                    f"WORKSTREAMS row {row_number} requires workstreamId, changeId and targetRef"
                )
            try:
                priority = int(str(row.get("priority") or "100"))
            except ValueError as exc:
                raise ContractError(
                    f"WORKSTREAMS row {row_number} priority must be an integer"
                ) from exc
            if priority < 1:
                raise ContractError(
                    f"WORKSTREAMS row {row_number} priority must be positive"
                )

            required_sources: list[Any] = []
            source_text = str(row.get("requiredSources") or "").strip()
            if source_text:
                try:
                    parsed = json.loads(source_text)
                except json.JSONDecodeError as exc:
                    raise ContractError(
                        f"WORKSTREAMS row {row_number} requiredSources must be JSON"
                    ) from exc
                if not isinstance(parsed, list):
                    raise ContractError(
                        f"WORKSTREAMS row {row_number} requiredSources must be an array"
                    )
                required_sources = parsed

            workstreams.append(
                {
                    "rowNumber": row_number,
                    "workstreamId": workstream_id,
                    "changeId": change_id,
                    "targetRef": target_ref,
                    "priority": priority,
                    "targetState": str(row.get("targetState") or "").strip(),
                    "requiredSources": required_sources,
                    "instructions": str(row.get("instructions") or "").strip(),
                }
            )
        return sorted(
            workstreams,
            key=lambda item: (-int(item["priority"]), int(item["rowNumber"])),
        )

    def _ensure_workstream_planner(self, jobs: list[DispatchJob], factory) -> str | None:
        """Seed one ORCHESTRATOR planning job for an explicit active workstream.

        This is deliberately mechanical. WORKSTREAMS says what change is active and
        which canonical target the Orchestrator must inspect; only the Orchestrator
        may decide which specialist jobs belong in the resulting DISPATCH_PLAN.
        """
        pending_states = {DispatchState.READY, DispatchState.WAITING_DEPENDENCIES}
        if any(job.status in ACTIVE_STATES or job.status in pending_states for job in jobs):
            return None

        workstreams = self._read_active_workstreams()
        if not workstreams:
            return None

        config = self._config_mapping()
        prompt_id = config.get(
            "ORCHESTRATOR_PROMPT_DOC_ID", "1lM9ObuTA108GnAIDzqzZ-ciK0tPPvtJUXj1YEZAs_Yg"
        )
        start_here = config.get(
            "START_HERE_ID", "1qjQt-8NvqyAog5MJnT8hV4rYejWhp3uh5u-FAdn_R3k"
        )
        existing_dedupes = {job.dedupe_key for job in jobs if job.dedupe_key}

        for workstream in workstreams:
            workstream_id = str(workstream["workstreamId"])
            change_id = str(workstream["changeId"])
            target_ref = str(workstream["targetRef"])
            dedupe = f"AUTO-WORKSTREAM-{_slug(workstream_id, 40)}"
            if dedupe in existing_dedupes:
                continue

            change_jobs = [job for job in jobs if job.change_id == change_id]
            generation = max((job.generation for job in change_jobs), default=0) + 1
            digest = hashlib.sha256(dedupe.encode()).hexdigest()[:12].upper()
            dispatch_id = f"D-{_slug(change_id, 24)}-ORCHESTRATOR-PLAN-{digest}"
            root_run_id = f"R-{_slug(change_id, 24)}-PLAN-{digest}"
            target_state = str(workstream.get("targetState") or "").strip()
            extra_instructions = str(workstream.get("instructions") or "").strip()
            required_sources = list(workstream.get("requiredSources") or [])

            instructions = (
                f"Autonomous workstream planning checkpoint for {workstream_id}. Reconstruct the "
                f"current governed state of {change_id} from canonical sources. The workstream "
                f"targetRef is {target_ref}. "
            )
            if target_state:
                instructions += f"The requested target state is {target_state}. "
            if extra_instructions:
                instructions += f"Workstream instruction: {extra_instructions} "
            instructions += (
                "Do not bypass gates or invent product semantics. Decide which authorized specialist "
                "jobs are actually needed and produce exactly one DISPATCH_PLAN JSON in this attempt "
                "staging with artifactType=DISPATCH_PLAN, planId, changeId, rationale, evidenceRefs "
                "and jobs[]. Each jobs[] item must contain agentId, executorType, taskType, targetRef, "
                "instructions, dedupeKey, sideEffectClass, retryPolicy, priority, maxAttempts, "
                "requiredSources and expectedArtifactPattern. If no further governed work is "
                "actionable, set dispatchPlan.present=false in the terminal receipt and explain why."
            )

            request = {
                "schemaVersion": 1,
                "artifactType": "DISPATCH_REQUEST",
                "dispatchId": dispatch_id,
                "rootRunId": root_run_id,
                "generation": generation,
                "agentId": _CONTINUATION_AGENT,
                "changeId": change_id,
                "taskType": "AUTONOMOUS_WORKSTREAM_PLANNING",
                "executorType": "CHATGPT",
                "targetRef": {"kind": "DRIVE_ARTIFACT", "id": target_ref},
                "causedBy": [workstream_id],
                "completion": {"terminalReceiptRequired": True},
                "retryPolicy": "RECONCILE_BEFORE_RETRY",
                "sideEffectClass": "READ_ONLY",
                "maxAttempts": 1,
                "maxExecutionMinutes": 45,
                "dependsOn": [],
                "readinessRule": "ALL_DEPENDENCIES_SUCCEEDED",
                "status": "READY",
                "createdAt": isoformat(datetime.now(UTC)),
                "requiredSources": [
                    {
                        "kind": "DRIVE_ARTIFACT",
                        "id": factory.bootstrap_doc_id,
                        "title": "FACTORY_EXECUTOR_BOOTSTRAP-v1",
                    },
                    {
                        "kind": "DRIVE_ARTIFACT",
                        "id": start_here,
                        "title": "START_HERE_PROJECT_FACTORY",
                    },
                    {
                        "kind": "DRIVE_ARTIFACT",
                        "id": prompt_id,
                        "title": "PROMPT_ORCHESTRATOR",
                    },
                    *required_sources,
                ],
                "instructions": instructions,
                "allowedWrites": {
                    "stagingOnly": True,
                    "expectedArtifactPattern": f"{change_id}-ORCHESTRATOR-PLAN-*.json",
                    "receiptRequired": True,
                },
                "dedupeKey": dedupe,
            }
            request_id = self._upload_request(factory, dispatch_id, request)
            now = isoformat(datetime.now(UTC))
            self._append_queue_row(
                [
                    dispatch_id,
                    root_run_id,
                    generation,
                    _CONTINUATION_AGENT,
                    change_id,
                    "AUTONOMOUS_WORKSTREAM_PLANNING",
                    target_ref,
                    "READY",
                    int(workstream["priority"]),
                    dedupe,
                    "",
                    0,
                    1,
                    "",
                    "",
                    "",
                    "",
                    "RECONCILE_BEFORE_RETRY",
                    "READ_ONLY",
                    request_id,
                    "",
                    "",
                    "",
                    "",
                    "",
                    now,
                    "",
                    "",
                    "",
                    now,
                ]
            )
            return dispatch_id
        return None