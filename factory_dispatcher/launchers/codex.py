from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..codex_prompt import build_codex_prompt, require_workspace_write
from ..codex_result import result_schema
from ..errors import ContractError, LaunchGuardError
from ..models import DispatchJob, DispatchRequest, ExecutorType, normalize_change_id
from ..receipts import AttemptContext, write_json
from .base import LaunchResult

FORBIDDEN_CODEX_FLAGS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "--full-auto",
    "--add-dir",
}


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LaunchGuardError(f"Codex request requires non-empty {key}")
    return value.strip()


def _normalize_repository(value: str) -> str:
    result = value.strip().replace("\\", "/").removesuffix(".git").rstrip("/")
    result = re.sub(r"^[a-z]+://", "", result, flags=re.IGNORECASE)
    result = re.sub(r"^[^@/]+@([^:]+):", r"\1/", result)
    if result.casefold().startswith("github.com/"):
        result = result[len("github.com/") :]
    return result.casefold()


def codex_arguments(
    executable: str, repository_path: Path, schema_path: Path, final_output: Path
) -> list[str]:
    return [
        executable,
        "exec",
        "--sandbox",
        "workspace-write",
        "--ephemeral",
        "--color",
        "never",
        "--cd",
        str(repository_path),
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(final_output),
        "-",
    ]


