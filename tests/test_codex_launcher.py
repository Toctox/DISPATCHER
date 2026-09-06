from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import queue_row

from factory_dispatcher.bootstrap import build_bootstrap
from factory_dispatcher.errors import ContractError, LaunchGuardError
from factory_dispatcher.launchers.codex import FORBIDDEN_CODEX_FLAGS, CodexLauncher
from factory_dispatcher.models import DispatchJob, DispatchRequest


class FakeGit:
    def __init__(self, repository: Path) -> None:
        self.top_level = str(repository)
        self.head = "a" * 40
        self.branch = "main"
        self.origin = "https://github.com/Toctox/example.git"
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        assert kwargs["shell"] is False
        assert kwargs["check"] is True
        self.calls.append(command)
        arguments = command[3:]
        if arguments == ["rev-parse", "--show-toplevel"]:
            output = self.top_level
        elif arguments == ["rev-parse", "HEAD"]:
            output = self.head
        elif arguments == ["rev-parse", "--abbrev-ref", "HEAD"]:
            output = self.branch
        elif arguments == ["remote", "get-url", "origin"]:
            if self.origin is None:
                raise subprocess.CalledProcessError(2, command, stderr="No such remote 'origin'")
            output = self.origin
        else:
            raise AssertionError(command)
        return SimpleNamespace(stdout=output + "\n")


