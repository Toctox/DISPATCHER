from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import queue_row
from test_chatgpt_extension import BINDING
from test_chatgpt_extension import extension_case as extension_case
from test_chatgpt_extension import extension_settings as extension_settings

from factory_dispatcher import chatgpt_extension_recover as cli
from factory_dispatcher import chatgpt_extension_recovery as recovery
from factory_dispatcher.chatgpt_browser import ChatGPTError
from factory_dispatcher.chatgpt_extension_bridge import Bridge, quiet_logger
from factory_dispatcher.chatgpt_extension_client import BridgeClient
from factory_dispatcher.chatgpt_extension_pair import create_pairing
from factory_dispatcher.chatgpt_extension_protocol import (
    CAPABILITY,
    RECOVER,
    RECOVERY_RELEASE,
    command,
    durable_write,
    proof,
    result,
    tab_lock,
)
from factory_dispatcher.errors import ContractError
from factory_dispatcher.google_gateway import QUEUE_HEADERS, GoogleWorkspaceGateway
from factory_dispatcher.mutex import LocalMutex
from factory_dispatcher.receipts import MAX_JSON_BYTES, AttemptContext, read_json_object

AUDIT_KEYS = {
    "dispatchId",
    "attemptId",
    "previousReservationPresent",
    "evidenceSendAttemptedFalse",
    "outcome",
}


@pytest.mark.parametrize("suffix", [".log", ".jsonl", ".json", ".tmp"])
def test_irrelevant_non_json_is_never_parsed(local_settings, attempt_context, monkeypatch, suffix):
    local_settings.state_directory.mkdir()
    # Even an exact ID in the filename must not associate unrelated CONTENT.
    path = local_settings.state_directory / f"{attempt_context.dispatchId}{suffix}"
    path.write_bytes(b"unrelated diagnostic banner\nnot JSON\ninvalid UTF-8: \xff\n")
    decoder = Mock(side_effect=AssertionError("irrelevant content must not be parsed"))
    monkeypatch.setattr(recovery, "decode_json_object", decoder)
    recovery.local_state_evidence(local_settings, attempt_context)
    decoder.assert_not_called()


@pytest.mark.parametrize("suffix", [".log", ".jsonl"])
@pytest.mark.parametrize("identity_field", ["dispatchId", "attemptId"])
def test_mixed_log_parses_only_associated_lines(
    local_settings, attempt_context, monkeypatch, suffix, identity_field
):
    local_settings.state_directory.mkdir()
    line = json.dumps(
        {
            "details": [
                {identity_field: getattr(attempt_context, identity_field), "state": "PREFLIGHT"}
            ]
        }
    ).encode()
    path = local_settings.state_directory / f"diagnostic{suffix}"
    path.write_bytes(b"non-JSON banner\r\n\xff unrelated\n\n" + line + b"\r\nnon-JSON footer\n")
    decoder = Mock(wraps=recovery.decode_json_object)
    monkeypatch.setattr(recovery, "decode_json_object", decoder)
    recovery.local_state_evidence(local_settings, attempt_context)
    decoder.assert_called_once_with(line)


@pytest.mark.parametrize("suffix", [".log", ".jsonl", ".json", ".tmp"])
@pytest.mark.parametrize("identity_field", ["dispatchId", "attemptId"])
def test_associated_invalid_json_remains_refused(recovery_case, suffix, identity_field):
    case = recovery_case
    context = AttemptContext.from_mapping(
        read_json_object(case.directory / "launch.json")["execution"]
    )
    identity = getattr(context, identity_field)
    path = case.settings.state_directory / f"diagnostic{suffix}"
    path.write_bytes(f'{{"{identity_field}": "{identity}", invalid JSON'.encode())
    # The local scan propagates ContractError; the public recovery still returns REFUSED.
    with pytest.raises(ContractError, match="Expected one strict UTF-8 JSON object"):
        recovery.local_state_evidence(case.settings, context)
    assert_refused(case, "REFUSED_UNVERIFIABLE_EVIDENCE")


