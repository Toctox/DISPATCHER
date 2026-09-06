"""Explicit human starter for a long-lived Factory browser. Never used by dispatches."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from .chatgpt_browser import (
    ChatGPTError,
    check_environment,
    discover_browser,
    profile_lock,
    resolve_browser,
    tab_lock,
)
from .config import load_local_settings
from .receipts import read_json_object, write_json


def port_in_use(endpoint):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", urlsplit(endpoint).port))
        except OSError:
            return True
    return False


def process_alive(pid):
    if type(pid) is not int or pid <= 0:
        return True  # Unknown startup evidence must not authorize a second process.
    if os.name == "nt":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such PID
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # POSIX existence probe only; NEVER used on Windows.
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def start_browser(
    settings,
    *,
    discover=discover_browser,
    occupied=port_in_use,
    popen_factory=subprocess.Popen,
    alive=process_alive,
    monotonic=time.monotonic,
    sleep=time.sleep,
):
    endpoint = check_environment(settings, setup=True)
    with tab_lock(settings):
        try:
            discover(endpoint)
        except ChatGPTError:
            if occupied(endpoint):
                raise ChatGPTError("CHATGPT_CDP_PORT_OCCUPIED") from None
        else:
            return {"outcome": "BROWSER_ALREADY_RUNNING", "endpoint": endpoint}
        executable = resolve_browser(settings)
        with profile_lock(settings) as profile:
            record_path = profile / ".factory-browser-start.json"
            if record_path.exists():
                previous = read_json_object(record_path)
                if alive(previous.get("pid")):
                    raise ChatGPTError("CHATGPT_BROWSER_START_UNCERTAIN")
            write_json(record_path, {"state": "STARTING", "endpoint": endpoint})
            arguments = [
                str(executable),
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={urlsplit(endpoint).port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://chatgpt.com/",
            ]
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            process = popen_factory(
                arguments,
                cwd=str(settings.project_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=flags,
                close_fds=True,
            )
            write_json(
                record_path,
                {
                    "state": "STARTING",
                    "endpoint": endpoint,
                    "pid": int(process.pid),
                },
            )
            deadline = monotonic() + 15
            while True:
                try:
                    discover(endpoint)
                except ChatGPTError:
                    if process.poll() is not None or monotonic() >= deadline:
                        # Never kill/relaunch a browser after ambiguous startup.
                        raise ChatGPTError("CHATGPT_BROWSER_START_UNCERTAIN") from None
                    sleep(0.25)
                else:
                    write_json(
                        record_path,
                        {
                            "state": "RUNNING",
                            "endpoint": endpoint,
                            "pid": int(process.pid),
                        },
                    )
                    return {
                        "outcome": "BROWSER_STARTED",
                        "endpoint": endpoint,
                        "pid": int(process.pid),
                    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Start or reuse a dedicated, long-lived CDP browser."
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args(argv)
    try:
        result = start_browser(load_local_settings(args.config))
    except Exception as exc:
        code = exc.code if isinstance(exc, ChatGPTError) else "CHATGPT_BROWSER_START_FAILED"
        print(json.dumps({"outcome": code}))
        return 1
    print(json.dumps(result))
    print("Faça login manualmente e deixe exatamente UMA aba ChatGPT aberta. Não feche o browser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
