from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from ..models import DispatchJob, DispatchRequest
from .base import LaunchResult
from .chatgpt_foreground import ForegroundChatGPTLauncher, ForegroundLaunchError


class GuardedForegroundChatGPTLauncher(ForegroundChatGPTLauncher):
    """Fail-closed launcher for ordinary Chat in the unified desktop app.

    The unified app has distinct Chat and Work surfaces. The Factory first invokes
    the ordinary new-chat shortcut, reacquires the ChatGPT foreground surface, then
    locates the composer through Windows UI Automation by its visible placeholder
    ("Mensagem para o ChatGPT" / "Message ChatGPT"). It never guesses composer
    coordinates. Enter remains one-shot and is forbidden until the empty-composer
    proof and exact bootstrap copy-back proof have both succeeded.
    """

    launcher_name = "FOREGROUND_CHATGPT_DESKTOP_V5"

    _ACTIVATION_WAIT_SECONDS = 2.0
    _CHAT_SURFACE_WAIT_SECONDS = 8.0
    _POST_REACQUIRE_WAIT_SECONDS = 2.0
    _COMPOSER_FOCUS_WAIT_SECONDS = 1.0
    _PASTE_SETTLE_SECONDS = 2.25
    _COPY_SETTLE_SECONDS = 0.50
    _FOREGROUND_REACQUIRE_ATTEMPTS = 20
    _FOREGROUND_REACQUIRE_INTERVAL_SECONDS = 0.25
    _COMPOSER_HELPER_TIMEOUT_SECONDS = 12.0

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
            time.sleep(0.75)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._send_ctrl_alt_n()
            self._record_phase(job, hwnd, pid, "CHAT_NEW_SHORTCUT_REQUESTED", send_attempted=False)
            time.sleep(self._CHAT_SURFACE_WAIT_SECONDS)

            # The shortcut can replace HWND/PID. Continue only on the ChatGPT.exe
            # window that is actually in foreground after the transition.
            hwnd, pid = self._wait_for_foreground_chatgpt_surface()
            self._activate_and_maximize(hwnd)
            time.sleep(self._POST_REACQUIRE_WAIT_SECONDS)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "CHAT_SURFACE_REACQUIRED", send_attempted=False)

            self._send_escape_once()
            time.sleep(1.25)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "NEW_CHAT_STABILIZED", send_attempted=False)

            # Critical last mile: do not click a coordinate. Search the Windows UIA
            # tree for the edit control exposed as "Mensagem para o ChatGPT" and
            # require that exact control to hold keyboard focus.
            self._focus_composer_by_placeholder(hwnd)
            time.sleep(self._COMPOSER_FOCUS_WAIT_SECONDS)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "COMPOSER_PLACEHOLDER_FOCUSED", send_attempted=False)

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

            send_attempted = True
            self._record_phase(job, hwnd, pid, "SEND_ATTEMPTED", send_attempted=True)
            self._send_enter_once()
            self._record_phase(job, hwnd, pid, "SEND_KEY_DISPATCHED", send_attempted=True)

            return LaunchResult(
                launcher=self.launcher_name,
                pid=pid,
                detail=(
                    "ordinary Chat requested; foreground ChatGPT surface reacquired; "
                    "composer focused by Mensagem para o ChatGPT placeholder; empty composer "
                    "verified; exact bootstrap verified; single Enter attempted"
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
    def _focus_composer_by_placeholder(cls, hwnd: int) -> None:
        helper = Path(__file__).resolve().parents[2] / "scripts" / "chatgpt-focus-composer.ps1"
        if not helper.is_file():
            raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_HELPER_NOT_FOUND")
        try:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-Hwnd",
                    str(int(hwnd)),
                ],
                capture_output=True,
                text=True,
                timeout=cls._COMPOSER_HELPER_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_UIA_FAILED") from exc

        if completed.returncode != 0:
            detail = f"{completed.stderr}\n{completed.stdout}"
            known = (
                "CHATGPT_DESKTOP_UIA_ROOT_NOT_FOUND",
                "CHATGPT_DESKTOP_COMPOSER_PLACEHOLDER_NOT_FOUND",
                "CHATGPT_DESKTOP_COMPOSER_PLACEHOLDER_AMBIGUOUS",
                "CHATGPT_DESKTOP_COMPOSER_FOCUS_FAILED",
            )
            for code in known:
                if code in detail:
                    raise ForegroundLaunchError(code)
            raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_UIA_FAILED")

        try:
            payload = json.loads(completed.stdout.strip())
        except (TypeError, ValueError) as exc:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_UIA_INVALID_RESULT") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "OK"
            or payload.get("kind") != "CHATGPT_DESKTOP_COMPOSER_FOCUS"
            or payload.get("hasKeyboardFocus") is not True
        ):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_FOCUS_FAILED")

    @classmethod
    def _verify_empty_composer(cls) -> None:
        sentinel = "PF_EMPTY_COMPOSER_PROBE"
        cls._set_clipboard_text(sentinel)
        cls._send_ctrl_a()
        cls._send_ctrl_c()
        time.sleep(cls._COPY_SETTLE_SECONDS)
        observed = cls._get_clipboard_text()
        if observed not in {sentinel, ""}:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_NOT_EMPTY")