@pytest.mark.parametrize("suffix", [".log", ".jsonl", ".json", ".tmp"])
@pytest.mark.parametrize(
    "fields,outcome",
    [
        ({"state": "SEND_ATTEMPTED"}, "REFUSED_SEND_EVIDENCE"),
        ({"artifactType": "EXECUTION_RECEIPT"}, "REFUSED_RECEIPT_PRESENT"),
    ],
)
def test_prefilter_does_not_discard_associated_send_or_receipt(
    recovery_case, suffix, fields, outcome
):
    case = recovery_case
    raw = json.dumps(
        {"details": {"attemptId": case.job.attempt_id, **fields}},
        indent=2 if suffix in {".json", ".tmp"} else None,
    ).encode()
    if suffix in {".log", ".jsonl"}:
        raw = b"unrelated banner\n" + raw + b"\nunrelated footer\n"
    (case.settings.state_directory / f"diagnostic{suffix}").write_bytes(raw)
    assert_refused(case, outcome)


@pytest.mark.parametrize("similarity", ["case", "separator", "typo", "shortened"])
def test_similar_but_not_exact_ids_are_ignored(
    local_settings, attempt_context, monkeypatch, similarity
):
    dispatch_id, attempt_id = attempt_context.dispatchId, attempt_context.attemptId
    similar = {
        "case": f"{dispatch_id.lower()} {attempt_id.lower()}",
        "separator": f"{dispatch_id.replace('-', '_')} {attempt_id.replace('-', '_')}",
        "typo": f"{dispatch_id.replace('UNIT', 'UN1T')} {attempt_id.replace('UNIT', 'UN1T')}",
        "shortened": dispatch_id[:-1],
    }[similarity].encode()
    assert dispatch_id.encode() not in similar and attempt_id.encode() not in similar
    local_settings.state_directory.mkdir()
    (local_settings.state_directory / "similar.log").write_bytes(b"non-JSON " + similar)
    decoder = Mock(side_effect=AssertionError("no fuzzy association"))
    monkeypatch.setattr(recovery, "decode_json_object", decoder)
    recovery.local_state_evidence(local_settings, attempt_context)
    decoder.assert_not_called()


def test_receipt_filename_still_refuses_without_identity_in_content(
    local_settings, attempt_context, monkeypatch
):
    local_settings.state_directory.mkdir()
    (local_settings.state_directory / attempt_context.receipt_name).write_bytes(
        b"unrelated non-JSON"
    )
    decoder = Mock(side_effect=AssertionError("receipt name must block before parsing"))
    monkeypatch.setattr(recovery, "decode_json_object", decoder)
    with pytest.raises(recovery.RecoveryRefused, match="REFUSED_RECEIPT_PRESENT"):
        recovery.local_state_evidence(local_settings, attempt_context)
    decoder.assert_not_called()


def test_prefilter_cannot_bypass_bounded_bytes(local_settings, attempt_context):
    local_settings.state_directory.mkdir()
    (local_settings.state_directory / "unrelated.json").write_bytes(b"x" * (MAX_JSON_BYTES + 1))
    with pytest.raises(recovery.RecoveryRefused, match="REFUSED_EVIDENCE_SIZE"):
        recovery.local_state_evidence(local_settings, attempt_context)


def test_prefilter_cannot_bypass_anti_link_check(local_settings, attempt_context, monkeypatch):
    local_settings.state_directory.mkdir()
    path = local_settings.state_directory / "unrelated.log"
    path.write_bytes(b"non-JSON banner")
    original_linked = recovery.linked
    monkeypatch.setattr(
        recovery, "linked", lambda candidate: candidate == path or original_linked(candidate)
    )
    with pytest.raises(recovery.RecoveryRefused, match="REFUSED_PATH"):
        recovery.local_state_evidence(local_settings, attempt_context)


