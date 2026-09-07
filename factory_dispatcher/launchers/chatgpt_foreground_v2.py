from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from ..models import DispatchJob, DispatchRequest
from .base import LaunchResult
from .chatgpt_foreground import ForegroundChatGPTLauncher, ForegroundLaunchError


class GuardedForegroundChatGPTLauncher(ForegroundChatGPTLauncher):
    """Fail-closed desktop launcher for the Chat surface.

    The foreground path is deliberately coarse but guarded: activate the official
    ChatGPT window, click the calibrated left-sidebar "Novo chat" target, wait for
    navigation to settle, require an empty composer, paste the exact bootstrap,
    copy it back for equality, and only then press Enter once.

    The launcher does not use Ctrl+N because that shortcut can inherit Work mode.
    """

    launcher_name = "FOREGROUND_CHATGPT_DESKTOP_V3"

    # Calibrated from the maximized Supremo capture (1550x830): the visible
    # "Novo chat" row is around x=50/y=112. Ratios keep the target window-relative.
    _NEW_CHAT_X_RATIO = 0.032
    _NEW_CHAT_Y_RATIO = 0.135

    _ACTIVATION_WAIT_SECONDS = 1.5
    _NEW_CHAT_WAIT_SECONDS = 8.0
    _COMPOSER_FOCUS_WAIT_SECONDS = 1.75
    _PASTE_SETTLE_SECONDS = 2.0
    _COPY_SETTLE_SECONDS = 0.45

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

            self._send_escape_once()
            time.sleep(0.6)
            self._ensure_foreground_chatgpt(hwnd, pid)

            # Explicit Chat navigation. Unlike Ctrl+N, the left-sidebar New Chat
            # control is intended to leave Work and enter the ordinary Chat surface.
            self._click_new_chat(hwnd)
            self._record_phase(job, hwnd, pid, "NEW_CHAT_CLICKED", send_attempted=False)
            time.sleep(self._NEW_CHAT_WAIT_SECONDS)

            self._ensure_foreground_chatgpt(hwnd, pid)
            self._send_escape_once()
            time.sleep(0.8)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "NEW_CHAT_STABILIZED", send_attempted=False)

            self._click_composer(hwnd)
            time.sleep(self._COMPOSER_FOCUS_WAIT_SECONDS)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "COMPOSER_CLICKED", send_attempted=False)

            # Fresh-chat proof: never overwrite or append to a populated composer.
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
            time.sleep(0.4)
            self._ensure_foreground_chatgpt(hwnd, pid)

            send_attempted = True
            self._record_phase(job, hwnd, pid, "SEND_ATTEMPTED", send_attempted=True)
            self._send_enter_once()
            self._record_phase(job, hwnd, pid, "SEND_KEY_DISPATCHED", send_attempted=True)

            return LaunchResult(
                launcher=self.launcher_name,
                pid=pid,
                detail=(
                    "explicit sidebar New Chat click; 8s stabilization; empty composer verified; "
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
    def _click_new_chat(cls, hwnd: int) -> None:
        user32 = cls._user32()
        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_WINDOW_RECT_FAILED")
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 1000 or height < 650:
            # The coordinate is calibrated for the maximized Factory runtime.
            raise ForegroundLaunchError("CHATGPT_DESKTOP_WINDOW_NOT_MAXIMIZED_FOR_NEW_CHAT")
        x = int(rect.left + width * cls._NEW_CHAT_X_RATIO)
        y = int(rect.top + height * cls._NEW_CHAT_Y_RATIO)
        if not user32.SetCursorPos(x, y):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_CURSOR_FAILED")
        user32.mouse_event(cls._MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(cls._MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    @classmethod
    def _verify_empty_composer(cls) -> None:
        sentinel = "PF_EMPTY_COMPOSER_PROBE"
        cls._set_clipboard_text(sentinel)
        cls._send_ctrl_a()
        cls._send_ctrl_c()
        time.sleep(cls._COPY_SETTLE_SECONDS)
        observed = cls._get_clipboard_text()
        # If copy left the sentinel intact, there was no selectable composer text.
        # Chromium may alternatively place an explicit empty Unicode string.
        if observed not in {sentinel, ""}:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_NOT_EMPTY")
