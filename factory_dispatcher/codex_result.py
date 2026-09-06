"""Codex final-output schema and deterministic conversion to EXECUTION_RECEIPT-v1."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .errors import ContractError
from .receipts import AttemptContext, read_json_object, required_string, validate_receipt


def result_schema(context: AttemptContext) -> dict[str, Any]:
    fields = {
        "schemaVersion": {"type": "integer", "enum": [1]},
        "dispatchId": {"type": "string", "enum": [context.dispatchId]},
        "attemptId": {"type": "string", "enum": [context.attemptId]},
        "status": {"type": "string", "enum": ["SUCCEEDED", "BLOCKED", "FAILED"]},
        "summary": {"type": "string"},
        "producedArtifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "description": {"type": "string"}},
                "required": ["path", "description"],
                "additionalProperties": False,
            },
        },
        "error": {
            "type": "object",
            "properties": {
                "code": {"type": ["string", "null"]},
                "detail": {"type": ["string", "null"]},
                "retrySuggested": {"type": "boolean"},
            },
            "required": ["code", "detail", "retrySuggested"],
            "additionalProperties": False,
        },
    }
    return {
        "type": "object",
        "properties": fields,
        "required": list(fields),
        "additionalProperties": False,
    }


def validate_result(raw: dict[str, Any], context: AttemptContext) -> None:
    if set(raw) != set(result_schema(context)["required"]):
        raise ContractError("Codex result has missing or unknown fields")
    if type(raw["schemaVersion"]) is not int or raw["schemaVersion"] != 1:
        raise ContractError("Codex result schemaVersion must be integer 1")
    for key in ("dispatchId", "attemptId"):
        if raw[key] != getattr(context, key):
            raise ContractError(f"Codex result {key} does not match the attempt")
    if not isinstance(raw["status"], str) or raw["status"] not in {
        "SUCCEEDED",
        "BLOCKED",
        "FAILED",
    }:
        raise ContractError("Unknown Codex result status")
    required_string(raw, "summary")
    artifacts = raw["producedArtifacts"]
    if not isinstance(artifacts, list):
        raise ContractError("Codex result producedArtifacts must be an array")
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "description"}:
            raise ContractError("Each Codex artifact requires path and description")
        path = required_string(artifact, "path")
        required_string(artifact, "description")
        windows = PureWindowsPath(path)
        posix = PurePosixPath(path)
        if windows.anchor or posix.is_absolute() or ".." in windows.parts or ".." in posix.parts:
            raise ContractError(
                "Codex artifact path must stay relative to the authorized repository"
            )
    error = raw["error"]
    if not isinstance(error, dict) or set(error) != {"code", "detail", "retrySuggested"}:
        raise ContractError("Codex result requires a complete error object")
    if type(error["retrySuggested"]) is not bool:
        raise ContractError("Codex error.retrySuggested must be boolean")
    if raw["status"] == "SUCCEEDED":
        if error != {"code": None, "detail": None, "retrySuggested": False}:
            raise ContractError("SUCCEEDED cannot contain an error or retry suggestion")
    else:
        required_string(error, "code")
        required_string(error, "detail")


def materialize_receipt(
    context: AttemptContext,
    final_output: Path,
    *,
    exit_code: int,
    timed_out: bool,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    if timed_out:
        code, detail = "CODEX_TIMEOUT", "Codex exceeded maxExecutionMinutes."
    elif exit_code != 0:
        code, detail = "CODEX_PROCESS_FAILED", f"Codex exited with code {exit_code}."
    else:
        try:
            raw = read_json_object(final_output)
            validate_result(raw, context)
        except ContractError:
            code, detail = (
                "CODEX_RESULT_INVALID",
                "Final output is missing or violates the JSON contract.",
            )
        else:
            code, detail = "", ""
            status = {"SUCCEEDED": "SUCCEEDED", "BLOCKED": "BLOCKED", "FAILED": "FAILED_FINAL"}[
                raw["status"]
            ]
            artifacts = raw["producedArtifacts"]
            error = raw["error"]
            statement = raw["summary"]
    if code:
        status = "FAILED_FINAL"
        error = {"code": code, "detail": detail, "retrySuggested": False}
        statement = "FactoryDispatcher worker: " + detail
    receipt = {
        "schemaVersion": 1,
        "artifactType": "EXECUTION_RECEIPT",
        **{key: value for key, value in context.to_dict().items() if key != "receiptFolderId"},
        "status": status,
        "producedArtifacts": artifacts,
        "error": error,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "executorStatement": statement,
    }
    validate_receipt(receipt)
    return receipt
