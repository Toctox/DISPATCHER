from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .codex_result import materialize_receipt, result_schema
from .errors import ContractError
from .launchers.codex import codex_arguments
from .mutex import LocalMutex
from .receipt_transport import transport_file
from .receipts import AttemptContext, read_json_object, required_string, write_json


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _absolute(raw: Any, field: str) -> Path:
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        raise ContractError(f"{field} requires an absolute path")
    return Path(raw).resolve()


def run_manifest(
    manifest_path: Path, *, transport: Callable[..., dict[str, Any]] | None = None
) -> int:
    manifest_path = manifest_path.resolve()
    with LocalMutex(manifest_path.parent / "worker.lock"):
        return _run_manifest(manifest_path, transport=transport or transport_file)


def _run_manifest(manifest_path: Path, *, transport: Callable[..., dict[str, Any]]) -> int:
    run_directory = manifest_path.parent
    manifest = read_json_object(manifest_path)
    if manifest.get("manifestVersion") != 2:
        raise ContractError("Worker requires manifestVersion=2 with receipt transport context")
    context = AttemptContext.from_mapping(manifest.get("execution"))
    if context.agentId != "CODEX_IMPLEMENTER":
        raise ContractError("Worker context requires CODEX_IMPLEMENTER")
    if run_directory.name != context.attemptId or run_directory.parent.name != context.dispatchId:
        raise ContractError("Worker directory does not match the execution attempt")
    if manifest.get("stagingOnly") is not False:
        raise ContractError("Worker workspace-write requires stagingOnly=false")
    cwd = _absolute(manifest.get("cwd"), "cwd")
    config_file = _absolute(manifest.get("configFile"), "configFile")
    if not cwd.is_dir() or run_directory.is_relative_to(cwd) or config_file.is_relative_to(cwd):
        raise ContractError(
            "Worker control files and OAuth configuration must be outside the repository"
        )

    files = {}
    for key, name in {
        "promptFile": "prompt.pending.txt",
        "stdoutFile": "stdout.log",
        "stderrFile": "stderr.log",
        "statusFile": "status.json",
        "finalOutputFile": "final-message.txt",
        "outputSchemaFile": "result-schema.json",
    }.items():
        files[key] = _absolute(manifest.get(key), key)
        if files[key] != run_directory / name:
            raise ContractError(f"{key} must be the designated file inside the attempt directory")
    schema = read_json_object(files["outputSchemaFile"])
    if schema != result_schema(context):
        raise ContractError("Output schema does not match the current attempt")
    arguments = manifest.get("arguments")
    if not isinstance(arguments, list) or not arguments or not isinstance(arguments[0], str):
        raise ContractError("Worker requires explicit Codex arguments")
    executable = _absolute(arguments[0], "Codex executable")
    expected_arguments = codex_arguments(
        str(executable), cwd, files["outputSchemaFile"], files["finalOutputFile"]
    )
    if arguments != expected_arguments:
        raise ContractError("Codex arguments differ from the allowed command")
    timeout_seconds = manifest.get("timeoutSeconds")
    if type(timeout_seconds) is not int or not 60 <= timeout_seconds <= 14_400:
        raise ContractError("Codex timeout must be between 60 and 14400 seconds")

    receipt_path = run_directory / "execution-receipt.json"
    if any(path.exists() for path in (receipt_path, files["statusFile"], files["finalOutputFile"])):
        raise ContractError(
            "Attempt already has execution output; use receipt recovery without rerunning"
        )
    prompt = files["promptFile"].read_text(encoding="utf-8-sig")
    files["promptFile"].unlink()
    started_at = _now()
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    timed_out = False
    process = None
    process_error = None
    exit_code = 1
    with (
        files["stdoutFile"].open("w", encoding="utf-8") as stdout_handle,
        files["stderrFile"].open("w", encoding="utf-8") as stderr_handle,
    ):
        try:
            process = subprocess.Popen(
                arguments,
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                shell=False,
                creationflags=creationflags,
            )
            write_json(
                files["statusFile"],
                {
                    "state": "RUNNING",
                    "workerPid": os.getpid(),
                    "codexPid": process.pid,
                    "startedAt": started_at,
                    "timeoutSeconds": timeout_seconds,
                },
            )
            try:
                process.communicate(input=prompt, timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.terminate()
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            exit_code = int(process.returncode)
        except OSError as exc:
            process_error = type(exc).__name__
            if process is not None:
                process.kill()
                process.communicate()
    finished_at = _now()
    receipt = materialize_receipt(
        context,
        files["finalOutputFile"],
        exit_code=exit_code,
        timed_out=timed_out,
        started_at=started_at,
        finished_at=finished_at,
    )
    write_json(receipt_path, receipt, exclusive=True)
    status = {
        "state": "TIMED_OUT" if timed_out else "EXITED",
        "workerPid": os.getpid(),
        "codexPid": process.pid if process is not None else None,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "finalOutputFile": str(files["finalOutputFile"]),
        "stdoutFile": str(files["stdoutFile"]),
        "stderrFile": str(files["stderrFile"]),
        "receiptFile": str(receipt_path),
        "receiptStatus": receipt["status"],
        "receiptErrorCode": receipt["error"].get("code"),
        "transportState": "PENDING",
    }
    if process_error:
        status["processErrorType"] = process_error
    write_json(files["statusFile"], status)
    try:
        sent = transport(receipt_path, config_file, expected=context)
        if sent.get("outcome") not in {"UPLOADED", "ALREADY_UPLOADED"}:
            raise ContractError("Receipt transport did not acknowledge upload")
        status["transportState"] = sent["outcome"]
        status["receiptDriveId"] = required_string(sent, "receiptDriveId")
    except Exception as exc:
        status["transportErrorType"] = type(exc).__name__
        # The local receipt remains available for explicit recovery.
    write_json(files["statusFile"], status)
    failed = receipt["status"] == "FAILED_FINAL" or status["transportState"] == "PENDING"
    return exit_code or (1 if failed else 0)


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 1:
        print("usage: python -m factory_dispatcher.codex_worker <launch.json>", file=sys.stderr)
        return 2
    manifest_path = Path(values[0])
    status_path = manifest_path.resolve().parent / "status.json"
    try:
        return run_manifest(manifest_path)
    except Exception as exc:
        if not status_path.exists():
            try:
                write_json(
                    status_path,
                    {
                        "state": "WORKER_ERROR",
                        "workerPid": os.getpid(),
                        "errorType": type(exc).__name__,
                        "finishedAt": _now(),
                    },
                )
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