@pytest.fixture
def launch_case(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    git = FakeGit(repository)
    process_factory = Mock(return_value=SimpleNamespace(pid=789))
    resolver = Mock(return_value=str(tmp_path / "codex.exe"))
    launcher = CodexLauncher(
        "codex",
        tmp_path / "state",
        enabled=True,
        config_file=tmp_path / "config.json",
        popen_factory=process_factory,
        run_factory=git,
        executable_resolver=resolver,
    )
    request = DispatchRequest(
        {
            "schemaVersion": "2",
            "artifactType": "DISPATCH_REQUEST",
            "executorType": "CODEX",
            "agentId": "CODEX_IMPLEMENTER",
            "dispatchId": "D-TEST-0001",
            "changeId": "CR-0004",
            "maxExecutionMinutes": 45,
            "instructions": "Implement only the approved specification.",
            "allowedWrites": {"stagingOnly": False},
            "codexExecution": {
                "implementationEligible": True,
                "repository": "Toctox/example",
                "repositoryPath": str(repository),
                "baseBranch": "main",
                "baseSha": "a" * 40,
                "specArtifact": "spec-drive-id",
                "specIdentity": "SPEC-001-v2",
                "causalDecision": "DECISION-001",
            },
        }
    )
    job = DispatchJob.from_row(
        2,
        queue_row(
            agentId="CODEX_IMPLEMENTER",
            changeId="CR-0004",
            status="CLAIMED",
            attempt=1,
            leaseOwner="TEST-WORKER",
            leaseToken="lease",
        ),
    )
    return SimpleNamespace(
        launcher=launcher,
        request=request,
        job=job,
        repository=repository,
        git=git,
        process_factory=process_factory,
        resolver=resolver,
    )


def launch(case):
    return case.launcher.launch(
        case.job, case.request, "obsolete chat bootstrap", receipt_folder_id="receipts-root"
    )


def assert_rejected(case, message, error_type=LaunchGuardError):
    with pytest.raises(error_type, match=message):
        launch(case)
    case.process_factory.assert_not_called()
    assert not case.launcher.state_directory.exists()


def make_local(case):
    case.request.raw["codexExecution"]["repository"] = "LOCAL/" + case.repository.name
    case.git.origin = None


@pytest.mark.parametrize("local", [False, True], ids=["remote", "local"])
def test_nested_v2_eligibility_true_launches_with_same_worker_contract(launch_case, local):
    case = launch_case
    if local:
        make_local(case)

    result = launch(case)

    case.process_factory.assert_called_once()
    worker_arguments = case.process_factory.call_args.args[0]
    worker_options = case.process_factory.call_args.kwargs
    manifest = json.loads(Path(worker_arguments[-1]).read_text(encoding="utf-8"))
    arguments = manifest["arguments"]
    assert result.pid == 789
    assert result.launcher == "CODEX_EXEC_WORKER"
    assert worker_arguments[1:3] == ["-m", "factory_dispatcher.codex_worker"]
    assert arguments == [
        str(Path(case.resolver.return_value).resolve()),
        "exec",
        "--sandbox",
        "workspace-write",
        "--ephemeral",
        "--color",
        "never",
        "--cd",
        str(case.repository.resolve()),
        "--output-schema",
        manifest["outputSchemaFile"],
        "--output-last-message",
        manifest["finalOutputFile"],
        "-",
    ]
    assert not FORBIDDEN_CODEX_FLAGS.intersection(arguments)
    assert worker_options["shell"] is False
    assert Path(manifest["cwd"]) == case.repository.resolve()
    assert manifest["timeoutSeconds"] == 2700
    prompt = Path(manifest["promptFile"]).read_text(encoding="utf-8")
    assert "FACTORY_CODEX_EXECUTION_V1" in prompt
    assert "obsolete chat bootstrap" not in prompt
    assert manifest["execution"]["leaseToken"] == case.job.lease_token
    assert manifest["execution"]["receiptFolderId"] == "receipts-root"
    assert any(command[3:] == ["remote", "get-url", "origin"] for command in case.git.calls) is (
        not local
    )


def test_missing_codex_execution_is_rejected_even_with_all_legacy_root_fields(launch_case):
    case = launch_case
    case.request.raw.update(case.request.raw.pop("codexExecution"))
    assert_rejected(case, "codexExecution object")


@pytest.mark.parametrize("value", [None, [], "", "object", True, 1])
def test_codex_execution_must_be_an_object(launch_case, value):
    launch_case.request.raw["codexExecution"] = value
    assert_rejected(launch_case, "codexExecution object")


def test_empty_codex_execution_does_not_fall_back_to_root(launch_case):
    case = launch_case
    case.request.raw.update(case.request.raw["codexExecution"])
    case.request.raw["codexExecution"] = {}
    assert_rejected(case, "implementationEligible")


def test_missing_implementation_eligibility_is_rejected(launch_case):
    del launch_case.request.raw["codexExecution"]["implementationEligible"]
    assert_rejected(launch_case, "implementationEligible")


@pytest.mark.parametrize("value", ["true", 1, 1.0, False, None, "", [], {}])
def test_implementation_eligibility_must_be_boolean_true(launch_case, value):
    launch_case.request.raw["codexExecution"]["implementationEligible"] = value
    assert_rejected(launch_case, "implementationEligible")


def test_root_eligibility_cannot_override_nested_false(launch_case):
    launch_case.request.raw["implementationEligible"] = True
    launch_case.request.raw["codexExecution"]["implementationEligible"] = False
    assert_rejected(launch_case, "implementationEligible")


def test_nested_fields_are_authoritative_over_conflicting_legacy_root_fields(launch_case):
    case = launch_case
    case.request.raw.update(dict.fromkeys(case.request.raw["codexExecution"], "wrong-root-value"))
    assert launch(case).pid == 789


@pytest.mark.parametrize(
    "field",
    ["repository", "repositoryPath", "baseBranch", "baseSha", "specArtifact"],
)
@pytest.mark.parametrize("value", [None, "", "   ", 1, False, {}])
def test_codex_identity_fields_require_nonempty_text(launch_case, field, value):
    launch_case.request.raw["codexExecution"][field] = value
    assert_rejected(launch_case, field)


@pytest.mark.parametrize(
    "field",
    [
        "implementationEligible",
        "repository",
        "repositoryPath",
        "baseBranch",
        "baseSha",
        "specArtifact",
        "specIdentity",
        "causalDecision",
    ],
)
def test_field_present_only_at_root_cannot_satisfy_v2(launch_case, field):
    case = launch_case
    case.request.raw[field] = case.request.raw["codexExecution"].pop(field)
    assert_rejected(case, field)


@pytest.mark.parametrize("identity_field", ["specIdentity", "specVersion"])
def test_either_nested_spec_identity_or_version_satisfies_guard(launch_case, identity_field):
    execution = launch_case.request.raw["codexExecution"]
    execution.pop("specIdentity")
    execution[identity_field] = "SPEC-001-v2"
    assert launch(launch_case).pid == 789


@pytest.mark.parametrize("value", [None, "", "  ", {}, [], 1, False])
def test_empty_or_invalid_spec_identity_and_version_are_rejected(launch_case, value):
    execution = launch_case.request.raw["codexExecution"]
    execution["specIdentity"] = value
    execution["specVersion"] = value
    assert_rejected(launch_case, "specVersion or specIdentity")


@pytest.mark.parametrize("value", [None, "", "  ", {}, [], 0, True])
def test_causal_decision_must_be_present_and_nonempty(launch_case, value):
    launch_case.request.raw["codexExecution"]["causalDecision"] = value
    assert_rejected(launch_case, "causalDecision")


def test_nonempty_causal_decision_object_remains_supported(launch_case):
    launch_case.request.raw["codexExecution"]["causalDecision"] = {"id": "DECISION-001"}
    assert launch(launch_case).pid == 789


@pytest.mark.parametrize("queue_value", [None, "", "   "])
def test_null_change_id_matches_absence_in_queue_for_local_smoke(launch_case, queue_value):
    case = launch_case
    make_local(case)
    case.request.raw["changeId"] = None
    case.job = DispatchJob.from_row(
        2,
        queue_row(
            agentId="CODEX_IMPLEMENTER",
            changeId=queue_value,
            attempt=1,
            status="CLAIMED",
            leaseToken="lease",
        ),
    )

    assert case.job.change_id == ""
    assert launch(case).pid == 789


@pytest.mark.parametrize(
    ("request_id", "queue_id"),
    [
        ("CR-OTHER", "CR-0004"),
        ("CR-0004", ""),
        ("CR-0004", None),
        (None, "CR-0004"),
        ("", "CR-0004"),
        ("cr-0004", "CR-0004"),
    ],
)
def test_mismatched_change_ids_fail_closed(launch_case, request_id, queue_id):
    case = launch_case
    case.request.raw["changeId"] = request_id
    case.job = replace(case.job, change_id=queue_id)
    assert_rejected(case, "changeId")


@pytest.mark.parametrize("value", [False, 0, 1, [], {}])
def test_invalid_request_change_id_is_not_coerced_to_absence(launch_case, value):
    case = launch_case
    case.request.raw["changeId"] = value
    case.job = replace(case.job, change_id="")
    assert_rejected(case, "changeId must be a string or null")


@pytest.mark.parametrize("repository", ["LOCAL/wrong-name", "LOCAL/", "LOCAL/group/repo"])
def test_local_name_must_match_exact_repository_directory(launch_case, repository):
    case = launch_case
    case.request.raw["codexExecution"]["repository"] = repository
    case.git.origin = None
    assert_rejected(case, "repository (identity|path)")


@pytest.mark.skipif(os.name != "nt", reason="Windows allows case-insensitive directory names")
def test_local_directory_name_is_case_insensitive_on_windows(launch_case):
    case = launch_case
    make_local(case)
    case.request.raw["codexExecution"]["repository"] = "LOCAL/" + case.repository.name.upper()
    assert launch(case).pid == 789


@pytest.mark.parametrize("repository", ["Toctox/example", "local/repo", "Local/repo"])
def test_non_local_or_non_exact_local_prefix_requires_origin(launch_case, repository):
    case = launch_case
    case.request.raw["codexExecution"]["repository"] = repository
    case.git.origin = None
    assert_rejected(case, "origin remote could not be verified")


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/Other/example.git",
        "https://github.com/Toctox/another.git",
        "https://example.com/Toctox/example.git",
    ],
)
def test_remote_origin_must_match_repository_identity(launch_case, remote):
    launch_case.git.origin = remote
    assert_rejected(launch_case, "origin remote does not match")