def test_real_pre_send_case_local_verify_with_unrelated_noise(extension_settings, monkeypatch):
    # Reproduce the supplied real-case identity/state in temporary files, never real storage.
    settings = extension_settings
    dispatch_id, attempt_id = "D-SMOKE-CHATGPT-0001", "D-SMOKE-CHATGPT-0001-A001"
    directory = settings.state_directory / "chatgpt-runs" / dispatch_id / attempt_id
    directory.mkdir(parents=True)
    context = AttemptContext.from_mapping(
        {
            "dispatchId": dispatch_id,
            "attemptId": attempt_id,
            "leaseToken": "fixture-lease",
            "agentId": "REGISTRAR",
            "requestDriveId": "fixture-request",
            "receiptFolderId": "fixture-receipts",
        }
    )
    reservation = {
        "reservationId": "fixture-reservation",
        "binding": BINDING,
        "phase": "NEW_CHAT_ATTEMPTED",
    }
    bootstrap = b"Fixture bootstrap, no dispatch execution."
    (directory / "bootstrap.txt").write_bytes(bootstrap)
    (directory / "worker.lock").touch()
    durable_write(
        directory / "launch.json",
        {
            "manifestVersion": 3,
            "executorType": "CHATGPT",
            "browserMode": "EXTENSION_BRIDGE",
            "execution": context.to_dict(),
            "extensionBinding": BINDING,
            "reservationId": reservation["reservationId"],
            "bootstrapHash": hashlib.sha256(bootstrap).hexdigest(),
        },
    )
    durable_write(
        directory / "status.json",
        {
            "dispatchId": dispatch_id,
            "attemptId": attempt_id,
            "requestDriveId": context.requestDriveId,
            "state": "STOPPED",
            "sendAttempted": False,
            "operationalSuccess": False,
            "errorCode": "CHATGPT_NEW_CHAT_FAILED",
            "needsReconciliation": True,
        },
    )
    (directory / "browser.log").write_text(
        "\n".join(
            json.dumps(
                {
                    "state": state,
                    "errorCode": "CHATGPT_NEW_CHAT_FAILED" if state == "STOPPED" else "",
                }
            )
            for state in ["STARTING", "PREFLIGHT", "PREPARING_TAB", "STOPPED"]
        )
        + "\n",
        encoding="utf-8",
    )
    for suffix in [".log", ".jsonl", ".json", ".tmp"]:
        (settings.state_directory / f"unrelated{suffix}").write_bytes(b"not JSON\n\xff unrelated\n")
    remote = Mock(side_effect=AssertionError("local verify must not contact Google"))
    monkeypatch.setattr(recovery, "verify_remote", remote)
    before = {
        path: path.read_bytes() for path in settings.state_directory.rglob("*") if path.is_file()
    }
    previous = copy.deepcopy(reservation)
    with ExitStack() as stack:
        evidence = recovery.LocalEvidence(settings, dispatch_id, attempt_id, stack)
        observed, _, _ = evidence.verify(reservation)
    assert observed == context
    assert reservation == previous
    assert {path: path.read_bytes() for path in before} == before
    remote.assert_not_called()


