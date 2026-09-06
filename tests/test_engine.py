from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import NOW, FakeGateway, FakeLauncher, queue_row

from factory_dispatcher.audit import MemoryAuditLogger
from factory_dispatcher.engine import Dispatcher
from factory_dispatcher.errors import ContractError
from factory_dispatcher.models import (
    DispatchJob,
    DispatchRequest,
    DispatchState,
    ExecutorType,
    Receipt,
    isoformat,
)


def build_dispatcher(
    gateway, settings, launcher, *, token="lease-secret", executor=ExecutorType.CHATGPT
):
    return Dispatcher(
        gateway,
        settings,
        MemoryAuditLogger(),
        {executor: launcher},
        clock=lambda: NOW,
        token_factory=lambda: token,
    )


def test_ready_job_is_claimed_staged_bootstrapped_and_launched(local_settings, chatgpt_request):
    gateway = FakeGateway([queue_row()], {"request-1": chatgpt_request})
    launcher = FakeLauncher()

    result = build_dispatcher(gateway, local_settings, launcher).tick()

    current = gateway.read_job(2)
    assert result["outcome"] == "LAUNCHED"
    assert current.status == DispatchState.RUNNING
    assert current.attempt == 1
    assert current.lease_owner == "TEST-WORKER"
    assert current.staging_folder_id == "stage-D-TEST-0001-D-TEST-0001-A001"
    assert [event[4] for event in gateway.events] == ["CLAIMED", "LAUNCHED"]
    assert len(launcher.calls) == 1
    assert "FACTORY_EXECUTION_V1" in launcher.calls[0][1]
    assert "leaseToken=lease-secret" in launcher.calls[0][1]
    assert len(gateway.uploads) == 1


def test_dry_run_never_mutates_or_launches(local_settings, chatgpt_request):
    gateway = FakeGateway([queue_row()], {"request-1": chatgpt_request})
    launcher = FakeLauncher()

    result = build_dispatcher(gateway, local_settings, launcher).tick(dry_run=True)

    assert result["outcome"] == "DRY_RUN"
    assert gateway.read_job(2).status == DispatchState.READY
    assert gateway.updates == []
    assert gateway.events == []
    assert launcher.calls == []


def test_waiting_human_smoke_is_never_activated(local_settings):
    smoke = queue_row(
        dispatchId="D-SMOKE-0001",
        rootRunId="R-SMOKE-0001",
        status="WAITING_HUMAN",
        attempt=0,
        maxAttempts=1,
        requestDriveId="smoke-request",
    )
    gateway = FakeGateway([smoke])

    result = build_dispatcher(gateway, local_settings, FakeLauncher()).tick()

    assert result["outcome"] == "IDLE"
    assert gateway.read_job(2).status == DispatchState.WAITING_HUMAN
    assert gateway.read_job(2).attempt == 0
    assert gateway.updates == []


def test_valid_receipt_moves_running_job_only_to_result_staged(local_settings):
    running = queue_row(
        status="RUNNING",
        attempt=1,
        leaseOwner="TEST-WORKER",
        leaseToken="correct-token",
        leaseExpiresAt=isoformat(NOW + timedelta(minutes=10)),
    )
    gateway = FakeGateway([running])
    name = "EXECUTION_RECEIPT__D-TEST-0001__D-TEST-0001-A001.json"
    gateway.receipts[name] = Receipt(
        drive_id="receipt-1",
        raw={
            "artifactType": "EXECUTION_RECEIPT",
            "dispatchId": "D-TEST-0001",
            "attemptId": "D-TEST-0001-A001",
            "leaseToken": "correct-token",
            "agentId": "REGISTRAR",
            "status": "SUCCEEDED",
            "requestDriveId": "request-1",
            "producedArtifacts": [{"driveId": "artifact-1"}],
            "finishedAt": isoformat(NOW),
        },
    )

    result = build_dispatcher(gateway, local_settings, FakeLauncher()).tick()

    current = gateway.read_job(2)
    assert result["reconciled"] == 1
    assert current.status == DispatchState.RESULT_STAGED
    assert current.receipt_drive_id == "receipt-1"
    assert [event[4] for event in gateway.events] == ["RECEIPT_FOUND"]


def test_wrong_receipt_token_is_ignored_without_queue_mutation(local_settings):
    running = queue_row(
        status="RUNNING",
        attempt=1,
        leaseOwner="TEST-WORKER",
        leaseToken="correct-token",
        leaseExpiresAt=isoformat(NOW + timedelta(minutes=10)),
    )
    gateway = FakeGateway([running])
    name = "EXECUTION_RECEIPT__D-TEST-0001__D-TEST-0001-A001.json"
    gateway.receipts[name] = Receipt(
        drive_id="receipt-1",
        raw={
            "artifactType": "EXECUTION_RECEIPT",
            "dispatchId": "D-TEST-0001",
            "attemptId": "D-TEST-0001-A001",
            "leaseToken": "wrong-token",
            "agentId": "REGISTRAR",
            "status": "SUCCEEDED",
            "requestDriveId": "request-1",
        },
    )

    build_dispatcher(gateway, local_settings, FakeLauncher()).tick()

    assert gateway.read_job(2).status == DispatchState.RUNNING
    assert gateway.updates == []


def test_expired_safe_retry_schedules_backoff(local_settings):
    running = queue_row(
        status="RUNNING",
        attempt=1,
        leaseOwner="TEST-WORKER",
        leaseToken="old-token",
        leaseExpiresAt=isoformat(NOW - timedelta(seconds=1)),
        retryPolicy="SAFE_RETRY",
    )
    gateway = FakeGateway([running])

    result = build_dispatcher(gateway, local_settings, FakeLauncher()).tick()

    current = gateway.read_job(2)
    assert result["expired"] == 1
    assert current.status == DispatchState.FAILED_RETRYABLE
    assert current.not_before == NOW + timedelta(minutes=5)
    assert [event[4] for event in gateway.events] == ["LEASE_EXPIRED", "RETRY_SCHEDULED"]


