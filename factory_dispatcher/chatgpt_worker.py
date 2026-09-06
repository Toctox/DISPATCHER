"""One bootstrap send, then read-only receipt polling. Never reads model responses."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .bootstrap import build_bootstrap
from .chatgpt_browser import (
    BrowserBinding,
    ChatGPTError,
    browser_session,
    check_environment,
    release_tab,
    require_tab_available,
    reserve_tab,
    tab_lock,
)
from .chatgpt_state import request_digest
from .config import load_local_settings
from .errors import ConfigurationError, ContractError
from .google_auth import build_google_services
from .google_gateway import GoogleWorkspaceGateway
from .models import DispatchRequest, DispatchState, ExecutorType, RetryPolicy, isoformat
from .mutex import LocalMutex, MutexBusyError
from .receipts import MAX_JSON_BYTES, AttemptContext, read_json_object, validate_receipt, write_json


def _current_job(gateway, context, clock, *, receipt_id=None):
    jobs = [job for job in gateway.read_queue() if job.dispatch_id == context.dispatchId]
    if len(jobs) != 1:
        raise ChatGPTError("CHATGPT_CURRENT_ATTEMPT_INVALID")
    job = gateway.read_job(jobs[0].row_number)
    candidate = job
    if (
        receipt_id
        and job.status == DispatchState.RESULT_STAGED
        and job.receipt_drive_id == receipt_id
    ):
        # Dispatcher may reconcile before our poll. This never authorizes a send.
        candidate = replace(job, status=DispatchState.RUNNING)
    try:
        context.match_current_job(candidate, clock())
    except ContractError as exc:
        raise ChatGPTError("CHATGPT_STALE_LEASE") from exc
    return job


def _validate_current_request(gateway, context, manifest, prompt, clock):
    factory = gateway.read_factory_config()
    if factory.receipts_folder_id != context.receiptFolderId:
        raise ChatGPTError("CHATGPT_RECEIPT_FOLDER_CHANGED")
    job = _current_job(gateway, context, clock)
    if job.retry_policy == RetryPolicy.SAFE_RETRY:
        raise ChatGPTError("CHATGPT_RECONCILIATION_POLICY_REQUIRED")
    request = DispatchRequest(
        gateway.read_json_artifact(context.requestDriveId, factory.requests_folder_id)
    )
    if (
        request.executor_type != ExecutorType.CHATGPT
        or request.raw.get("artifactType") != "DISPATCH_REQUEST"
        or request.raw.get("dispatchId") != job.dispatch_id
        or request.raw.get("agentId") != job.agent_id
        or request.change_id != job.change_id
        or request_digest(request.raw) != manifest.get("requestHash")
    ):
        raise ChatGPTError("CHATGPT_REQUEST_CHANGED")
    if not job.staging_folder_id or build_bootstrap(
        job, factory, job.staging_folder_id, request
    ).encode("utf-8") != prompt.encode("utf-8"):
        raise ChatGPTError("CHATGPT_BOOTSTRAP_MISMATCH")
    if "maxExecutionMinutes" in request.raw:
        minutes = request.raw["maxExecutionMinutes"]
        timeout = manifest.get("timeoutSeconds")
        if (
            type(minutes) is not int
            or not 1 <= minutes <= 240
            or type(timeout) is not int
            or timeout > minutes * 60
        ):
            raise ChatGPTError("CHATGPT_INVALID_TIMEOUT")
    # Reading the request may have consumed time or raced with a queue writer.
    latest = _current_job(gateway, context, clock)
    if latest.staging_folder_id != job.staging_folder_id:
        raise ChatGPTError("CHATGPT_BOOTSTRAP_MISMATCH")
    return latest


def _read_receipt(gateway, context, clock):
    factory = gateway.read_factory_config()
    if factory.receipts_folder_id != context.receiptFolderId:
        raise ChatGPTError("CHATGPT_RECEIPT_FOLDER_CHANGED")
    receipt = gateway.find_receipt(context.receiptFolderId, context.receipt_name)
    # Always recheck authority AFTER the network call, even if nothing was found.
    _current_job(gateway, context, clock, receipt_id=receipt.drive_id if receipt else None)
    if receipt is None:
        return None
    validate_receipt(receipt.raw)
    context.match_receipt(receipt.raw)
    return receipt


class LocalRecord:
    def __init__(self, directory, initial, clock):
        self.directory = directory
        self.status = initial
        self.clock = clock

    def set(self, state, **fields):
        timestamp = isoformat(self.clock())
        self.status.update(state=state, updatedAt=timestamp, **fields)
        write_json(self.directory / "status.json", self.status)
        # No exception text, bootstrap, lease, URL, cookies or response payloads in logs.
        with (self.directory / "browser.log").open("a", encoding="utf-8") as handle:
            os.chmod(self.directory / "browser.log", 0o600)
            handle.write(
                json.dumps(
                    {"at": timestamp, "state": state, "errorCode": fields.get("errorCode", "")}
                )
                + "\n"
            )


def _execute(
    manifest,
    context,
    prompt,
    record,
    settings,
    gateway,
    session_factory,
    environment_check,
    clock,
    monotonic,
    sleep,
):
    if not settings.writes_enabled:
        raise ChatGPTError("CHATGPT_WRITES_DISABLED")
    endpoint = environment_check(settings)
    binding = BrowserBinding.from_mapping(manifest.get("browserBinding"))
    if endpoint != binding.endpoint:
        raise ChatGPTError("CHATGPT_BROWSER_CHANGED")
    if gateway is None:
        try:
            services = build_google_services(settings, interactive=False, http_timeout=10)
        except ConfigurationError as exc:
            raise ChatGPTError("CHATGPT_GOOGLE_AUTH_REQUIRED") from exc
        gateway = GoogleWorkspaceGateway(services, settings)
    initial_job = _validate_current_request(gateway, context, manifest, prompt, clock)
    lease_deadline = initial_job.lease_expires_at
    timeout = manifest.get("timeoutSeconds")
    if type(timeout) is not int or not 1 <= timeout <= settings.chatgpt_browser_timeout_seconds:
        raise ChatGPTError("CHATGPT_INVALID_TIMEOUT")
    deadline = monotonic() + timeout
    with tab_lock(settings):
        require_tab_available(settings)
        record.set("ATTACHING_BROWSER")
        with session_factory(settings, endpoint) as ui:
            ui.verify_binding(binding)
            ui.require_auth()
            ui.prepare(prompt)
            current = _validate_current_request(gateway, context, manifest, prompt, clock)
            lease_deadline = min(lease_deadline, current.lease_expires_at)
            if monotonic() >= deadline:
                raise ChatGPTError("CHATGPT_RECEIPT_TIMEOUT")
            if clock() >= lease_deadline:
                raise ChatGPTError("CHATGPT_STALE_LEASE")
            ui.verify_binding(binding)
            # Durable write-ahead evidence. A click exception can still mean submission occurred.
            reserve_tab(settings, binding, context)
            record.set("SEND_ATTEMPTED", sendAttempted=True, sendAttemptedAt=isoformat(clock()))
            send_budget = min(
                10, deadline - monotonic(), (lease_deadline - clock()).total_seconds()
            )
            if send_budget <= 0:
                raise ChatGPTError("CHATGPT_STALE_LEASE")
            try:
                ui.send(timeout_ms=max(1, int(send_budget * 1000)))
            except Exception:
                record.set("WAITING_RECEIPT", errorCode="CHATGPT_SEND_UNCERTAIN")
            else:
                record.set("WAITING_RECEIPT", submittedAt=isoformat(clock()))
            while True:
                if clock() >= lease_deadline:
                    raise ChatGPTError("CHATGPT_STALE_LEASE")
                if monotonic() >= deadline:
                    raise ChatGPTError("CHATGPT_RECEIPT_TIMEOUT")
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
                # A slow network call never extends our deadline or lease authority.
                if clock() >= lease_deadline:
                    raise ChatGPTError("CHATGPT_STALE_LEASE")
                if monotonic() >= deadline:
                    raise ChatGPTError("CHATGPT_RECEIPT_TIMEOUT")
                if receipt is not None:
                    record.set(
                        "RECEIPT_FOUND",
                        receiptDriveId=receipt.drive_id,
                        receiptStatus=receipt.raw["status"],
                        errorCode="",
                    )
                    break
                remaining_lease = (lease_deadline - clock()).total_seconds()
                delay = min(
                    settings.chatgpt_browser_poll_seconds, deadline - monotonic(), remaining_lease
                )
                if delay > 0:
                    sleep(delay)
        # Detach only. The browser and its existing tab remain available.
        release_tab(settings, record.status["receiptDriveId"])
        record.set(
            "FINISHED",
            operationalSuccess=True,
            needsReconciliation=False,
            disconnectedAt=isoformat(clock()),
            errorCode="",
        )
    return 0


def run_worker(
    manifest_path: Path,
    *,
    settings=None,
    gateway=None,
    session_factory=browser_session,
    environment_check=check_environment,
    clock=lambda: datetime.now(UTC),
    monotonic=time.monotonic,
    sleep=time.sleep,
) -> int:
    manifest_path = manifest_path.resolve()
    manifest = read_json_object(manifest_path)
    context = AttemptContext.from_mapping(manifest.get("execution"))
    settings = settings or load_local_settings(Path(manifest["configFile"]))
    directory = (
        settings.state_directory / "chatgpt-runs" / context.dispatchId / context.attemptId
    ).resolve()
    if (
        manifest_path != directory / "launch.json"
        or manifest.get("manifestVersion") != 2
        or manifest.get("executorType") != "CHATGPT"
        or manifest.get("browserMode") != "ATTACH_CDP"
    ):
        raise ChatGPTError("CHATGPT_MANIFEST_INVALID")
    try:
        with LocalMutex(directory / "worker.lock"):
            initial = read_json_object(directory / "status.json")
            if initial.get("state") != "PREPARED":
                # No resume command may send the same attempt twice. Preserve original evidence.
                return 2
            record = LocalRecord(directory, initial, clock)
            record.set("STARTING", workerPid=os.getpid(), startedAt=isoformat(clock()))
            try:
                with (directory / "bootstrap.txt").open("rb") as handle:
                    content = handle.read(MAX_JSON_BYTES + 1)
                if len(content) > MAX_JSON_BYTES or hashlib.sha256(
                    content
                ).hexdigest() != manifest.get("bootstrapHash"):
                    raise ChatGPTError("CHATGPT_BOOTSTRAP_MISMATCH")
                prompt = content.decode("utf-8")
                return _execute(
                    manifest,
                    context,
                    prompt,
                    record,
                    settings,
                    gateway,
                    session_factory,
                    environment_check,
                    clock,
                    monotonic,
                    sleep,
                )
            except Exception as exc:
                code = exc.code if isinstance(exc, ChatGPTError) else "CHATGPT_WORKER_FAILED"
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run one local ChatGPT browser attempt.")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        return run_worker(args.manifest)
    except Exception:
        print(json.dumps({"outcome": "CHATGPT_WORKER_REJECTED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