@pytest.fixture
def recovery_case(extension_case, monkeypatch):
    case = extension_case
    case.client.errors["NEW_CHAT"] = "CHATGPT_NEW_CHAT_FAILED"
    assert case.run() == 1
    case.gateway.rows[2].update(
        status="WAITING_HUMAN", lastErrorCode="CHATGPT_RECONCILIATION_REQUIRED"
    )
    create_pairing(case.settings)
    manifest = read_json_object(case.directory / "launch.json")
    reservation = {
        "reservationId": manifest["reservationId"],
        "binding": manifest["extensionBinding"],
        "phase": "NEW_CHAT_ATTEMPTED",
    }
    bridge = Bridge(case.settings)
    bridge.reservation.active = copy.deepcopy(reservation)
    bridge.reservation.save()
    storage = {
        "activeReservation": copy.deepcopy(reservation),
        "pairing": {"extensionSecret": "fake-private-secret"},
        "factoryTab": {"factoryTabId": 99, "browserInstanceId": "different-session"},
    }
    calls = []

    async def send(raw):
        request = command(json.loads(raw))
        calls.append(request)
        assert request["control"] == RECOVERY_RELEASE
        expected = {
            "reservationId": request["reservationId"],
            "binding": request["binding"],
            "phase": "NEW_CHAT_ATTEMPTED",
        }
        response = {
            "protocolVersion": 1,
            "type": "RESULT",
            "requestId": request["requestId"],
            "status": "OK",
        }
        if storage.get("activeReservation") != expected:
            response.update(status="ERROR", errorCode="CHATGPT_RESERVATION_INVALID")
        else:
            del storage["activeReservation"]
        bridge.pending[1].set_result(result(response, request))

    bridge.extension = SimpleNamespace(send=send)
    case.gateway.read_queue_for_recovery = Mock(side_effect=case.gateway.read_queue)
    case.gateway.validate_receipt_folder = Mock()
    monkeypatch.setattr(recovery, "readonly_gateway", lambda settings: case.gateway)
    case.bridge, case.storage, case.recovery_calls = bridge, storage, calls
    case.recovery_request = {
        "protocolVersion": 1,
        "type": "CONTROL",
        "control": RECOVER,
        "requestId": "recover-1",
        "dispatchId": case.job.dispatch_id,
        "attemptId": case.job.attempt_id,
    }
    case.recover = lambda: asyncio.run(bridge.relay(case.recovery_request))
    case.audits = lambda: [
        read_json_object(path)
        for path in sorted(
            (case.settings.project_root / "state" / "chatgpt-bridge" / "recovery-audit").glob(
                "*.json"
            )
        )
    ]
    return case


def snapshot(case):
    return {
        "files": {path.name: path.read_bytes() for path in case.directory.iterdir()},
        "pairing": case.settings.chatgpt_pairing_file.read_bytes(),
        "reservation": case.bridge.reservation.path.read_bytes(),
        "storage": copy.deepcopy(case.storage),
        "rows": copy.deepcopy(case.gateway.rows),
    }


def assert_refused(case, expected="REFUSED", *, exchange=False):
    before = snapshot(case)
    with pytest.raises(ChatGPTError, match="RECOVERY_REFUSED"):
        case.recover()
    assert snapshot(case) == before
    assert bool(case.recovery_calls) is exchange
    assert case.gateway.updates == case.gateway.events == case.gateway.uploads == []
    assert case.audits()[-1]["outcome"].startswith(expected)
    assert set(case.audits()[-1]) == AUDIT_KEYS


def edit_status(case, **changes):
    durable_write(case.directory / "status.json", {**case.status(), **changes})


def test_pre_send_success_preserves_everything_except_reservation(recovery_case):
    case = recovery_case
    before = snapshot(case)
    assert case.recover()["status"] == "OK"
    after = snapshot(case)
    assert after["files"] == before["files"]
    assert after["pairing"] == before["pairing"]
    assert after["rows"] == before["rows"]
    assert after["storage"] == {
        k: v for k, v in before["storage"].items() if k != "activeReservation"
    }
    assert case.bridge.reservation.active == read_json_object(case.bridge.reservation.path) == {}
    assert len(case.recovery_calls) == 1
    assert case.gateway.updates == case.gateway.events == case.gateway.uploads == []
    case.gateway.read_queue_for_recovery.assert_called_once_with()
    assert case.audits() == [
        {
            "dispatchId": case.job.dispatch_id,
            "attemptId": case.job.attempt_id,
            "previousReservationPresent": True,
            "evidenceSendAttemptedFalse": True,
            "outcome": "RELEASED_PRE_SEND",
        }
    ]
    assert case.status()["state"] == "STOPPED"
    assert case.status()["needsReconciliation"] is True


@pytest.mark.parametrize("value", [True, 0, 1, "false", None, [], {}])
def test_refuses_non_false_send_attempted(recovery_case, value):
    edit_status(recovery_case, sendAttempted=value)
    assert_refused(recovery_case, "REFUSED_SEND_EVIDENCE")