class CodexLauncher:
    def __init__(
        self,
        executable: str,
        state_directory: Path,
        *,
        enabled: bool,
        config_file: Path | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        run_factory: Callable[..., Any] = subprocess.run,
        executable_resolver: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.executable = executable
        self.state_directory = state_directory
        self.enabled = enabled
        self.config_file = (
            config_file or Path(__file__).resolve().parents[2] / "config.json"
        ).resolve()
        self.popen_factory = popen_factory
        self.run_factory = run_factory
        self.executable_resolver = executable_resolver

    def _git(self, repository_path: Path, *arguments: str) -> str:
        completed = self.run_factory(
            ["git", "-C", str(repository_path), *arguments],
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
        return str(completed.stdout).strip()

    def _validate(self, job: DispatchJob, request: DispatchRequest) -> tuple[Path, int]:
        raw = request.raw
        if request.executor_type != ExecutorType.CODEX:
            raise LaunchGuardError("CodexLauncher requires executorType=CODEX")
        if not self.enabled:
            raise LaunchGuardError("Codex launch is disabled in local config")
        require_workspace_write(request)

        codex_execution = raw.get("codexExecution")
        if not isinstance(codex_execution, dict):
            raise LaunchGuardError("Codex request requires codexExecution object")
        if codex_execution.get("implementationEligible") is not True:
            raise LaunchGuardError("codexExecution.implementationEligible must be the boolean true")
        if _required_text(raw, "agentId") != "CODEX_IMPLEMENTER":
            raise LaunchGuardError("Codex agentId must be CODEX_IMPLEMENTER")
        if job.agent_id != "CODEX_IMPLEMENTER":
            raise LaunchGuardError("QUEUE agentId must be CODEX_IMPLEMENTER")
        if _required_text(raw, "dispatchId") != job.dispatch_id:
            raise LaunchGuardError("request dispatchId does not match QUEUE")
        try:
            request_change_id = request.change_id
            queue_change_id = normalize_change_id(job.change_id)
        except ContractError as exc:
            raise LaunchGuardError(str(exc)) from exc
        if request_change_id != queue_change_id:
            raise LaunchGuardError("request changeId does not match QUEUE")

        repository = _required_text(codex_execution, "repository")
        repository_text = _required_text(codex_execution, "repositoryPath")
        base_branch = _required_text(codex_execution, "baseBranch")
        base_sha = _required_text(codex_execution, "baseSha")
        _required_text(codex_execution, "specArtifact")
        if not any(
            isinstance(codex_execution.get(key), str) and codex_execution[key].strip()
            for key in ("specVersion", "specIdentity")
        ):
            raise LaunchGuardError(
                "Codex request requires non-empty codexExecution.specVersion or specIdentity"
            )
        causal_decision = codex_execution.get("causalDecision")
        if not (
            isinstance(causal_decision, str)
            and causal_decision.strip()
            or isinstance(causal_decision, dict)
            and causal_decision
        ):
            raise LaunchGuardError("Codex request requires non-empty codexExecution.causalDecision")

        timeout_minutes = raw.get("maxExecutionMinutes")
        if type(timeout_minutes) is not int:
            raise LaunchGuardError("Codex request requires integer maxExecutionMinutes")
        if not 1 <= timeout_minutes <= 240:
            raise LaunchGuardError("maxExecutionMinutes must be between 1 and 240")

        repository_path = Path(repository_text).expanduser()
        if not repository_path.is_absolute():
            raise LaunchGuardError("repositoryPath must be absolute")
        repository_path = repository_path.resolve()
        if not repository_path.is_dir():
            raise LaunchGuardError("repositoryPath is not an existing directory")
        try:
            top_level = Path(self._git(repository_path, "rev-parse", "--show-toplevel")).resolve()
            head_sha = self._git(repository_path, "rev-parse", "HEAD")
            branch = self._git(repository_path, "rev-parse", "--abbrev-ref", "HEAD")
        except (OSError, subprocess.CalledProcessError) as exc:
            raise LaunchGuardError("repository identity could not be verified by git") from exc
        if top_level != repository_path:
            raise LaunchGuardError("repositoryPath must be the exact git top-level directory")
        if head_sha.casefold() != base_sha.casefold():
            raise LaunchGuardError("HEAD does not match exact baseSha")
        if branch != base_branch:
            raise LaunchGuardError("current branch does not match exact baseBranch")

        if repository.startswith("LOCAL/"):
            expected_name = repository.removeprefix("LOCAL/")
            if not expected_name or "/" in expected_name or "\\" in expected_name:
                raise LaunchGuardError(
                    "LOCAL repository identity requires a single repository name"
                )
            actual_name = repository_path.name
            if os.name == "nt":
                actual_name, expected_name = actual_name.casefold(), expected_name.casefold()
            if actual_name != expected_name:
                raise LaunchGuardError("local repository path does not match repository identity")
        else:
            try:
                remote = self._git(repository_path, "remote", "get-url", "origin")
            except (OSError, subprocess.CalledProcessError) as exc:
                raise LaunchGuardError("origin remote could not be verified") from exc
            if _normalize_repository(remote) != _normalize_repository(repository):
                raise LaunchGuardError("origin remote does not match exact repository identity")
        return repository_path, timeout_minutes

    def _resolve_executable(self) -> str:
        configured = Path(self.executable).expanduser()
        if configured.is_absolute():
            if not configured.is_file():
                raise LaunchGuardError(f"configured Codex executable was not found: {configured}")
            return str(configured.resolve())
        resolved = self.executable_resolver(self.executable)
        if not resolved:
            raise LaunchGuardError(f"Codex executable was not found on PATH: {self.executable}")
        return str(Path(resolved).resolve())

    def launch(
        self,
        job: DispatchJob,
        request: DispatchRequest,
        prompt: str,
        *,
        receipt_folder_id: str | None = None,
    ) -> LaunchResult:
        repository_path, timeout_minutes = self._validate(job, request)
        executable = self._resolve_executable()
        context = AttemptContext.from_job(job, receipt_folder_id)
        output_directory = self.state_directory / "codex-runs" / job.dispatch_id / job.attempt_id
        output_directory = output_directory.resolve()
        if output_directory.is_relative_to(repository_path):
            raise LaunchGuardError("Worker control files must be outside the Codex repository")
        output_directory.mkdir(parents=True, exist_ok=False)
        final_output = output_directory / "final-message.txt"
        schema_path = output_directory / "result-schema.json"
        stdout_path = output_directory / "stdout.log"
        stderr_path = output_directory / "stderr.log"
        prompt_path = output_directory / "prompt.pending.txt"
        status_path = output_directory / "status.json"
        manifest_path = output_directory / "launch.json"
        arguments = codex_arguments(executable, repository_path, schema_path, final_output)
        if any(flag in arguments for flag in FORBIDDEN_CODEX_FLAGS):
            raise LaunchGuardError("internal safety check found a forbidden Codex flag")
        prompt = build_codex_prompt(job, request, job.staging_folder_id)
        prompt_path.write_text(prompt, encoding="utf-8")
        write_json(schema_path, result_schema(context), exclusive=True)
        try:
            os.chmod(prompt_path, 0o600)
        except OSError:
            pass
        manifest = {
            "manifestVersion": 2,
            "execution": context.to_dict(),
            "configFile": str(self.config_file),
            "stagingOnly": False,
            "arguments": arguments,
            "cwd": str(repository_path),
            "promptFile": str(prompt_path),
            "stdoutFile": str(stdout_path),
            "stderrFile": str(stderr_path),
            "finalOutputFile": str(final_output),
            "outputSchemaFile": str(schema_path),
            "statusFile": str(status_path),
            "timeoutSeconds": timeout_minutes * 60,
        }
        write_json(manifest_path, manifest, exclusive=True)
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:
            process = self.popen_factory(
                [
                    sys.executable,
                    "-m",
                    "factory_dispatcher.codex_worker",
                    str(manifest_path),
                ],
                cwd=str(Path(__file__).resolve().parents[2]),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                creationflags=creationflags,
            )
        except Exception:
            prompt_path.unlink(missing_ok=True)
            raise
        return LaunchResult(
            launcher="CODEX_EXEC_WORKER",
            pid=int(process.pid),
            detail=f"status: {status_path}; final output: {final_output}",
        )
