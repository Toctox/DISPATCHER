"""Mechanical extension send, followed exclusively by the existing receipt contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from .chatgpt_browser import ChatGPTError
from .chatgpt_extension_client import BridgeClient
from .chatgpt_extension_protocol import (
    CONTROL,
    MAX_BOOTSTRAP,
    binding,
    check_settings,
    durable_write,
    tab_lock,
)
from .chatgpt_worker import LocalRecord, _read_receipt, _validate_current_request
from .config import load_local_settings
from .errors import ConfigurationError, ContractError
from .google_auth import build_google_services
from .google_gateway import GoogleWorkspaceGateway
from .models import isoformat
from .mutex import LocalMutex, MutexBusyError
from .receipts import AttemptContext, read_json_object


def _execute(
    manifest, context, prompt, record, settings, gateway, client_factory, clock, monotonic, sleep
):
    check_settings(settings)
    if not settings.writes_enabled:
        raise ChatGPTError("CHATGPT_WRITES_DISABLED")
    pinned = binding(manifest.get("extensionBinding"))
    if pinned["extensionId"] != settings.chatgpt_extension_id:
        raise ChatGPTError("CHATGPT_BINDING_CHANGED")
    if gateway is None:
        try:
            services = build_google_services(settings, interactive=False, http_timeout=10)
        except ConfigurationError as exc:
            raise ChatGPTError("CHATGPT_GOOGLE_AUTH_REQUIRED") from exc
        gateway = GoogleWorkspaceGateway(services, settings)
    job = _validate_current_request(gateway, context, manifest, prompt, clock)
    lease_deadline = job.lease_expires_at
    timeout = manifest.get("timeoutSeconds")
    if type(timeout) is not int or not 1 <= timeout <= settings.chatgpt_browser_timeout_seconds:
        raise ChatGPTError("CHATGPT_INVALID_TIMEOUT")
    deadline = monotonic() + timeout

    def require_live():
        if clock() >= lease_deadline:
            raise ChatGPTError("CHATGPT_STALE_LEASE")
        if monotonic() >= deadline:
            raise ChatGPTError("CHATGPT_RECEIPT_TIMEOUT")

    with tab_lock(settings):
        client = client_factory(settings)
        record.set("PREFLIGHT")
        observed = client.call("PRECHECK", binding=pinned)["binding"]
        if observed != pinned:
            raise ChatGPTError("CHATGPT_BINDING_CHANGED")
        fields = {"binding": pinned, "reservationId": manifest["reservationId"]}
        require_live()
        record.set("PREPARING_TAB")
        client.call("NEW_CHAT", **fields)
        require_live()
        client.call("INSERT_BOOTSTRAP", bootstrap=prompt, **fields)
        current = _validate_current_request(gateway, context, manifest, prompt, clock)
        lease_deadline = min(lease_deadline, current.lease_expires_at)
        require_live()
        # Both bridge and extension also persist their own SEND_ATTEMPTED before forwarding.
        record.set("SEND_ATTEMPTED", sendAttempted=True, sendAttemptedAt=isoformat(clock()))
        durable_write(record.directory / "status.json", record.status)
        budget = min(10, deadline - monotonic(), (lease_deadline - clock()).total_seconds())
        require_live()
        expires_at = int(min(lease_deadline.timestamp(), clock().timestamp() + budget) * 1000)
        try:
            client.call("SEND", timeout=budget, expiresAt=expires_at, **fields)
        except Exception:
            record.set("WAITING_RECEIPT", errorCode="CHATGPT_SEND_UNCERTAIN")
        else:
            # ACK means a mechanical click only, never successful execution.
            record.set("WAITING_RECEIPT", submittedAt=isoformat(clock()))
        while True:
            require_live()
            try:
                receipt = _read_receipt(gateway, context, clock)
            except ChatGPTError:
                raise
            except ContractError:
                record.set("WAITING_RECEIPT", errorCode="CHATGPT_RECEIPT_REJECTED")
                receipt = None
            except Exception:
                record.set("WAITING_RECEIPT", errorCode="CHATGPT_RECEIPT_POLL_ERROR")
                receipt = None
            require_live()
            if receipt is not None:
                record.set(
                    "RECEIPT_FOUND",
                    receiptDriveId=receipt.drive_id,
                    receiptStatus=receipt.raw["status"],
                    errorCode="",
                )
                # Pure reservation bookkeeping. The extension never receives receipt content.
                try:
                    client.call(CONTROL, **fields)
                    release_pending = False
                except Exception:
                    release_pending = True
                record.set(
                    "FINISHED",
                    operationalSuccess=True,
                    needsReconciliation=False,
                    reservationReleasePending=release_pending,
                    errorCode="CHATGPT_RESERVATION_RELEASE_PENDING" if release_pending else "",
                    finishedAt=isoformat(clock()),
                )
                return 0
            delay = min(
                settings.chatgpt_browser_poll_seconds,
                deadline - monotonic(),
                (lease_deadline - clock()).total_seconds(),
            )
            if delay > 0:
                sleep(delay)


def run_worker(
    manifest_path,
    *,
    settings=None,
    gateway=None,
    client_factory=BridgeClient,
    clock=lambda: datetime.now(UTC),
    monotonic=time.monotonic,
    sleep=time.sleep,
):
    manifest_path = Path(manifest_path).resolve()
    manifest = read_json_object(manifest_path)
    context = AttemptContext.from_mapping(manifest.get("execution"))
    settings = settings or load_local_settings(Path(manifest["configFile"]))
    directory = (
        settings.state_directory / "chatgpt-runs" / context.dispatchId / context.attemptId
    ).resolve()
    if (
        manifest_path != directory / "launch.json"
        or manifest.get("manifestVersion") != 3
        or manifest.get("executorType") != "CHATGPT"
        or manifest.get("browserMode") != "EXTENSION_BRIDGE"
    ):
        raise ChatGPTError("CHATGPT_MANIFEST_INVALID")
    try:
        with LocalMutex(directory / "worker.lock"):
            initial = read_json_object(directory / "status.json")
            if initial.get("state") != "PREPARED":
                return 2
            record = LocalRecord(directory, initial, clock)
            record.set("STARTING", workerPid=os.getpid(), startedAt=isoformat(clock()))
            try:
                with (directory / "bootstrap.txt").open("rb") as handle:
                    content = handle.read(MAX_BOOTSTRAP + 1)
                if len(content) > MAX_BOOTSTRAP or hashlib.sha256(
                    content
                ).hexdigest() != manifest.get("bootstrapHash"):
                    raise ChatGPTError("CHATGPT_BOOTSTRAP_MISMATCH")
                return _execute(
                    manifest,
                    context,
                    content.decode("utf-8"),
                    record,
                    settings,
                    gateway,
                    client_factory,
                    clock,
                    monotonic,
                    sleep,
                )
            except Exception as exc:
                code = exc.code if isinstance(exc, ChatGPTError) else "CHATGPT_WORKER_FAILED"
                if isinstance(exc, MutexBusyError):
                    code = "CHATGPT_TAB_BUSY"
                record.set(
                    "STOPPED",
                    operationalSuccess=False,
                    needsReconciliation=True,
                    retryPolicy="RECONCILE_BEFORE_RETRY",
                    errorCode=code,
                    finishedAt=isoformat(clock()),
                )
                return 1
    except MutexBusyError:
        return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run one extension CHATGPT attempt.")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        return run_worker(args.manifest)
    except Exception:
        print(json.dumps({"outcome": "CHATGPT_WORKER_REJECTED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