@pytest.mark.parametrize(
    "field,value",
    [
        ("attemptId", "D-TEST-0001-A002"),
        ("dispatchId", "D-OTHER"),
        ("requestDriveId", "request-other"),
        ("state", "PREPARED"),
        ("operationalSuccess", True),
        ("operationalSuccess", 0),
        ("errorCode", "CHATGPT_SEND_UNCERTAIN"),
        ("errorCode", "CHATGPT_BRIDGE_UNAVAILABLE"),
        ("errorCode", "UNKNOWN_PRE_SEND"),
        ("needsReconciliation", False),
        ("sendAttemptedAt", "2026-09-06T01:00:00Z"),
        ("receiptDriveId", "receipt-1"),
    ],
)
def test_refuses_contradictory_status(recovery_case, field, value):
    edit_status(recovery_case, **{field: value})
    assert_refused(recovery_case)


def test_refuses_missing_send_field(recovery_case):
    status = recovery_case.status()
    del status["sendAttempted"]
    durable_write(recovery_case.directory / "status.json", status)
    assert_refused(recovery_case)


def test_refuses_missing_status(recovery_case):
    (recovery_case.directory / "status.json").unlink()
    assert_refused(recovery_case, "REFUSED_STATUS_MISSING")


def test_refuses_exact_but_different_requested_attempt(recovery_case):
    recovery_case.recovery_request["attemptId"] = "D-TEST-0001-A002"
    assert_refused(recovery_case, "REFUSED_STATUS_MISSING")


def test_refuses_existing_valid_remote_receipt(recovery_case):
    recovery_case.publish()
    assert_refused(recovery_case, "REFUSED_RECEIPT_PRESENT")


def test_refuses_invalid_remote_receipt_too(recovery_case):
    recovery_case.publish(status="UNKNOWN", leaseToken="divergent")
    assert_refused(recovery_case, "REFUSED_RECEIPT_PRESENT")


def test_refuses_local_receipt_outside_attempt(recovery_case):
    case = recovery_case
    receipt = (
        case.settings.state_directory
        / f"EXECUTION_RECEIPT__{case.job.dispatch_id}__{case.job.attempt_id}.json"
    )
    durable_write(receipt, {"artifactType": "EXECUTION_RECEIPT"})
    assert_refused(case, "REFUSED_RECEIPT_PRESENT")
    assert receipt.exists()


def test_refuses_local_receipt_with_noncanonical_filename(recovery_case):
    case = recovery_case
    case.publish()
    receipt = next(iter(case.gateway.receipts.values())).raw
    case.gateway.receipts.clear()
    durable_write(case.settings.state_directory / "operator-output.json", receipt)
    assert_refused(case, "REFUSED_RECEIPT_PRESENT")


@pytest.mark.parametrize("name", ["status.json", "launch.json", "browser.log"])
def test_refuses_malformed_evidence(recovery_case, name):
    (recovery_case.directory / name).write_text('{"state": "STOPPED",', encoding="utf-8")
    assert_refused(recovery_case)


def test_refuses_duplicate_json_keys(recovery_case):
    path = recovery_case.directory / "status.json"
    raw = path.read_text(encoding="utf-8")
    raw = raw.replace('"sendAttempted": false', '"sendAttempted": true, "sendAttempted": false')
    path.write_text(raw, encoding="utf-8")
    assert_refused(recovery_case)


def test_completed_other_local_attempt_is_inert_but_still_locked(recovery_case):
    case = recovery_case
    other = case.directory.parent / "D-TEST-0001-A002"
    other.mkdir()
    durable_write(
        other / "status.json",
        {
            "state": "STOPPED",
            "dispatchId": case.job.dispatch_id,
            "attemptId": other.name,
            "sendAttempted": False,
        },
    )
    durable_write(other / "launch.json", {"reservationId": "another-reservation"})
    with LocalMutex(other / "worker.lock"):
        with pytest.raises(ChatGPTError, match="RECOVERY_REFUSED"):
            case.recover()
    assert not case.recovery_calls
    assert case.recover()["status"] == "OK"


