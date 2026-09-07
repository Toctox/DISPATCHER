from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

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
    only operation is: activate the ChatGPT desktop window, focus the composer at a
    fixed window-relative point, paste the canonical Factory bootstrap, and press
    Enter exactly once.
    """

    requires_reconciliation = True
    launcher_name = "FOREGROUND_CHATGPT_DESKTOP"

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SW_RESTORE = 9
    _SW_MAXIMIZE = 3
    _CF_UNICODETEXT = 13
    _GMEM_MOVEABLE = 0x0002
    _VK_CONTROL = 0x11
    _VK_V = 0x56
    _VK_RETURN = 0x0D
    _VK_MENU = 0x12
    _KEYEVENTF_KEYUP = 0x0002
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004

    # New-chat/home composer in the desktop app. Relative coordinates make the
    # smoke independent of screen resolution while still keeping the operation fixed.
    _COMPOSER_X_RATIO = 0.50
    _COMPOSER_Y_RATIO = 0.67

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

        self._activate_and_maximize(hwnd)
        time.sleep(0.8)
        self._click_composer(hwnd)
        time.sleep(0.25)
        self._set_clipboard_text(prompt)
        self._send_ctrl_v()
        time.sleep(0.35)
        self._send_enter_once()

        # After Enter, completion is established only by the canonical receipt. If
        # the receipt never appears, RECONCILE_BEFORE_RETRY prevents blind resend.
        return LaunchResult(
            launcher=self.launcher_name,
            pid=self._window_pid(hwnd),
            detail="foreground paste and single Enter attempted",
        )

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
        query_name.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
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
    def _activate_and_maximize(cls, hwnd: int) -> None:
        user32 = cls._user32()
        user32.ShowWindow(wintypes.HWND(hwnd), cls._SW_RESTORE)
        user32.ShowWindow(wintypes.HWND(hwnd), cls._SW_MAXIMIZE)
        # A benign Alt press improves SetForegroundWindow reliability when the
        # Dispatcher itself is a background process.
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
            handle = None  # clipboard owns the allocation now
        finally:
            user32.CloseClipboard()
            if handle:
                global_free(handle)

    @classmethod
    def _send_ctrl_v(cls) -> None:
        user32 = cls._user32()
        user32.keybd_event(cls._VK_CONTROL, 0, 0, 0)
        user32.keybd_event(cls._VK_V, 0, 0, 0)
        user32.keybd_event(cls._VK_V, 0, cls._KEYEVENTF_KEYUP, 0)
        user32.keybd_event(cls._VK_CONTROL, 0, cls._KEYEVENTF_KEYUP, 0)

    @classmethod
    def _send_enter_once(cls) -> None:
        user32 = cls._user32()
        user32.keybd_event(cls._VK_RETURN, 0, 0, 0)
        user32.keybd_event(cls._VK_RETURN, 0, cls._KEYEVENTF_KEYUP, 0)
