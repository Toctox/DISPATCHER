from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import NOW, FakeGateway, queue_row

from factory_dispatcher.audit import MemoryAuditLogger
from factory_dispatcher.codex_result import materialize_receipt
from factory_dispatcher.errors import ContractError
from factory_dispatcher.models import Receipt, isoformat
from factory_dispatcher.receipt_transport import ReceiptTransport, transport_file
from factory_dispatcher.receipts import read_json_object, write_json


class TransportGateway(FakeGateway):
    def __init__(self, context):
        super().__init__(
            [
                queue_row(
                    dispatchId=context.dispatchId,
                    agentId=context.agentId,
                    attempt=1,
                    status="RUNNING",
                    leaseToken=context.leaseToken,
                    requestDriveId=context.requestDriveId,
                    leaseExpiresAt=isoformat(NOW + timedelta(minutes=45)),
                )
            ]
        )
        self.folder_checks = []
        self.receipt_uploads = []

    def validate_receipt_folder(self, folder_id, dispatch_folder_id):
        self.folder_checks.append((folder_id, dispatch_folder_id))

    def upload_receipt(self, folder_id, name, content):
        self.receipt_uploads.append((folder_id, name, content))
        self.receipts[name] = Receipt("uploaded-id", json.loads(content.decode("utf-8-sig")))
        return "uploaded-id"


@pytest.fixture
def transport_case(tmp_path, local_settings, attempt_context, structured_result):
    result_file = tmp_path / "final.json"
    write_json(result_file, structured_result)
    receipt = materialize_receipt(
        attempt_context,
        result_file,
        exit_code=0,
        timed_out=False,
        started_at=isoformat(NOW - timedelta(minutes=2)),
        finished_at=isoformat(NOW),
    )
    file = tmp_path / "execution-receipt.json"
    write_json(file, receipt)
    gateway = TransportGateway(attempt_context)
    logger = MemoryAuditLogger()
    transport = ReceiptTransport(gateway, local_settings, logger, clock=lambda: NOW)
    return SimpleNamespace(
        context=attempt_context,
        file=file,
        receipt=receipt,
        gateway=gateway,
        settings=local_settings,
        logger=logger,
        transport=transport,
    )


def test_transport_uploads_original_bytes_to_pinned_folder_and_records_event(transport_case):
    case = transport_case
    original = case.file.read_bytes()
    result = case.transport.send(case.file, expected=case.context)
    assert result == {"outcome": "UPLOADED", "receiptDriveId": "uploaded-id"}
    assert case.gateway.folder_checks == [("receipts-root", "dispatch-root")]
    assert case.gateway.receipt_uploads == [("receipts-root", case.context.receipt_name, original)]
    assert case.file.read_bytes() == original
    assert case.gateway.updates == []
    assert case.gateway.events[0][4] == "RECEIPT_UPLOADED"
    assert case.context.leaseToken not in json.dumps(case.gateway.events)
    assert case.context.leaseToken not in json.dumps(case.logger.records)


def test_generic_recovery_finds_queue_and_destination_without_launching(transport_case):
    case = transport_case
    assert case.transport.send(case.file)["outcome"] == "UPLOADED"
    assert case.gateway.receipt_uploads[0][1] == case.context.receipt_name
    assert case.gateway.updates == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("dispatchId", "D-OTHER"),
        ("attempt", 2),
        ("leaseToken", "other-lease"),
        ("agentId", "REGISTRAR"),
        ("requestDriveId", "request-other"),
        ("leaseExpiresAt", isoformat(NOW)),
        ("leaseExpiresAt", ""),
        ("status", "FAILED_FINAL"),
        ("status", "RESULT_STAGED"),
        ("status", "CANCELLED"),
    ],
)
def test_transport_rejects_stale_or_incompatible_queue_before_upload(transport_case, key, value):
    case = transport_case
    case.gateway.rows[2][key] = value
    with pytest.raises(ContractError):
        case.transport.send(case.file)
    assert case.gateway.receipt_uploads == []
    assert case.gateway.events == []
    assert case.gateway.updates == []


def test_transport_checks_lease_again_immediately_before_upload(transport_case):
    case = transport_case
    original_read = case.gateway.read_job

    def read_after_concurrent_claim(row_number):
        case.gateway.rows[row_number]["leaseToken"] = "replaced-after-folder-lookup"
        return original_read(row_number)

    case.gateway.read_job = read_after_concurrent_claim
    with pytest.raises(ContractError, match="leaseToken"):
        case.transport.send(case.file)
    assert case.gateway.receipt_uploads == []


def test_transport_rejects_expiry_during_folder_lookup(transport_case):
    case = transport_case
    times = iter([NOW, NOW + timedelta(hours=1)])
    case.transport.clock = lambda: next(times)
    with pytest.raises(ContractError, match="expired"):
        case.transport.send(case.file)
    assert case.gateway.receipt_uploads == []


def test_transport_rejects_destination_changed_since_launch(transport_case):
    case = transport_case
    case.gateway.factory = replace(case.gateway.factory, receipts_folder_id="changed-folder")
    with pytest.raises(ContractError, match="receiptFolderId"):
        case.transport.send(case.file, expected=case.context)
    assert case.gateway.receipt_uploads == []