@pytest.mark.parametrize("name", ["receipt.json", "receipt-upload.json", "unexpected.log"])
def test_refuses_unaccounted_attempt_evidence(recovery_case, name):
    durable_write(recovery_case.directory / name, {"state": "SEND_ATTEMPTED"})
    assert_refused(recovery_case)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reservationId", "another-reservation"),
        ("binding", {**BINDING, "tabId": 8}),
        ("phase", "SEND_ATTEMPTED"),
        ("phase", "NEW_CHAT"),
        ("phase", "INSERT_BOOTSTRAP_ATTEMPTED"),
        ("unknownField", True),
    ],
)
def test_refuses_divergent_bridge_reservation(recovery_case, field, value):
    recovery_case.bridge.reservation.active[field] = value
    recovery_case.bridge.reservation.save()
    assert_refused(recovery_case, "REFUSED_RESERVATION_MISMATCH")


def test_refuses_bridge_disk_memory_divergence(recovery_case):
    durable_write(recovery_case.bridge.reservation.path, {})
    assert_refused(recovery_case, "REFUSED_RESERVATION_MISMATCH")


@pytest.mark.parametrize(
    "field,value",
    [
        ("reservationId", "another-reservation"),
        ("binding", {**BINDING, "documentId": "other"}),
        ("phase", "SEND_ATTEMPTED"),
    ],
)
def test_refuses_divergent_extension_reservation(recovery_case, field, value):
    recovery_case.storage["activeReservation"][field] = value
    assert_refused(recovery_case, "REFUSED_EXTENSION_RESERVATION", exchange=True)


def test_refuses_no_extension_reservation(recovery_case):
    del recovery_case.storage["activeReservation"]
    assert_refused(recovery_case, "REFUSED_EXTENSION_RESERVATION", exchange=True)


@pytest.mark.parametrize(
    "state",
    ["PREPARED", "STARTING", "PREPARING_TAB", "SEND_ATTEMPTED", "WAITING_RECEIPT", "UNKNOWN"],
)
def test_refuses_another_local_active_attempt(recovery_case, state):
    case = recovery_case
    other = case.directory.parent / "D-TEST-0001-A002"
    other.mkdir()
    durable_write(
        other / "status.json",
        {"state": state, "dispatchId": case.job.dispatch_id, "attemptId": other.name},
    )
    durable_write(other / "launch.json", {"reservationId": "another-reservation"})
    assert_refused(case, "REFUSED_OTHER_ATTEMPT")


def test_refuses_another_remote_active_chatgpt_even_expired(recovery_case):
    case = recovery_case
    case.gateway.rows[1002] = queue_row(
        dispatchId="D-OTHER", status="RUNNING", attempt=1, requestDriveId="request-other"
    )
    case.gateway.requests["request-other"] = {"dispatchId": "D-OTHER", "executorType": "CHATGPT"}
    assert_refused(case, "REFUSED_OTHER_ATTEMPT")


def test_unrelated_active_codex_is_not_recovered_or_modified(recovery_case):
    case = recovery_case
    case.gateway.rows[3] = queue_row(
        dispatchId="D-CODEX", status="RUNNING", attempt=1, requestDriveId="request-codex"
    )
    case.gateway.requests["request-codex"] = {"dispatchId": "D-CODEX", "executorType": "CODEX"}
    before = copy.deepcopy(case.gateway.rows)
    assert case.recover()["status"] == "OK"
    assert case.gateway.rows == before


@pytest.mark.parametrize(
    "field,value",
    [("attempt", 2), ("leaseToken", "other"), ("status", "READY"), ("receiptDriveId", "existing")],
)
def test_refuses_divergent_canonical_queue(recovery_case, field, value):
    recovery_case.gateway.rows[2][field] = value
    assert_refused(recovery_case)


@pytest.mark.parametrize(
    "method",
    [
        "read_factory_config",
        "read_queue_for_recovery",
        "read_json_artifact",
        "find_receipt",
        "validate_receipt_folder",
    ],
)
def test_remote_read_failure_never_releases(recovery_case, method):
    setattr(recovery_case.gateway, method, Mock(side_effect=RuntimeError("secret-not-to-log")))
    assert_refused(recovery_case, "REFUSED_UNVERIFIABLE_EVIDENCE")
    assert "secret-not-to-log" not in str(recovery_case.audits())


