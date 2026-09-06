from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory_dispatcher import codex_worker
from factory_dispatcher.codex_result import result_schema
from factory_dispatcher.errors import ContractError
from factory_dispatcher.launchers.codex import codex_arguments
from factory_dispatcher.receipts import read_json_object, write_json


@pytest.fixture
def worker_case(tmp_path, attempt_context, structured_result, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    run_directory = (
        tmp_path / "state" / "codex-runs" / attempt_context.dispatchId / attempt_context.attemptId
    )
    run_directory.mkdir(parents=True)
    paths = {
        key: run_directory / name
        for key, name in {
            "promptFile": "prompt.pending.txt",
            "stdoutFile": "stdout.log",
            "stderrFile": "stderr.log",
            "finalOutputFile": "final-message.txt",
            "statusFile": "status.json",
            "outputSchemaFile": "result-schema.json",
        }.items()
    }
    paths["promptFile"].write_text("private prompt", encoding="utf-8")
    write_json(paths["outputSchemaFile"], result_schema(attempt_context))
    arguments = codex_arguments(
        str(tmp_path / "codex.exe"), repository, paths["outputSchemaFile"], paths["finalOutputFile"]
    )
    manifest = {
        "manifestVersion": 2,
        "execution": attempt_context.to_dict(),
        "configFile": str(tmp_path / "config.json"),
        "stagingOnly": False,
        "arguments": arguments,
        "cwd": str(repository),
        **{key: str(value) for key, value in paths.items()},
        "timeoutSeconds": 60,
    }
    manifest_file = run_directory / "launch.json"
    write_json(manifest_file, manifest)
    case = SimpleNamespace(
        repository=repository,
        directory=run_directory,
        file=manifest_file,
        manifest=manifest,
        context=attempt_context,
        paths=paths,
        output=structured_result,
        exit_code=0,
        times_out=False,
        processes=[],
        transport=Mock(return_value={"outcome": "UPLOADED", "receiptDriveId": "receipt-drive-id"}),
    )

    class FinishedProcess:
        def __init__(self, arguments, **kwargs):
            self.pid = 991
            self.returncode = case.exit_code
            self.prompt = ""
            self.arguments = arguments
            self.options = kwargs
            self.terminated = False
            self.timeout = None

        def communicate(self, input=None, timeout=None):
            if input is not None:
                self.prompt = input
                self.timeout = timeout
                if case.times_out:
                    raise subprocess.TimeoutExpired(self.arguments, timeout)
                if case.output is not None:
                    data = case.output
                    if isinstance(data, dict):
                        data = json.dumps(data).encode("utf-8")
                    paths["finalOutputFile"].write_bytes(data)
            return "", ""

        def terminate(self):
            self.terminated = True

    def fake_popen(arguments, **kwargs):
        process = FinishedProcess(arguments, **kwargs)
        case.processes.append(process)
        return process

    monkeypatch.setattr(codex_worker.subprocess, "Popen", fake_popen)
    # An omitted injection must never reach the real OAuth/Drive transport in a test.
    monkeypatch.setattr(codex_worker, "transport_file", case.transport)
    return case


def run(case):
    return codex_worker.run_manifest(case.file, transport=case.transport)


@pytest.mark.parametrize(("exit_code", "times_out"), [(0, False), (7, False), (-15, True)])
def test_codex_worker_records_exit_code_timeout_and_removes_prompt(
    worker_case, exit_code, times_out
):
    case = worker_case
    case.exit_code, case.times_out = exit_code, times_out
    actual_exit_code = run(case)
    status = read_json_object(case.paths["statusFile"])
    assert actual_exit_code == exit_code
    assert status["state"] == ("TIMED_OUT" if times_out else "EXITED")
    assert status["exitCode"] == exit_code
    assert status["timedOut"] is times_out
    assert status["codexPid"] == 991
    assert status["finalOutputFile"] == str(case.paths["finalOutputFile"])
    process = case.processes[0]
    assert process.prompt == "private prompt"
    assert process.terminated is times_out
    assert process.timeout == 60
    assert process.arguments == case.manifest["arguments"]
    assert process.options["shell"] is False
    assert process.options["cwd"] == str(case.repository)
    assert process.options["stdin"] == subprocess.PIPE
    assert process.options["stdout"].name == status["stdoutFile"]
    assert process.options["stderr"].name == status["stderrFile"]
    assert not case.paths["promptFile"].exists()
    receipt = read_json_object(case.directory / "execution-receipt.json")
    assert receipt["status"] == ("SUCCEEDED" if exit_code == 0 else "FAILED_FINAL")
    if times_out:
        assert receipt["error"]["code"] == "CODEX_TIMEOUT"
    elif exit_code:
        assert receipt["error"]["code"] == "CODEX_PROCESS_FAILED"


@pytest.mark.parametrize(
    ("task_status", "receipt_status", "expected_exit"),
    [("SUCCEEDED", "SUCCEEDED", 0), ("BLOCKED", "BLOCKED", 0), ("FAILED", "FAILED_FINAL", 1)],
)
def test_worker_uses_structured_task_status_not_exit_zero(
    worker_case, task_status, receipt_status, expected_exit
):
    case = worker_case
    case.output["status"] = task_status
    if task_status != "SUCCEEDED":
        case.output["error"] = {
            "code": "SOURCE_UNAVAILABLE",
            "detail": "Required source unavailable.",
            "retrySuggested": False,
        }
    assert run(case) == expected_exit
    receipt_path = case.directory / "execution-receipt.json"
    receipt = read_json_object(receipt_path)
    assert receipt["status"] == receipt_status
    assert receipt["dispatchId"] == case.context.dispatchId
    assert receipt["attemptId"] == case.context.attemptId
    assert receipt["leaseToken"] == case.context.leaseToken
    assert receipt["agentId"] == case.context.agentId
    assert receipt["requestDriveId"] == case.context.requestDriveId
    assert receipt["executorStatement"] == case.output["summary"]
    assert receipt["producedArtifacts"] == case.output["producedArtifacts"]
    case.transport.assert_called_once_with(
        receipt_path, case.directory.parents[3] / "config.json", expected=case.context
    )
    status = read_json_object(case.paths["statusFile"])
    assert status["exitCode"] == 0
    assert status["receiptStatus"] == receipt_status
    assert status["transportState"] == "UPLOADED"


@pytest.mark.parametrize(
    "output", [None, b"", b"{broken", b"Task succeeded", b"[]", b"null", b"```json\n{}\n```"]
)
def test_worker_rejects_missing_non_json_and_prose_output(worker_case, output):
    case = worker_case
    case.output = output
    assert run(case) == 1
    receipt = read_json_object(case.directory / "execution-receipt.json")
    assert receipt["status"] == "FAILED_FINAL"
    assert receipt["error"]["code"] == "CODEX_RESULT_INVALID"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schemaVersion", True),
        ("status", "DONE"),
        ("status", {}),
        ("dispatchId", "D-STALE"),
        ("attemptId", "D-UNIT-0001-A002"),
        ("summary", ""),
        ("producedArtifacts", "file.txt"),
        ("error", None),
    ],
)
def test_worker_rejects_invalid_shape_and_mismatched_attempt(worker_case, key, value):
    case = worker_case
    case.output[key] = value
    assert run(case) == 1
    receipt = read_json_object(case.directory / "execution-receipt.json")
    assert receipt["error"]["code"] == "CODEX_RESULT_INVALID"
    assert receipt["dispatchId"] == case.context.dispatchId


