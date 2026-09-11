from __future__ import annotations

import atexit
import json
import os
import secrets
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HOST = "127.0.0.1"
PORT = int(os.environ.get("FACTORY_LOCAL_AGENT_PORT", "8765"))
BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FactoryNode" / "local-agent"
LOG_DIR = BASE / "logs"
TOKEN_PATH = BASE / "token.txt"
PID_PATH = BASE / "agent.pid"
MAX_BODY = 8 * 1024 * 1024

BASE.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _token() -> str:
    if TOKEN_PATH.exists():
        value = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    TOKEN_PATH.write_text(value, encoding="utf-8")
    return value


TOKEN = _token()
PID_PATH.write_text(str(os.getpid()), encoding="ascii")


def _cleanup_pid() -> None:
    try:
        if PID_PATH.exists() and PID_PATH.read_text(encoding="ascii").strip() == str(os.getpid()):
            PID_PATH.unlink()
    except OSError:
        pass


atexit.register(_cleanup_pid)

_processes: dict[str, dict[str, Any]] = {}
_process_lock = threading.Lock()


def _shell_argv(shell: str, command: str, argv: list[str] | None = None) -> list[str]:
    shell = (shell or "powershell").lower()
    if shell == "powershell":
        return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    if shell == "cmd":
        return ["cmd.exe", "/d", "/s", "/c", command]
    if shell == "direct":
        if not argv:
            raise ValueError("direct shell requires non-empty argv")
        return [str(x) for x in argv]
    raise ValueError("shell must be powershell, cmd, or direct")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _read_text(path: Path, max_bytes: int = 5 * 1024 * 1024) -> str:
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"file too large for text read: {size} bytes")
    return path.read_text(encoding="utf-8", errors="replace")