@pytest.mark.parametrize(
    "remote",
    ["https://github.com/Toctox/example.git", "git@github.com:Toctox/example.git"],
)
def test_correct_remote_origin_is_preserved(launch_case, remote):
    launch_case.git.origin = remote
    assert launch(launch_case).pid == 789


@pytest.mark.parametrize("local", [False, True], ids=["remote", "local"])
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("head", "b" * 40, "baseSha"),
        ("branch", "other-branch", "baseBranch"),
        ("top_level", "parent", "top-level"),
    ],
)
def test_git_identity_guards_apply_to_local_and_remote_repositories(
    launch_case, local, field, value, message
):
    case = launch_case
    if local:
        make_local(case)
    if field == "top_level":
        value = str(case.repository.parent)
    setattr(case.git, field, value)
    assert_rejected(case, message)


def test_git_failure_is_rejected_before_starting_worker(launch_case):
    launch_case.launcher.run_factory = Mock(side_effect=OSError("git unavailable"))
    assert_rejected(launch_case, "repository identity could not be verified")


@pytest.mark.parametrize("kind", ["relative", "missing", "file"])
def test_repository_path_must_be_existing_absolute_directory(launch_case, kind):
    case = launch_case
    if kind == "relative":
        path = "relative/repo"
        message = "absolute"
    elif kind == "missing":
        path = str(case.repository / "missing")
        message = "existing directory"
    else:
        file_path = case.repository / "not-a-directory"
        file_path.write_text("fixture", encoding="utf-8")
        path = str(file_path)
        message = "existing directory"
    case.request.raw["codexExecution"]["repositoryPath"] = path
    assert_rejected(case, message)


