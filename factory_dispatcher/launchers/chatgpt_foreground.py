from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..errors import LaunchGuardError
from ..models import DispatchJob, DispatchRequest
from .base import LaunchResult


class ForegroundLaunchError(LaunchGuardError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ForegroundChatGPTLauncher:
    """Closed Windows foreground launcher for the official ChatGPT desktop app.

    This launcher intentionally exposes no generic keyboard/mouse/shell surface. Its
    only operation is: activate the ChatGPT desktop window, open a fresh chat with
    Ctrl+N, prove the app stayed foreground, focus the composer at a fixed
    window-relative point, paste the canonical Factory bootstrap, verify exact
    composer contents through the clipboard, and press Enter exactly once.
    """

    requires_reconciliation = True
    launcher_name = "FOREGROUND_CHATGPT_DESKTOP"

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SW_RESTORE = 9
    _SW_MAXIMIZE = 3
    _CF_UNICODETEXT = 13
    _GMEM_MOVEABLE = 0x0002
    _VK_CONTROL = 0x11
    _VK_N = 0x4E
    _VK_V = 0x56
    _VK_A = 0x41
    _VK_C = 0x43
    _VK_RETURN = 0x0D
    _VK_ESCAPE = 0x1B
    _VK_RIGHT = 0x27
    _VK_MENU = 0x12
    _KEYEVENTF_KEYUP = 0x0002
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004

    _COMPOSER_X_RATIO = 0.50
    _COMPOSER_Y_RATIO = 0.67

    _ACTIVATION_WAIT_SECONDS = 1.0
    _NEW_CHAT_WAIT_SECONDS = 3.0
    _COMPOSER_FOCUS_WAIT_SECONDS = 0.8
    _PASTE_SETTLE_SECONDS = 1.0
    _COPY_SETTLE_SECONDS = 0.25

    def __init__(self, state_directory: Path) -> None:
        self.state_directory = Path(state_directory)

    @classmethod
    def available(cls) -> bool:
        return cls._find_chatgpt_window() != 0

    def preflight(self, request: DispatchRequest) -> None:
        if sys.platform != "win32":
            raise ForegroundLaunchError("CHATGPT_DESKTOP_UNSUPPORTED_PLATFORM")
        if self._find_chatgpt_window() == 0:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_WINDOW_NOT_FOUND")

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
            time.sleep(0.25)

            for sequence in (1, 2):
                self._ensure_foreground_chatgpt(hwnd, pid)
                self._send_ctrl_n()
                self._record_phase(
                    job,
                    hwnd,
                    pid,
                    f"NEW_CHAT_REQUESTED_{sequence}",
                    send_attempted=False,
                )
                time.sleep(self._NEW_CHAT_WAIT_SECONDS)

            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "NEW_CHAT_STABILIZED", send_attempted=False)

            self._click_composer(hwnd)
            time.sleep(self._COMPOSER_FOCUS_WAIT_SECONDS)
            self._ensure_foreground_chatgpt(hwnd, pid)
            self._record_phase(job, hwnd, pid, "COMPOSER_CLICKED", send_attempted=False)

            self._set_clipboard_text(prompt)
            self._send_ctrl_v()
            self._record_phase(job, hwnd, pid, "BOOTSTRAP_PASTE_ATTEMPTED", send_attempted=False)
            time.sleep(self._PASTE_SETTLE_SECONDS)

            self._ensure_foreground_chatgpt(hwnd, pid)
            self._verify_pasted_prompt(prompt)
            self._record_phase(job, hwnd, pid, "BOOTSTRAP_VERIFIED", send_attempted=False)

            self._send_right_once()
            time.sleep(0.2)
            self._ensure_foreground_chatgpt(hwnd, pid)

            send_attempted = True
            self._record_phase(job, hwnd, pid, "SEND_ATTEMPTED", send_attempted=True)
            self._send_enter_once()
            self._record_phase(job, hwnd, pid, "SEND_KEY_DISPATCHED", send_attempted=True)

            return LaunchResult(
                launcher=self.launcher_name,
                pid=pid,
                detail=(
                    "fresh-chat Ctrl+N x2 stabilized; foreground/process validated; "
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

    def _record_phase(
        self,
        job: DispatchJob,
        hwnd: int,
        pid: int,
        phase: str,
        *,
        send_attempted: bool,
        error_code: str | None = None,
    ) -> None:
        directory = self.state_directory / "chatgpt-foreground"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{job.attempt_id}.json"
            history: list[dict[str, object]] = []
            if path.exists():
                try:
                    previous = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(previous, dict) and isinstance(previous.get("history"), list):
                        history = [item for item in previous["history"] if isinstance(item, dict)]
                except (OSError, ValueError):
                    history = []
            entry: dict[str, object] = {
                "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "phase": phase,
                "sendAttempted": send_attempted,
            }
            if error_code:
                entry["errorCode"] = error_code
            history.append(entry)
            payload = {
                "dispatchId": job.dispatch_id,
                "attemptId": job.attempt_id,
                "agentId": job.agent_id,
                "hwnd": int(hwnd),
                "pid": int(pid),
                "phase": phase,
                "sendAttempted": send_attempted,
                "errorCode": error_code,
                "history": history,
            }
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_STATE_WRITE_FAILED") from exc

    @classmethod
    def _user32(cls):
        return ctypes.windll.user32

    @classmethod
    def _kernel32(cls):
        return ctypes.windll.kernel32

    @classmethod
    def _window_pid(cls, hwnd: int) -> int | None:
        if sys.platform != "win32" or not hwnd:
            return None
        pid = wintypes.DWORD()
        cls._user32().GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value) if pid.value else None

    @classmethod
    def _process_basename(cls, pid: int) -> str:
        kernel32 = cls._kernel32()
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        query_name = kernel32.QueryFullProcessImageNameW
        query_name.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        query_name.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(cls._PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not query_name(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return os.path.basename(buffer.value).lower()
        finally:
            close_handle(handle)

    @classmethod
    def _find_chatgpt_window(cls) -> int:
        if sys.platform != "win32":
            return 0
        user32 = cls._user32()
        matches: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and cls._process_basename(int(pid.value)) == "chatgpt.exe":
                matches.append(int(hwnd))
                return False
            return True

        callback = callback_type(visit)
        user32.EnumWindows(callback, 0)
        return matches[0] if matches else 0

    @classmethod
    def _ensure_foreground_chatgpt(cls, hwnd: int, expected_pid: int) -> None:
        user32 = cls._user32()
        if not user32.IsWindow(wintypes.HWND(hwnd)) or not user32.IsWindowVisible(wintypes.HWND(hwnd)):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_WINDOW_LOST")
        current_pid = cls._window_pid(hwnd)
        if current_pid != expected_pid or cls._process_basename(expected_pid) != "chatgpt.exe":
            raise ForegroundLaunchError("CHATGPT_DESKTOP_PROCESS_CHANGED")
        foreground = int(user32.GetForegroundWindow() or 0)
        if foreground != int(hwnd):
            cls._activate_and_maximize(hwnd)
            time.sleep(0.4)
            foreground = int(user32.GetForegroundWindow() or 0)
        if foreground != int(hwnd):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_FOREGROUND_LOST")

    @classmethod
    def _activate_and_maximize(cls, hwnd: int) -> None:
        user32 = cls._user32()
        user32.ShowWindow(wintypes.HWND(hwnd), cls._SW_RESTORE)
        user32.ShowWindow(wintypes.HWND(hwnd), cls._SW_MAXIMIZE)
        user32.keybd_event(cls._VK_MENU, 0, 0, 0)
        user32.keybd_event(cls._VK_MENU, 0, cls._KEYEVENTF_KEYUP, 0)
        if not user32.SetForegroundWindow(wintypes.HWND(hwnd)):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_ACTIVATION_FAILED")
        user32.BringWindowToTop(wintypes.HWND(hwnd))

    @classmethod
    def _click_composer(cls, hwnd: int) -> None:
        user32 = cls._user32()
        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_WINDOW_RECT_FAILED")
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 600 or height < 400:
            raise ForegroundLaunchError("CHATGPT_DESKTOP_WINDOW_TOO_SMALL")
        x = int(rect.left + width * cls._COMPOSER_X_RATIO)
        y = int(rect.top + height * cls._COMPOSER_Y_RATIO)
        if not user32.SetCursorPos(x, y):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_CURSOR_FAILED")
        user32.mouse_event(cls._MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(cls._MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    @classmethod
    def _set_clipboard_text(cls, text: str) -> None:
        user32 = cls._user32()
        kernel32 = cls._kernel32()

        global_alloc = kernel32.GlobalAlloc
        global_alloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        global_alloc.restype = ctypes.c_void_p
        global_lock = kernel32.GlobalLock
        global_lock.argtypes = [ctypes.c_void_p]
        global_lock.restype = ctypes.c_void_p
        global_unlock = kernel32.GlobalUnlock
        global_unlock.argtypes = [ctypes.c_void_p]
        global_unlock.restype = wintypes.BOOL
        global_free = kernel32.GlobalFree
        global_free.argtypes = [ctypes.c_void_p]
        global_free.restype = ctypes.c_void_p
        set_clipboard_data = user32.SetClipboardData
        set_clipboard_data.argtypes = [wintypes.UINT, ctypes.c_void_p]
        set_clipboard_data.restype = ctypes.c_void_p

        encoded = (text + "\0").encode("utf-16-le")
        if not user32.OpenClipboard(None):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_CLIPBOARD_BUSY")
        handle = None
        try:
            if not user32.EmptyClipboard():
                raise ForegroundLaunchError("CHATGPT_DESKTOP_CLIPBOARD_FAILED")
            handle = global_alloc(cls._GMEM_MOVEABLE, len(encoded))
            if not handle:
                raise ForegroundLaunchError("CHATGPT_DESKTOP_CLIPBOARD_FAILED")
            pointer = global_lock(handle)
            if not pointer:
                raise ForegroundLaunchError("CHATGPT_DESKTOP_CLIPBOARD_FAILED")
            try:
                ctypes.memmove(pointer, encoded, len(encoded))
            finally:
                global_unlock(handle)
            if not set_clipboard_data(cls._CF_UNICODETEXT, handle):
                raise ForegroundLaunchError("CHATGPT_DESKTOP_CLIPBOARD_FAILED")
            handle = None
        finally:
            user32.CloseClipboard()
            if handle:
                global_free(handle)

    @classmethod
    def _get_clipboard_text(cls) -> str:
        user32 = cls._user32()
        kernel32 = cls._kernel32()
        get_clipboard_data = user32.GetClipboardData
        get_clipboard_data.argtypes = [wintypes.UINT]
        get_clipboard_data.restype = ctypes.c_void_p
        global_lock = kernel32.GlobalLock
        global_lock.argtypes = [ctypes.c_void_p]
        global_lock.restype = ctypes.c_void_p
        global_unlock = kernel32.GlobalUnlock
        global_unlock.argtypes = [ctypes.c_void_p]
        global_unlock.restype = wintypes.BOOL

        if not user32.OpenClipboard(None):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_CLIPBOARD_BUSY")
        try:
            handle = get_clipboard_data(cls._CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = global_lock(handle)
            if not pointer:
                raise ForegroundLaunchError("CHATGPT_DESKTOP_CLIPBOARD_FAILED")
            try:
                return ctypes.wstring_at(pointer)
            finally:
                global_unlock(handle)
        finally:
            user32.CloseClipboard()

    @classmethod
    def _verify_pasted_prompt(cls, expected: str) -> None:
        sentinel = f"PF_VERIFY_{uuid4().hex}"
        cls._set_clipboard_text(sentinel)
        cls._send_ctrl_a()
        cls._send_ctrl_c()
        time.sleep(cls._COPY_SETTLE_SECONDS)
        observed = cls._get_clipboard_text()
        normalize = lambda value: str(value).replace("\r\n", "\n").replace("\r", "\n")
        if normalize(observed) != normalize(expected):
            raise ForegroundLaunchError("CHATGPT_DESKTOP_BOOTSTRAP_VERIFY_FAILED")

    @classmethod
    def _send_ctrl_key(cls, vk: int) -> None:
        user32 = cls._user32()
        user32.keybd_event(cls._VK_CONTROL, 0, 0, 0)
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, cls._KEYEVENTF_KEYUP, 0)
        user32.keybd_event(cls._VK_CONTROL, 0, cls._KEYEVENTF_KEYUP, 0)

    @classmethod
    def _send_ctrl_n(cls) -> None:
        cls._send_ctrl_key(cls._VK_N)

    @classmethod
    def _send_ctrl_v(cls) -> None:
        cls._send_ctrl_key(cls._VK_V)

    @classmethod
    def _send_ctrl_a(cls) -> None:
        cls._send_ctrl_key(cls._VK_A)

    @classmethod
    def _send_ctrl_c(cls) -> None:
        cls._send_ctrl_key(cls._VK_C)

    @classmethod
    def _send_escape_once(cls) -> None:
        user32 = cls._user32()
        user32.keybd_event(cls._VK_ESCAPE, 0, 0, 0)
        user32.keybd_event(cls._VK_ESCAPE, 0, cls._KEYEVENTF_KEYUP, 0)

    @classmethod
    def _send_right_once(cls) -> None:
        user32 = cls._user32()
        user32.keybd_event(cls._VK_RIGHT, 0, 0, 0)
        user32.keybd_event(cls._VK_RIGHT, 0, cls._KEYEVENTF_KEYUP, 0)

    @classmethod
    def _send_enter_once(cls) -> None:
        user32 = cls._user32()
        user32.keybd_event(cls._VK_RETURN, 0, 0, 0)
        user32.keybd_event(cls._VK_RETURN, 0, cls._KEYEVENTF_KEYUP, 0)