def test_duplicate_ready_job_is_superseded(local_settings, chatgpt_request):
    existing = queue_row(
        dispatchId="D-OLD",
        status="SUCCEEDED",
        attempt=1,
        requestDriveId="request-old",
    )
    candidate = queue_row(dispatchId="D-NEW", requestDriveId="request-new")
    gateway = FakeGateway([existing, candidate])

    result = build_dispatcher(gateway, local_settings, FakeLauncher()).tick()

    assert result["outcome"] == "IDLE"
    assert gateway.read_job(3).status == DispatchState.SUPERSEDED
    assert gateway.rows[3]["lastErrorCode"] == "DUPLICATE_DEDUPE_KEY"
    assert [event[4] for event in gateway.events] == ["DUPLICATE_REJECTED"]


def test_root_run_cap_parks_candidate(local_settings):
    consumed = queue_row(
        dispatchId="D-OLD",
        status="SUCCEEDED",
        attempt=25,
        dedupeKey="OLD-KEY",
        requestDriveId="request-old",
    )
    candidate = queue_row(dispatchId="D-NEW", dedupeKey="NEW-KEY")
    gateway = FakeGateway([consumed, candidate])

    build_dispatcher(gateway, local_settings, FakeLauncher()).tick()

    assert gateway.read_job(3).status == DispatchState.WAITING_HUMAN
    assert gateway.rows[3]["lastErrorCode"] == "ROOT_RUN_EXECUTION_CAP"


def test_ready_job_at_max_attempts_becomes_failed_final(local_settings):
    gateway = FakeGateway([queue_row(attempt=3, maxAttempts=3)])

    build_dispatcher(gateway, local_settings, FakeLauncher()).tick()

    assert gateway.read_job(2).status == DispatchState.FAILED_FINAL
    assert gateway.rows[2]["lastErrorCode"] == "MAX_ATTEMPTS"


def test_receipt_for_stale_attempt_is_rejected(local_settings):
    running = queue_row(
        status="RUNNING",
        attempt=2,
        leaseOwner="TEST-WORKER",
        leaseToken="current-token",
        leaseExpiresAt=isoformat(NOW + timedelta(minutes=10)),
    )
    gateway = FakeGateway([running])
    current_name = "EXECUTION_RECEIPT__D-TEST-0001__D-TEST-0001-A002.json"
    gateway.receipts[current_name] = Receipt(
        drive_id="stale-receipt",
        raw={
            "artifactType": "EXECUTION_RECEIPT",
            "dispatchId": "D-TEST-0001",
            "attemptId": "D-TEST-0001-A001",
            "leaseToken": "current-token",
            "agentId": "REGISTRAR",
            "status": "SUCCEEDED",
            "requestDriveId": "request-1",
        },
    )

    build_dispatcher(gateway, local_settings, FakeLauncher()).tick()

    assert gateway.read_job(2).status == DispatchState.RUNNING
    assert gateway.updates == []


def test_executor_type_routes_to_codex_launcher(local_settings):
    row = queue_row(
        agentId="CODEX_IMPLEMENTER",
        changeId="CR-0004",
        requestDriveId="codex-request",
    )
    request = {
        "schemaVersion": "2",
        "artifactType": "DISPATCH_REQUEST",
        "dispatchId": "D-TEST-0001",
        "agentId": "CODEX_IMPLEMENTER",
        "changeId": "CR-0004",
        "executorType": "CODEX",
        "maxExecutionMinutes": 45,
        "allowedWrites": {"stagingOnly": False},
        "codexExecution": {
            "implementationEligible": True,
            "repository": "Toctox/example",
            "repositoryPath": str(local_settings.project_root),
            "baseBranch": "main",
            "baseSha": "a" * 40,
            "specArtifact": "spec-drive-id",
            "specIdentity": "SPEC-001-v2",
            "causalDecision": "DECISION-001",
        },
    }
    gateway = FakeGateway([row], {"codex-request": request})
    launcher = FakeLauncher()

    result = build_dispatcher(gateway, local_settings, launcher, executor=ExecutorType.CODEX).tick()

    assert result["executorType"] == "CODEX"
    assert result["outcome"] == "LAUNCHED"
    assert len(launcher.calls) == 1


@pytest.mark.parametrize(
    ("request_id", "queue_id", "matches"),
    [
        (None, None, True),
        (None, "", True),
        ("", None, True),
        ("CR-0004", "CR-0004", True),
        (None, "CR-0004", False),
        ("CR-0004", None, False),
        ("CR-0004", "", False),
        ("CR-OTHER", "CR-0004", False),
    ],
)
def test_request_identity_compares_change_ids_before_claim(
    local_settings, request_id, queue_id, matches
):
    gateway = FakeGateway([])
    dispatcher = build_dispatcher(gateway, local_settings, FakeLauncher())
    job = DispatchJob.from_row(2, queue_row(changeId=queue_id))
    request = DispatchRequest(
        {
            "schemaVersion": "2",
            "dispatchId": job.dispatch_id,
            "agentId": job.agent_id,
            "changeId": request_id,
        }
    )

    if matches:
        dispatcher._validate_request_identity(job, request)
    else:
        with pytest.raises(ContractError, match="changeId"):
            dispatcher._validate_request_identity(job, request)
    assert gateway.updates == []