@pytest.mark.parametrize("value", [None, True, False, 0, 241, -1, 1.5, "45", "invalid", [], {}])
def test_invalid_execution_timeout_is_rejected(launch_case, value):
    launch_case.request.raw["maxExecutionMinutes"] = value
    assert_rejected(launch_case, "maxExecutionMinutes")


def test_timeout_cannot_move_to_codex_execution(launch_case):
    case = launch_case
    case.request.raw["codexExecution"]["maxExecutionMinutes"] = case.request.raw.pop(
        "maxExecutionMinutes"
    )
    assert_rejected(case, "maxExecutionMinutes")


@pytest.mark.parametrize("value", [1, 240])
def test_timeout_boundaries_are_valid(launch_case, value):
    launch_case.request.raw["maxExecutionMinutes"] = value
    assert launch(launch_case).pid == 789


def test_disabled_launcher_remains_disabled(launch_case):
    launch_case.launcher.enabled = False
    assert_rejected(launch_case, "disabled")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("executorType", "CHATGPT", "executorType=CODEX"),
        ("agentId", "REGISTRAR", "agentId"),
        ("dispatchId", "D-OTHER", "dispatchId"),
    ],
)
def test_root_identity_guards_are_preserved(launch_case, field, value, message):
    launch_case.request.raw[field] = value
    assert_rejected(launch_case, message)


@pytest.mark.parametrize("value", [None, "", "UNKNOWN"])
def test_invalid_executor_type_fails_closed(launch_case, value):
    launch_case.request.raw["executorType"] = value
    assert_rejected(launch_case, "executorType", ContractError)


def test_queue_agent_must_be_codex_implementer(launch_case):
    launch_case.job = replace(launch_case.job, agent_id="REGISTRAR")
    assert_rejected(launch_case, "QUEUE agentId")


def test_missing_executable_on_path_fails_closed(launch_case):
    launch_case.resolver.return_value = None
    assert_rejected(launch_case, "not found on PATH")


def test_missing_explicit_executable_fails_closed(launch_case):
    launch_case.launcher.executable = str(launch_case.repository / "missing-codex.exe")
    assert_rejected(launch_case, "configured Codex executable was not found")


def test_workspace_write_prompt_permits_only_authorized_repository_mutations(launch_case):
    case = launch_case
    case.job = replace(case.job, lease_token="unit-secret-not-for-model")
    case.request.raw["instructions"] = "Create authorized.txt in the repository root."
    launch(case)
    manifest_file = Path(case.process_factory.call_args.args[0][-1])
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    prompt = Path(manifest["promptFile"]).read_text(encoding="utf-8")
    assert "allowedWrites.stagingOnly=false" in prompt
    assert "Create authorized.txt in the repository root." in prompt
    assert str(case.repository) in prompt
    assert "Repository/workspace mutations are execution effects, not Factory artifacts." in prompt
    assert "Do not publish into canonical Factory folders" in prompt
    assert "Write all produced artifacts only to the staging folder" not in prompt
    assert "Write exactly one terminal EXECUTION_RECEIPT" not in prompt
    assert "Do not create or upload EXECUTION_RECEIPT yourself" in prompt
    assert "The local worker creates it" in prompt
    assert "rg is optional" in prompt
    assert case.job.lease_token not in prompt.split("DISPATCH_REQUEST SNAPSHOT")[0]


@pytest.mark.parametrize("value", [True, None, "false", 0, {}, []])
def test_staging_only_or_invalid_write_scope_blocks_worker_start(launch_case, value):
    launch_case.request.raw["allowedWrites"]["stagingOnly"] = value
    assert_rejected(launch_case, "stagingOnly")


def test_missing_allowed_writes_fails_closed(launch_case):
    del launch_case.request.raw["allowedWrites"]
    assert_rejected(launch_case, "allowedWrites")


def test_bootstrap_routes_codex_to_its_execution_contract(launch_case):
    from conftest import FakeGateway

    case = launch_case
    text = build_bootstrap(case.job, FakeGateway([]).factory, "stage-id", case.request)
    assert text.startswith("FACTORY_CODEX_EXECUTION_V1")
    assert "stagingFolderId=stage-id" in text
    assert "bootstrapDriveId=" not in text
    assert "leaseToken=" not in text


def test_missing_receipt_destination_fails_before_process_start(launch_case):
    case = launch_case
    with pytest.raises(ContractError, match="receiptFolderId"):
        case.launcher.launch(case.job, case.request, "prompt")
    case.process_factory.assert_not_called()
