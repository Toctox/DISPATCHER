from __future__ import annotations

import time

from ..models import DispatchJob, DispatchRequest
from .base import LaunchResult
from .chatgpt_foreground import ForegroundChatGPTLauncher, ForegroundLaunchError


class GuardedForegroundChatGPTLauncher(ForegroundChatGPTLauncher):
    """Fail-closed launcher for ordinary Chat in the unified desktop app.

    The unified app has distinct Chat and Work surfaces. A generic New Chat action
    can inherit Work, so the Factory first invokes the documented Windows new-chat
    shortcut (Ctrl+Alt+N), then reacquires and proves that the foreground surface is
    owned by ChatGPT.exe. Only after a long stabilization period does it touch the
    composer. Enter remains one-shot and is forbidden until both the empty-composer
    proof and exact bootstrap copy-back proof have succeeded.
    """

    launcher_name = "FOREGROUND_CHATGPT_DESKTOP_V4"

    _ACTIVATION_WAIT_SECONDS = 2.0
    _CHAT_SURFACE_WAIT_SECONDS = 8.0
    _POST_REACQUIRE_WAIT_SECONDS = 2.0
    _COMPOSER_FOCUS_WAIT_SECONDS = 2.0
    _PASTE_SETTLE_SECONDS = 2.25
    _COPY_SETTLE_SECONDS = 0.50
    _FOREGROUND_REACQUIRE_ATTEMPTS = 20
    _FOREGROUND_REACQUIRE_INTERVAL_SECONDS = 0.25

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

            # Close transient menus first. The ordinary new-chat shortcut is used
            # specifically so a Work session cannot silently supply the next target.
            self._send_escape_once()
            time.sleep(0.75)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._send_ctrl_alt_n()
            self._record_phase(job, hwnd, pid, "CHAT_NEW_SHORTCUT_REQUESTED", send_attempted=False)
            time.sleep(self._CHAT_SURFACE_WAIT_SECONDS)

            # Ctrl+Alt+N may replace the HWND or activate another ChatGPT.exe window.
            # Rebind only to the *current foreground* ChatGPT surface; never continue
            # typing into the old Work/agent window by assumption.
            hwnd, pid = self._wait_for_foreground_chatgpt_surface()
            self._activate_and_maximize(hwnd)
            time.sleep(self._POST_REACQUIRE_WAIT_SECONDS)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "CHAT_SURFACE_REACQUIRED", send_attempted=False)

            self._send_escape_once()
            time.sleep(1.25)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "NEW_CHAT_STABILIZED", send_attempted=False)

            self._click_composer(hwnd)
            time.sleep(self._COMPOSER_FOCUS_WAIT_SECONDS)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "COMPOSER_CLICKED", send_attempted=False)

            # Fresh-chat proof. If the target still contains prior text, fail before
            # writing anything and leave the queue for reconciliation.
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
            time.sleep(0.5)
            self._ensure_foreground_chatgpt(hwnd, pid)

            # One-way boundary: from this point an external send may have occurred.
            send_attempted = True
            self._record_phase(job, hwnd, pid, "SEND_ATTEMPTED", send_attempted=True)
            self._send_enter_once()
            self._record_phase(job, hwnd, pid, "SEND_KEY_DISPATCHED", send_attempted=True)

            return LaunchResult(
                launcher=self.launcher_name,
                pid=pid,
                detail=(
                    "Ctrl+Alt+N ordinary Chat requested; foreground ChatGPT surface reacquired; "
                    "8s+ stabilization; empty composer verified; exact bootstrap verified; "
                    "single Enter attempted"
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
    def _send_ctrl_alt_n(cls) -> None:
        user32 = cls._user32()
        user32.keybd_event(cls._VK_CONTROL, 0, 0, 0)
        user32.keybd_event(cls._VK_MENU, 0, 0, 0)
        user32.keybd_event(cls._VK_N, 0, 0, 0)
        user32.keybd_event(cls._VK_N, 0, cls._KEYEVENTF_KEYUP, 0)
        user32.keybd_event(cls._VK_MENU, 0, cls._KEYEVENTF_KEYUP, 0)
        user32.keybd_event(cls._VK_CONTROL, 0, cls._KEYEVENTF_KEYUP, 0)

    @classmethod
    def _wait_for_foreground_chatgpt_surface(cls) -> tuple[int, int]:
        user32 = cls._user32()
        for _ in range(cls._FOREGROUND_REACQUIRE_ATTEMPTS):
            hwnd = int(user32.GetForegroundWindow() or 0)
            if hwnd and user32.IsWindowVisible(hwnd):
                pid = cls._window_pid(hwnd)
                if pid and cls._process_basename(pid) == "chatgpt.exe":
                    return hwnd, pid
            time.sleep(cls._FOREGROUND_REACQUIRE_INTERVAL_SECONDS)
        raise ForegroundLaunchError("CHATGPT_DESKTOP_CHAT_SURFACE_NOT_FOREGROUND")

    @classmethod
    def _verify_empty_composer(cls) -> None:
        sentinel = "PF_EMPTY_COMPOSER_PROBE"
        cls._set_clipboard_text(sentinel)
        cls._send_ctrl_a()
        cls._send_ctrl_c()
        time.sleep(cls._COPY_SETTLE_SECONDS)
        observed = cls._get_clipboard_text()
        # If copy leaves the sentinel intact there was no selectable composer text.
        # Chromium may alternatively place an explicit empty Unicode string.
        if observed not in {sentinel, ""}:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_NOT_EMPTY")