@pytest.mark.parametrize("lock_kind", ["worker", "tab", "dispatcher"])
def test_refuses_busy_locks(recovery_case, lock_kind):
    case = recovery_case
    lock = {
        "worker": LocalMutex(case.directory / "worker.lock"),
        "tab": tab_lock(case.settings),
        "dispatcher": LocalMutex(case.settings.mutex_file),
    }[lock_kind]
    before = snapshot(case)
    with lock:
        with pytest.raises(ChatGPTError, match="RECOVERY_REFUSED"):
            case.recover()
    assert snapshot(case) == before
    assert not case.recovery_calls
    assert case.audits()[-1]["outcome"] == "REFUSED_ACTIVE_LOCK"


def test_refuses_send_history_even_when_status_false(recovery_case):
    case = recovery_case
    path = case.directory / "browser.log"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"state": "SEND_ATTEMPTED"}) + "\n")
    assert_refused(case, "REFUSED_LOG_HISTORY")


def test_refuses_send_in_dispatcher_evidence(recovery_case):
    case = recovery_case
    case.settings.log_directory.mkdir()
    durable_write(
        case.settings.log_directory / "dispatch.json",
        {"attemptId": case.job.attempt_id, "state": "SEND_ATTEMPTED"},
    )
    assert_refused(case, "REFUSED_SEND_EVIDENCE")


def test_refuses_changed_evidence_during_remote_reads(recovery_case):
    case = recovery_case
    case.gateway.find_receipt = lambda *args: edit_status(case, sendAttempted=True)
    with pytest.raises(ChatGPTError, match="RECOVERY_REFUSED"):
        case.recover()
    assert not case.recovery_calls
    assert case.bridge.reservation.active
    assert case.storage["activeReservation"]


def test_failure_to_persist_write_ahead_audit_never_releases(recovery_case, monkeypatch):
    case = recovery_case
    monkeypatch.setattr(recovery.RecoveryAudit, "record", Mock(side_effect=OSError("disk full")))
    before = snapshot(case)
    with pytest.raises(ChatGPTError, match="RECOVERY_REFUSED"):
        case.recover()
    assert snapshot(case) == before
    assert not case.recovery_calls


def test_lost_ack_preserves_bridge_barrier_and_never_retries(recovery_case, monkeypatch):
    case = recovery_case
    calls = []

    async def lost_ack(request):
        calls.append(request)
        del case.storage["activeReservation"]
        raise ChatGPTError("CHATGPT_BRIDGE_UNAVAILABLE")

    monkeypatch.setattr(case.bridge, "exchange", lost_ack)
    previous = case.bridge.reservation.path.read_bytes()
    with pytest.raises(ChatGPTError, match="RECOVERY_UNCERTAIN"):
        case.recover()
    assert len(calls) == 1
    assert case.bridge.reservation.path.read_bytes() == previous
    assert case.bridge.reservation.active
    assert case.audits()[-1]["outcome"] == "RECOVERY_UNCERTAIN"


def test_direct_worker_release_control_cannot_bypass_evidence(recovery_case):
    case = recovery_case
    release = {
        "protocolVersion": 1,
        "type": "CONTROL",
        "control": RECOVERY_RELEASE,
        "requestId": "bypass",
        "binding": BINDING,
        "reservationId": case.bridge.reservation.active["reservationId"],
    }
    with pytest.raises(ChatGPTError, match="RECOVERY_REFUSED"):
        asyncio.run(case.bridge.relay(release))
    assert case.bridge.reservation.active
    assert not case.recovery_calls


@pytest.mark.parametrize(
    "extra", [{"force": True}, {"resetAll": True}, {"sendAttempted": False}, {"binding": BINDING}]
)
def test_recovery_protocol_rejects_caller_assertions(recovery_case, extra):
    with pytest.raises(ChatGPTError):
        command({**recovery_case.recovery_request, **extra})


