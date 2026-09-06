from __future__ import annotations

import webbrowser
from collections.abc import Callable

import pyperclip

from ..errors import LaunchGuardError
from ..models import DispatchJob, DispatchRequest, ExecutorType
from .base import LaunchResult


class ManualChatGPTLauncher:
    def __init__(
        self,
        url: str,
        *,
        enabled: bool,
        clipboard_copy: Callable[[str], None] = pyperclip.copy,
        browser_open: Callable[[str], bool] = webbrowser.open,
    ) -> None:
        self.url = url
        self.enabled = enabled
        self.clipboard_copy = clipboard_copy
        self.browser_open = browser_open

    def preflight(self, request: DispatchRequest) -> None:
        if request.executor_type != ExecutorType.CHATGPT or not self.enabled:
            raise LaunchGuardError("CHATGPT_MANUAL_DISABLED")

    def launch(
        self,
        job: DispatchJob,
        request: DispatchRequest,
        prompt: str,
        *,
        receipt_folder_id: str | None = None,
    ) -> LaunchResult:
        if request.executor_type != ExecutorType.CHATGPT:
            raise LaunchGuardError("ManualChatGPTLauncher requires executorType=CHATGPT")
        if not self.enabled:
            raise LaunchGuardError("manual ChatGPT launch is disabled in local config")
        self.clipboard_copy(prompt)
        if not self.browser_open(self.url):
            raise LaunchGuardError("the default browser did not accept the ChatGPT URL")
        return LaunchResult(
            launcher="MANUAL_CHATGPT",
            detail="bootstrap copied to clipboard; browser opened without DOM automation",
        )
