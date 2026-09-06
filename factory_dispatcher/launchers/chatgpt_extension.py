"""Launch an extension attempt only after a read-only, binding-pinned preflight."""

import hashlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

from ..chatgpt_browser import ChatGPTError
from ..chatgpt_extension_client import BridgeClient
from ..chatgpt_extension_protocol import MAX_BOOTSTRAP, binding, check_settings, tab_lock
from ..chatgpt_state import request_digest
from ..models import ExecutorType, RetryPolicy
from ..mutex import MutexBusyError
from ..receipts import AttemptContext, write_json
from .base import LaunchResult


class ExtensionChatGPTLauncher:
    requires_reconciliation = True

    def __init__(
        self, settings, *, config_file, client_factory=BridgeClient, popen_factory=subprocess.Popen
    ):
        self.settings = settings
        self.config_file = config_file.resolve()
        self.client_factory = client_factory
        self.popen_factory = popen_factory
        self._binding = None
        self._request_hash = None

    def _timeout(self, request):
        if request.executor_type != ExecutorType.CHATGPT:
            raise ChatGPTError("CHATGPT_EXECUTOR_MISMATCH")
        timeout = self.settings.chatgpt_browser_timeout_seconds
        if "maxExecutionMinutes" in request.raw:
            minutes = request.raw["maxExecutionMinutes"]
            if type(minutes) is not int or not 1 <= minutes <= 240:
                raise ChatGPTError("CHATGPT_INVALID_TIMEOUT")
            timeout = min(timeout, minutes * 60)
        return timeout

    def preflight(self, request):
        self._binding = None
        self._request_hash = None
        self._timeout(request)
        check_settings(self.settings)
        try:
            with tab_lock(self.settings):
                response = self.client_factory(self.settings).call("PRECHECK")
                observed = binding(response.get("binding"))
                if observed["extensionId"] != self.settings.chatgpt_extension_id:
                    raise ChatGPTError("CHATGPT_BINDING_CHANGED")
            self._binding = observed
            self._request_hash = request_digest(request.raw)
        except MutexBusyError as exc:
            raise ChatGPTError("CHATGPT_TAB_BUSY") from exc

    def launch(self, job, request, prompt, *, receipt_folder_id=None):
        check_settings(self.settings)
        timeout = self._timeout(request)
        if self._binding is None or self._request_hash != request_digest(request.raw):
            raise ChatGPTError("CHATGPT_PREFLIGHT_REQUIRED")
        if job.retry_policy == RetryPolicy.SAFE_RETRY:
            raise ChatGPTError("CHATGPT_RECONCILIATION_POLICY_REQUIRED")
        content = prompt.encode("utf-8")
        if not 1 <= len(content) <= MAX_BOOTSTRAP:
            raise ChatGPTError("CHATGPT_BOOTSTRAP_MISMATCH")
        context = AttemptContext.from_job(job, receipt_folder_id)
        directory = (
            self.settings.state_directory / "chatgpt-runs" / context.dispatchId / context.attemptId
        ).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        prompt_path = directory / "bootstrap.txt"
        prompt_path.write_bytes(content)
        os.chmod(prompt_path, 0o600)
        manifest_path = directory / "launch.json"
        write_json(
            manifest_path,
            {
                "manifestVersion": 3,
                "executorType": "CHATGPT",
                "browserMode": "EXTENSION_BRIDGE",
                "extensionBinding": self._binding,
                "reservationId": str(uuid.uuid4()),
                "execution": context.to_dict(),
                "configFile": str(self.config_file),
                "requestHash": self._request_hash,
                "bootstrapHash": hashlib.sha256(content).hexdigest(),
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
        self._binding = None
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            process = self.popen_factory(
                [
                    sys.executable,
                    "-m",
                    "factory_dispatcher.chatgpt_extension_worker",
                    str(manifest_path),
                ],
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
            "EXTENSION_CHATGPT_WORKER",
            pid=int(process.pid),
            detail=f"status: {directory / 'status.json'}",
        )