def test_recovery_queue_read_is_unbounded_and_read_only(extension_settings):
    sheets, drive = Mock(), Mock()
    sheets.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": [list(QUEUE_HEADERS)]
    }
    gateway = GoogleWorkspaceGateway(
        SimpleNamespace(sheets=sheets, drive=drive), extension_settings
    )
    assert gateway.read_queue_for_recovery() == []
    sheets.spreadsheets.return_value.values.return_value.get.assert_called_once_with(
        spreadsheetId="sheet", range="QUEUE!A:AN"
    )
    sheets.spreadsheets.return_value.values.return_value.batchUpdate.assert_not_called()
    assert not drive.mock_calls


@pytest.mark.parametrize("flag", ["--force", "--reset-all", "--dispatch", "--timeout"])
def test_cli_no_force_reset_timeout_or_abbreviation(flag):
    with pytest.raises(SystemExit) as caught:
        cli.main(
            [
                "--config",
                "fake.json",
                "--dispatch-id",
                "D-TEST",
                "--attempt-id",
                "D-TEST-A001",
                flag,
            ]
        )
    assert caught.value.code == 2


def test_cli_delegates_once_and_never_launches_browser(recovery_case, monkeypatch, capsys):
    case = recovery_case
    client = Mock()
    client.call.side_effect = lambda action, **fields: case.recover()
    monkeypatch.setattr(cli, "load_local_settings", lambda path: case.settings)
    monkeypatch.setattr(cli, "BridgeClient", lambda settings: client)
    assert (
        cli.main(
            [
                "--config",
                "fake.json",
                "--dispatch-id",
                case.job.dispatch_id,
                "--attempt-id",
                case.job.attempt_id,
            ]
        )
        == 0
    )
    client.call.assert_called_once_with(
        RECOVER, timeout=None, dispatchId=case.job.dispatch_id, attemptId=case.job.attempt_id
    )
    assert json.loads(capsys.readouterr().out)["outcome"] == "RELEASED_PRE_SEND"


def test_authenticated_loopback_recovery_with_fake_extension_and_fake_google(recovery_case):
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve

    case = recovery_case

    async def scenario():
        bridge = case.bridge
        bridge.extension = None
        async with serve(
            bridge.handle,
            "127.0.0.1",
            0,
            process_request=bridge.process_request,
            logger=quiet_logger(),
        ) as server:
            port = server.sockets[0].getsockname()[1]
            settings = replace(case.settings, chatgpt_extension_bridge_port=port)
            async with connect(
                f"ws://127.0.0.1:{port}/extension",
                origin=f"chrome-extension://{settings.chatgpt_extension_id}",
                proxy=None,
            ) as extension:
                challenge = json.loads(await extension.recv())
                await extension.send(
                    json.dumps(
                        {
                            "protocolVersion": 1,
                            "type": "AUTH",
                            "capabilities": [CAPABILITY],
                            "proof": proof(
                                bridge.pairing["extensionSecret"],
                                "extension",
                                challenge["nonce"],
                                settings.chatgpt_extension_id,
                            ),
                        }
                    )
                )
                assert json.loads(await extension.recv())["type"] == "READY"

                async def fake_extension():
                    request = command(json.loads(await extension.recv()))
                    assert request["control"] == RECOVERY_RELEASE
                    active = case.storage["activeReservation"]
                    assert active["reservationId"] == request["reservationId"]
                    assert active["binding"] == request["binding"]
                    assert active["phase"] == "NEW_CHAT_ATTEMPTED"
                    del case.storage["activeReservation"]
                    await extension.send(
                        json.dumps(
                            {
                                "protocolVersion": 1,
                                "type": "RESULT",
                                "requestId": request["requestId"],
                                "status": "OK",
                            }
                        )
                    )

                task = asyncio.create_task(fake_extension())
                try:
                    response = await asyncio.to_thread(
                        BridgeClient(settings).call,
                        RECOVER,
                        dispatchId=case.job.dispatch_id,
                        attemptId=case.job.attempt_id,
                    )
                    assert response["status"] == "OK"
                    await task
                    assert bridge.reservation.active == {}
                    assert case.gateway.updates == case.gateway.events == case.gateway.uploads == []
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