def test_recovery_validates_destination_pinned_in_local_manifest(transport_case):
    case = transport_case
    folder = (
        case.settings.state_directory
        / "codex-runs"
        / case.context.dispatchId
        / case.context.attemptId
    )
    folder.mkdir(parents=True)
    pinned = case.context.to_dict()
    pinned["receiptFolderId"] = "original-other-folder"
    write_json(folder / "launch.json", {"execution": pinned})
    with pytest.raises(ContractError, match="Local attempt manifest"):
        case.transport.send(case.file)
    assert case.gateway.receipt_uploads == []


def test_transport_rejects_invalid_destination_metadata(transport_case):
    case = transport_case
    case.gateway.validate_receipt_folder = Mock(side_effect=ContractError("Not a receipt folder"))
    with pytest.raises(ContractError, match="receipt folder"):
        case.transport.send(case.file)
    assert case.gateway.receipt_uploads == []


def test_duplicate_upload_is_idempotent_and_does_not_duplicate_acknowledged_event(transport_case):
    case = transport_case
    assert case.transport.send(case.file)["outcome"] == "UPLOADED"
    assert case.transport.send(case.file)["outcome"] == "ALREADY_UPLOADED"
    assert len(case.gateway.receipt_uploads) == 1
    assert len(case.gateway.events) == 1


def test_recovery_can_record_event_after_upload_succeeded_but_event_failed(transport_case):
    case = transport_case
    original_append = case.gateway.append_event
    case.gateway.append_event = Mock(side_effect=OSError("event append failed"))
    with pytest.raises(OSError):
        case.transport.send(case.file)
    assert len(case.gateway.receipt_uploads) == 1
    case.gateway.append_event = original_append
    assert case.transport.send(case.file)["outcome"] == "ALREADY_UPLOADED"
    assert len(case.gateway.receipt_uploads) == 1
    assert len(case.gateway.events) == 1


def test_conflicting_remote_receipt_is_never_overwritten(transport_case):
    case = transport_case
    other = {**case.receipt, "executorStatement": "A different terminal receipt"}
    case.gateway.receipts[case.context.receipt_name] = Receipt("other-id", other)
    with pytest.raises(ContractError, match="different receipt"):
        case.transport.send(case.file)
    assert case.gateway.receipt_uploads == []


def test_recovery_preserves_bom_and_additional_receipt_metadata(transport_case):
    case = transport_case
    case.receipt["dispatchPlan"] = {"dispatches": []}
    content = ("\ufeff" + json.dumps(case.receipt, indent=4)).encode("utf-8")
    case.file.write_bytes(content)
    case.transport.send(case.file)
    assert case.gateway.receipt_uploads[0][2] == content
    assert case.file.read_bytes() == content


def test_recovery_rejects_duplicate_json_keys(transport_case):
    case = transport_case
    original = case.file.read_text(encoding="utf-8")
    case.file.write_text(original.replace("{", '{"status":"BLOCKED",', 1), encoding="utf-8")
    with pytest.raises(ContractError, match="duplicate keys"):
        case.transport.send(case.file)
    assert case.gateway.receipt_uploads == []


def test_transport_fails_closed_when_writes_disabled_before_oauth(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    write_json(config, {"spreadsheetId": "sheet", "writesEnabled": False})
    auth = Mock(side_effect=AssertionError("OAuth must not run"))
    monkeypatch.setattr("factory_dispatcher.receipt_transport.build_google_services", auth)
    with pytest.raises(ContractError, match="writesEnabled"):
        transport_file(tmp_path / "receipt.json", config)
    auth.assert_not_called()


def test_transport_failure_keeps_receipt_bytes_and_pending_sidecar(transport_case):
    case = transport_case
    original = case.file.read_bytes()
    case.gateway.upload_receipt = Mock(side_effect=OSError("Drive unavailable"))
    with pytest.raises(OSError):
        case.transport.send(case.file)
    assert case.file.read_bytes() == original
    sidecar = read_json_object(case.file.with_name(case.file.name + ".transport.json"))
    assert sidecar["receiptFolderId"] == "receipts-root"
    assert "receiptDriveId" not in sidecar


def test_transported_blocked_receipt_is_reconciled_normally_on_next_mock_tick(transport_case):
    from conftest import FakeLauncher

    from factory_dispatcher.engine import Dispatcher
    from factory_dispatcher.models import DispatchState, ExecutorType

    case = transport_case
    case.receipt["status"] = "BLOCKED"
    case.receipt["error"] = {"code": "SOURCE_UNAVAILABLE", "detail": "Missing required source."}
    write_json(case.file, case.receipt)
    case.transport.send(case.file)
    result = Dispatcher(
        case.gateway,
        case.settings,
        case.logger,
        {ExecutorType.CODEX: FakeLauncher()},
        clock=lambda: NOW,
    ).tick()
    assert result["reconciled"] == 1
    assert case.gateway.read_job(2).status == DispatchState.RESULT_STAGED
    assert case.gateway.read_job(2).receipt_drive_id == "uploaded-id"
    assert case.gateway.receipts[case.context.receipt_name].raw["status"] == "BLOCKED"
