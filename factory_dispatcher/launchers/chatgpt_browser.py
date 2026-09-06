"""Asynchronous ChatGPT browser worker launcher. Does not interpret tasks."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from ..chatgpt_browser import (
    BrowserBinding,
    ChatGPTError,
    browser_session,
    check_environment,
    require_tab_available,
    tab_lock,
)
from ..chatgpt_state import request_digest
from ..config import LocalSettings
from ..models import DispatchJob, DispatchRequest, ExecutorType, RetryPolicy
from ..receipts import AttemptContext, write_json
from .base import LaunchResult


class BrowserChatGPTLauncher:
    requires_reconciliation = True

    def __init__(
        self,
        settings: LocalSettings,
        *,
        config_file: Path,
        environment_check=check_environment,
        session_factory=browser_session,
        popen_factory=subprocess.Popen,
    ):
        self.settings = settings
        self.config_file = config_file.resolve()
        self.environment_check = environment_check
        self.session_factory = session_factory
        self.popen_factory = popen_factory
        self._preflight_binding = None
        self._preflight_request_hash = None

    def _timeout(self, request: DispatchRequest) -> int:
        if request.executor_type != ExecutorType.CHATGPT:
            raise ChatGPTError("CHATGPT_EXECUTOR_MISMATCH")
        timeout = self.settings.chatgpt_browser_timeout_seconds
        if "maxExecutionMinutes" in request.raw:
            minutes = request.raw["maxExecutionMinutes"]
            if type(minutes) is not int or not 1 <= minutes <= 240:
                raise ChatGPTError("CHATGPT_INVALID_TIMEOUT")
            timeout = min(timeout, minutes * 60)
        return timeout

    def preflight(self, request: DispatchRequest) -> None:
        self._preflight_binding = None
        self._preflight_request_hash = None
        self._timeout(request)
        try:
            endpoint = self.environment_check(self.settings)
            with tab_lock(self.settings):
                require_tab_available(self.settings)
                with self.session_factory(self.settings, endpoint) as ui:
                    ui.preflight()
                    ui.verify_binding(ui.binding)
                    observed = BrowserBinding.from_mapping(ui.binding.to_dict())
            self._preflight_binding = observed
            self._preflight_request_hash = request_digest(request.raw)
        except ChatGPTError:
            raise
        except Exception as exc:
            raise ChatGPTError("CHATGPT_PREFLIGHT_FAILED") from exc

    def launch(
        self,
        job: DispatchJob,
        request: DispatchRequest,
        prompt: str,
        *,
        receipt_folder_id: str | None = None,
    ) -> LaunchResult:
        timeout = self._timeout(request)
        self.environment_check(self.settings)
        if self._preflight_binding is None or self._preflight_request_hash != request_digest(
            request.raw
        ):
            raise ChatGPTError("CHATGPT_PREFLIGHT_REQUIRED")
        if job.retry_policy == RetryPolicy.SAFE_RETRY:
            raise ChatGPTError("CHATGPT_RECONCILIATION_POLICY_REQUIRED")
        context = AttemptContext.from_job(job, receipt_folder_id)
        directory = (
            self.settings.state_directory / "chatgpt-runs" / context.dispatchId / context.attemptId
        ).resolve()
        # Never reuse attempt evidence, even if a previous worker crashed before sending.
        directory.mkdir(parents=True, exist_ok=False)
        bootstrap = prompt.encode("utf-8")
        prompt_path = directory / "bootstrap.txt"
        prompt_path.write_bytes(bootstrap)  # Preserve UTF-8 and LF exactly on Windows.
        os.chmod(prompt_path, 0o600)
        manifest_path = directory / "launch.json"
        write_json(
            manifest_path,
            {
                "manifestVersion": 2,
                "executorType": "CHATGPT",
                "browserMode": "ATTACH_CDP",
                "browserBinding": self._preflight_binding.to_dict(),
                "execution": context.to_dict(),
                "configFile": str(self.config_file),
                "requestHash": request_digest(request.raw),
                "bootstrapHash": hashlib.sha256(bootstrap).hexdigest(),
                "timeoutSeconds": timeout,
            },
            exclusive=True,
        )
        write_json(
            directory / "status.json",
            {
                "state": "PREPARED",
                "dispatchId": context.dispatchId,
                "attemptId": context.attemptId,
                "requestDriveId": context.requestDriveId,
                "sendAttempted": False,
                "needsReconciliation": True,
                "retryPolicy": "RECONCILE_BEFORE_RETRY",
            },
            exclusive=True,
        )
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            process = self.popen_factory(
                [sys.executable, "-m", "factory_dispatcher.chatgpt_worker", str(manifest_path)],
                cwd=str(Path(__file__).resolve().parents[2]),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=flags,
            )
        except Exception as exc:
            write_json(
                directory / "status.json",
                {
                    "state": "STOPPED",
                    "errorCode": "CHATGPT_WORKER_START_FAILED",
                    "sendAttempted": False,
                    "needsReconciliation": True,
                    "retryPolicy": "RECONCILE_BEFORE_RETRY",
                },
            )
            raise ChatGPTError("CHATGPT_WORKER_START_FAILED") from exc
        return LaunchResult(
            "BROWSER_CHATGPT_WORKER",
            pid=int(process.pid),
            detail=f"status: {directory / 'status.json'}",
        )