def test_worker_persists_receipt_before_upload_and_preserves_it_on_transport_failure(worker_case):
    case = worker_case

    def fail_upload(file, config_file, *, expected):
        receipt = read_json_object(file)
        assert receipt["leaseToken"] == expected.leaseToken
        raise OSError("sensitive upstream error: " + expected.leaseToken)

    case.transport.side_effect = fail_upload
    assert run(case) == 1
    assert (case.directory / "execution-receipt.json").is_file()
    status = read_json_object(case.paths["statusFile"])
    assert status["receiptStatus"] == "SUCCEEDED"
    assert status["transportState"] == "PENDING"
    assert status["transportErrorType"] == "OSError"
    assert case.context.leaseToken not in case.paths["statusFile"].read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "flag", ["--add-dir", "--yolo", "--approve-for-me", "--sandbox=danger-full-access"]
)
def test_worker_rejects_command_changes_before_starting_codex(worker_case, flag):
    case = worker_case
    case.manifest["arguments"].insert(-1, flag)
    write_json(case.file, case.manifest)
    with pytest.raises(ContractError, match="allowed command"):
        run(case)
    assert case.processes == []
    case.transport.assert_not_called()


def test_worker_rejects_staging_only_manifest(worker_case):
    case = worker_case
    case.manifest["stagingOnly"] = True
    write_json(case.file, case.manifest)
    with pytest.raises(ContractError, match="stagingOnly=false"):
        run(case)
    assert case.processes == []


def test_worker_rejects_schema_for_another_attempt(worker_case):
    case = worker_case
    schema = result_schema(case.context)
    schema["properties"]["dispatchId"]["enum"] = ["D-OTHER"]
    write_json(case.paths["outputSchemaFile"], schema)
    with pytest.raises(ContractError, match="schema"):
        run(case)
    assert case.processes == []


def test_worker_does_not_rerun_an_attempt_that_has_a_receipt(worker_case):
    case = worker_case
    assert run(case) == 0
    original = (case.directory / "execution-receipt.json").read_bytes()
    with pytest.raises(ContractError, match="already has execution output"):
        run(case)
    assert len(case.processes) == 1
    assert (case.directory / "execution-receipt.json").read_bytes() == original
