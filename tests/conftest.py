from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from factory_dispatcher.config import FactoryConfig, LocalSettings
from factory_dispatcher.models import DispatchJob, DispatchState, Receipt, isoformat
from factory_dispatcher.receipts import AttemptContext

NOW = datetime(2026, 9, 4, 21, 30, tzinfo=UTC)


def queue_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "dispatchId": "D-TEST-0001",
        "rootRunId": "R-TEST-0001",
        "generation": 1,
        "agentId": "REGISTRAR",
        "changeId": "",
        "taskType": "READ_ONLY_TEST",
        "targetRef": "artifact@Drive:test",
        "status": "READY",
        "priority": 10,
        "dedupeKey": "TEST-KEY-0001",
        "dependsOn": "",
        "attempt": 0,
        "maxAttempts": 3,
        "leaseOwner": "",
        "leaseToken": "",
        "leaseExpiresAt": "",
        "notBefore": "",
        "retryPolicy": "SAFE_RETRY",
        "sideEffectClass": "READ_ONLY",
        "requestDriveId": "request-1",
        "stagingFolderId": "",
        "receiptDriveId": "",
        "producedArtifactIds": "",
        "lastErrorCode": "",
        "lastErrorDetail": "",
        "createdAt": isoformat(NOW - timedelta(hours=1)),
        "claimedAt": "",
        "startedAt": "",
        "finishedAt": "",
        "updatedAt": isoformat(NOW - timedelta(hours=1)),
    }
    row.update(overrides)
    return row


class FakeGateway:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        requests: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.rows = {index: dict(row) for index, row in enumerate(rows, start=2)}
        self.requests = requests or {}
        self.receipts: dict[str, Receipt] = {}
        self.events: list[list[Any]] = []
        self.uploads: list[tuple[str, str, str]] = []
        self.updates: list[tuple[int, dict[str, Any]]] = []
        self.factory = FactoryConfig(
            dispatch_folder_id="dispatch-root",
            requests_folder_id="requests-root",
            staging_folder_id="staging-root",
            receipts_folder_id="receipts-root",
            archive_folder_id="archive-root",
            bootstrap_doc_id="bootstrap-doc",
            protocol_doc_id="protocol-doc",
            default_lease_minutes=45,
            default_max_attempts=3,
            retry_backoff_minutes=5,
            max_concurrent_chatgpt_executions=1,
            max_executions_per_root_run=25,
        )

    def read_factory_config(self) -> FactoryConfig:
        return self.factory

    def read_queue(self) -> list[DispatchJob]:
        return [DispatchJob.from_row(number, row) for number, row in self.rows.items()]

    def read_job(self, row_number: int) -> DispatchJob:
        return DispatchJob.from_row(row_number, self.rows[row_number])

    def update_job(self, row_number: int, fields: dict[str, Any]) -> None:
        self.rows[row_number].update(fields)
        self.updates.append((row_number, dict(fields)))

    def claim_job(
        self,
        job: DispatchJob,
        *,
        owner: str,
        lease_token: str,
        now: datetime,
        lease_minutes: int,
    ) -> DispatchJob:
        current = self.read_job(job.row_number)
        if current.status != DispatchState.READY:
            raise AssertionError("fake claim expected READY")
        self.update_job(
            job.row_number,
            {
                "status": "CLAIMED",
                "attempt": current.attempt + 1,
                "leaseOwner": owner,
                "leaseToken": lease_token,
                "leaseExpiresAt": isoformat(now + timedelta(minutes=lease_minutes)),
                "claimedAt": isoformat(now),
                "updatedAt": isoformat(now),
            },
        )
        return self.read_job(job.row_number)

    def append_event(self, values: list[Any]) -> None:
        self.events.append(values)

    def read_json_artifact(
        self, file_id: str, expected_parent_id: str | None = None
    ) -> dict[str, Any]:
        return dict(self.requests[file_id])

    def create_staging(self, root_id: str, dispatch_id: str, attempt_id: str) -> str:
        return f"stage-{dispatch_id}-{attempt_id}"

    def upload_text(self, parent_id: str, name: str, content: str) -> str:
        self.uploads.append((parent_id, name, content))
        return "bootstrap-upload"

    def find_receipt(self, receipt_folder_id: str, name: str) -> Receipt | None:
        return self.receipts.get(name)


class FakeLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[DispatchJob, str]] = []

    def launch(
        self,
        job: DispatchJob,
        request: Any,
        prompt: str,
        *,
        receipt_folder_id: str | None = None,
    ) -> Any:
        from factory_dispatcher.launchers.base import LaunchResult

        self.calls.append((job, prompt))
        return LaunchResult("FAKE", pid=123)


@pytest.fixture
def local_settings(tmp_path: Path) -> LocalSettings:
    return LocalSettings(
        project_root=tmp_path,
        spreadsheet_id="sheet",
        credentials_file=tmp_path / "credentials.json",
        token_file=tmp_path / "token.json",
        state_directory=tmp_path / "state",
        claim_owner="TEST-WORKER",
        writes_enabled=True,
        manual_chatgpt_launch_enabled=True,
        codex_launch_enabled=False,
        chatgpt_url="https://chatgpt.com/",
        codex_executable="codex",
        google_scopes=("scope",),
    )


@pytest.fixture
def chatgpt_request() -> dict[str, Any]:
    return {
        "schemaVersion": "1",
        "artifactType": "DISPATCH_REQUEST",
        "dispatchId": "D-TEST-0001",
        "agentId": "REGISTRAR",
        "changeId": "",
        "executorType": "CHATGPT",
    }


@pytest.fixture
def attempt_context() -> AttemptContext:
    return AttemptContext.from_mapping(
        {
            "dispatchId": "D-UNIT-0001",
            "attemptId": "D-UNIT-0001-A001",
            "leaseToken": "unit-test-lease",
            "agentId": "CODEX_IMPLEMENTER",
            "requestDriveId": "request-unit",
            "receiptFolderId": "receipts-root",
        }
    )


@pytest.fixture
def structured_result(attempt_context) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "dispatchId": attempt_context.dispatchId,
        "attemptId": attempt_context.attemptId,
        "status": "SUCCEEDED",
        "summary": "Authorized repository work completed.",
        "producedArtifacts": [
            {"path": "result.txt", "description": "Authorized repository change"}
        ],
        "error": {"code": None, "detail": None, "retrySuggested": False},
    }