class Handler(BaseHTTPRequestHandler):
    server_version = "FactoryLocalAgent/0.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {self.client_address[0]} {fmt % args}\n"
        with (LOG_DIR / "http.log").open("a", encoding="utf-8") as f:
            f.write(line)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Local-Agent-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")

    def _send(self, status: int, value: Any) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        alt = self.headers.get("X-Local-Agent-Token", "")
        return secrets.compare_digest(auth, f"Bearer {TOKEN}") or secrets.compare_digest(alt, TOKEN)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send(401, {"ok": False, "error": "unauthorized"})
        return False

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {
                "ok": True,
                "service": "factory-local-agent",
                "version": "0.2",
                "pid": os.getpid(),
                "host": HOST,
                "port": PORT,
                "mode": "LOCAL_SANDBOX",
            })
            return
        if not self._require_auth():
            return
        if self.path == "/v1/status":
            with _process_lock:
                running = sum(1 for p in _processes.values() if p["process"].poll() is None)
            self._send(200, {
                "ok": True,
                "pid": os.getpid(),
                "base": str(BASE),
                "cwd": os.getcwd(),
                "runningProcesses": running,
            })
            return
        if self.path == "/v1/processes":
            items = []
            with _process_lock:
                for task_id, item in _processes.items():
                    proc = item["process"]
                    items.append({
                        "id": task_id,
                        "pid": proc.pid,
                        "running": proc.poll() is None,
                        "exitCode": proc.poll(),
                        "shell": item["shell"],
                        "command": item["command"],
                        "cwd": item["cwd"],
                        "stdout": item["stdout"],
                        "stderr": item["stderr"],
                        "startedAt": item["startedAt"],
                    })
            self._send(200, {"ok": True, "processes": items})
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        try:
            data = self._body()
            if self.path == "/v1/exec":
                self._exec(data)
            elif self.path == "/v1/process/start":
                self._process_start(data)
            elif self.path == "/v1/process/stop":
                self._process_stop(data)
            elif self.path == "/v1/process/output":
                self._process_output(data)
            elif self.path == "/v1/file/read":
                self._file_read(data)
            elif self.path == "/v1/file/write":
                self._file_write(data)
            elif self.path == "/v1/file/list":
                self._file_list(data)
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except subprocess.TimeoutExpired as exc:
            self._send(408, {"ok": False, "error": "command timeout", "timeout": exc.timeout})
        except Exception as exc:  # local development agent deliberately returns actionable failures
            self._send(400, {"ok": False, "error": str(exc)})

    def _exec(self, data: dict[str, Any]) -> None:
        shell = str(data.get("shell", "powershell"))
        command = str(data.get("command", ""))
        argv = data.get("argv")
        if argv is not None and not isinstance(argv, list):
            raise ValueError("argv must be an array")
        cwd = str(data.get("cwd") or Path.home())
        timeout = int(data.get("timeoutSec", 600))
        args = _shell_argv(shell, command, argv)
        completed = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        self._send(200, {
            "ok": completed.returncode == 0,
            "exitCode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "cwd": cwd,
        })

    def _process_start(self, data: dict[str, Any]) -> None:
        shell = str(data.get("shell", "powershell"))
        command = str(data.get("command", ""))
        argv = data.get("argv")
        if argv is not None and not isinstance(argv, list):
            raise ValueError("argv must be an array")
        cwd = str(data.get("cwd") or Path.home())
        args = _shell_argv(shell, command, argv)
        task_id = uuid.uuid4().hex
        stdout_path = LOG_DIR / f"{task_id}.stdout.log"
        stderr_path = LOG_DIR / f"{task_id}.stderr.log"
        stdout_file = stdout_path.open("ab", buffering=0)
        stderr_file = stderr_path.open("ab", buffering=0)
        try:
            proc = subprocess.Popen(
                args,
                cwd=cwd,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=subprocess.DEVNULL,
                creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
        finally:
            stdout_file.close()
            stderr_file.close()
        with _process_lock:
            _processes[task_id] = {
                "process": proc,
                "shell": shell,
                "command": command if shell != "direct" else " ".join(str(x) for x in (argv or [])),
                "cwd": cwd,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "startedAt": time.time(),
            }
        self._send(200, {"ok": True, "id": task_id, "pid": proc.pid})

    def _process_stop(self, data: dict[str, Any]) -> None:
        task_id = str(data.get("id", ""))
        with _process_lock:
            item = _processes.get(task_id)
        if not item:
            raise ValueError("unknown process id")
        proc = item["process"]
        if proc.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._send(200, {"ok": True, "id": task_id, "pid": proc.pid, "exitCode": proc.poll()})

    def _process_output(self, data: dict[str, Any]) -> None:
        task_id = str(data.get("id", ""))
        max_bytes = int(data.get("maxBytes", 65536))
        with _process_lock:
            item = _processes.get(task_id)
        if not item:
            raise ValueError("unknown process id")

        def tail(path: str) -> str:
            p = Path(path)
            if not p.exists():
                return ""
            raw = p.read_bytes()
            return raw[-max_bytes:].decode("utf-8", errors="replace")

        proc = item["process"]
        self._send(200, {
            "ok": True,
            "id": task_id,
            "pid": proc.pid,
            "running": proc.poll() is None,
            "exitCode": proc.poll(),
            "stdout": tail(item["stdout"]),
            "stderr": tail(item["stderr"]),
        })

    def _file_read(self, data: dict[str, Any]) -> None:
        path = Path(str(data["path"])).expanduser().resolve()
        self._send(200, {"ok": True, "path": str(path), "content": _read_text(path)})

    def _file_write(self, data: dict[str, Any]) -> None:
        path = Path(str(data["path"])).expanduser().resolve()
        content = str(data.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._send(200, {"ok": True, "path": str(path), "bytes": len(content.encode("utf-8"))})

    def _file_list(self, data: dict[str, Any]) -> None:
        path = Path(str(data.get("path") or Path.home())).expanduser().resolve()
        entries = []
        for child in path.iterdir():
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                })
            except OSError:
                continue
        entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
        self._send(200, {"ok": True, "path": str(path), "entries": entries})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    if getattr(__import__("sys"), "stdout", None):
        print(f"Factory Local Agent listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        _cleanup_pid()


if __name__ == "__main__":
    main()
