from __future__ import annotations

import time

from ..models import DispatchJob, DispatchRequest
from .base import LaunchResult
from .chatgpt_foreground import ForegroundChatGPTLauncher, ForegroundLaunchError


class GuardedForegroundChatGPTLauncher(ForegroundChatGPTLauncher):
    """Fail-closed desktop launcher with slower fresh-chat stabilization.

    The v1 launcher proved the foreground path. This v2 deliberately removes the
    second Ctrl+N and adds an empty-composer proof before inserting the bootstrap.
    It never presses Enter unless the exact bootstrap was copied back successfully.
    """

    launcher_name = "FOREGROUND_CHATGPT_DESKTOP_V2"

    _ACTIVATION_WAIT_SECONDS = 1.5
    _NEW_CHAT_WAIT_SECONDS = 6.0
    _COMPOSER_FOCUS_WAIT_SECONDS = 1.5
    _PASTE_SETTLE_SECONDS = 1.75
    _COPY_SETTLE_SECONDS = 0.40

    def launch(
        self,
        job: DispatchJob,
        request: DispatchRequest,
        prompt: str,
        *,
        receipt_folder_id: str | None = None,
    ) -> LaunchResult:
        if not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 65536:
            raise ForegroundLaunchError("CHATGPT_FOREGROUND_PROMPT_INVALID")

        hwnd = self._find_chatgpt_window()
        if hwnd == 0:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_WINDOW_NOT_FOUND")
        pid = self._window_pid(hwnd)
        if not pid:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_PROCESS_NOT_FOUND")

        send_attempted = False
        try:
            self._record_phase(job, hwnd, pid, "WINDOW_FOUND", send_attempted=False)
            self._activate_and_maximize(hwnd)
            time.sleep(self._ACTIVATION_WAIT_SECONDS)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "FOREGROUND_CONFIRMED", send_attempted=False)

            # Close menus/overlays, then request exactly one fresh Chat surface.
            self._send_escape_once()
            time.sleep(0.5)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._send_ctrl_n()
            self._record_phase(job, hwnd, pid, "NEW_CHAT_REQUESTED", send_attempted=False)
            time.sleep(self._NEW_CHAT_WAIT_SECONDS)

            # Dismiss any transient overlay produced during navigation and let the
            # composer settle before touching it.
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._send_escape_once()
            time.sleep(0.75)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "NEW_CHAT_STABILIZED", send_attempted=False)

            self._click_composer(hwnd)
            time.sleep(self._COMPOSER_FOCUS_WAIT_SECONDS)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "COMPOSER_CLICKED", send_attempted=False)

            # Fresh-chat proof: the target composer must be empty before the
            # Factory bootstrap is inserted. This prevents overwriting a draft or
            # silently appending to a still-populated prior conversation.
            self._verify_empty_composer()
            self._record_phase(job, hwnd, pid, "EMPTY_COMPOSER_VERIFIED", send_attempted=False)

            self._set_clipboard_text(prompt)
            self._send_ctrl_v()
            self._record_phase(job, hwnd, pid, "BOOTSTRAP_PASTE_ATTEMPTED", send_attempted=False)
            time.sleep(self._PASTE_SETTLE_SECONDS)

            self._ensure_foreground_chatgpt(hwnd, pid)
            self._verify_pasted_prompt(prompt)
            self._record_phase(job, hwnd, pid, "BOOTSTRAP_VERIFIED", send_attempted=False)

            self._send_right_once()
            time.sleep(0.35)
            self._ensure_foreground_chatgpt(hwnd, pid)

            send_attempted = True
            self._record_phase(job, hwnd, pid, "SEND_ATTEMPTED", send_attempted=True)
            self._send_enter_once()
            self._record_phase(job, hwnd, pid, "SEND_KEY_DISPATCHED", send_attempted=True)

            return LaunchResult(
                launcher=self.launcher_name,
                pid=pid,
                detail=(
                    "single Ctrl+N; 6s fresh-chat stabilization; empty composer verified; "
                    "bootstrap exact-copy verified; single Enter attempted"
                ),
            )
        except ForegroundLaunchError as exc:
            self._record_phase(
                job,
                hwnd,
                pid,
                "FAILED",
                send_attempted=send_attempted,
                error_code=exc.code,
            )
            raise

    @classmethod
    def _verify_empty_composer(cls) -> None:
        sentinel = "PF_EMPTY_COMPOSER_PROBE"
        cls._set_clipboard_text(sentinel)
        cls._send_ctrl_a()
        cls._send_ctrl_c()
        time.sleep(cls._COPY_SETTLE_SECONDS)
        observed = cls._get_clipboard_text()
        # If copy left the sentinel intact, there was no selectable composer text.
        # An empty string is also accepted because some Chromium surfaces explicitly
        # place an empty Unicode string on the clipboard for an empty selection.
        if observed not in {sentinel, ""}:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_NOT_EMPTY")
