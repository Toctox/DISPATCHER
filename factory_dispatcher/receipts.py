"""Strict local JSON and attempt identity shared by the worker and receipt transport."""

from __future__ import annotations

import hmac
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ContractError
from .models import ACTIVE_STATES, DispatchJob, parse_timestamp

MAX_JSON_BYTES = 2 * 1024 * 1024
RECEIPT_STATUSES = {"SUCCEEDED", "BLOCKED", "WAITING_HUMAN", "FAILED_RETRYABLE", "FAILED_FINAL"}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("JSON contains duplicate keys")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ContractError("JSON contains a non-finite number")


def decode_json_object(content: bytes) -> dict[str, Any]:
    if len(content) > MAX_JSON_BYTES:
        raise ContractError("JSON exceeds the local contract size limit")
    try:
        result = json.loads(
            content.decode("utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ContractError("Expected one strict UTF-8 JSON object") from exc
    if not isinstance(result, dict):
        raise ContractError("JSON root must be an object")
    return result


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return decode_json_object(handle.read(MAX_JSON_BYTES + 1))
    except OSError as exc:
        raise ContractError("Required local JSON file is missing or unreadable") from exc


def write_json(path: Path, payload: dict[str, Any], *, exclusive: bool = False) -> None:
    """Keep control files private; terminal receipts are never overwritten."""
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    target = path if exclusive else path.with_suffix(path.suffix + ".tmp")
    with target.open("x" if exclusive else "w", encoding="utf-8") as handle:
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        handle.write(content)
    if not exclusive:
        target.replace(path)


def required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{key} must be a non-empty string")
    return value


@dataclass(frozen=True)
class AttemptContext:
    dispatchId: str
    attemptId: str
    leaseToken: str = field(repr=False)
    agentId: str
    requestDriveId: str
    receiptFolderId: str

    @classmethod
    def from_mapping(cls, raw: Any) -> AttemptContext:
        if not isinstance(raw, dict):
            raise ContractError("Missing execution attempt context")
        fields = {key: required_string(raw, key) for key in cls.__dataclass_fields__}
        for key in ("dispatchId", "attemptId"):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", fields[key]):
                raise ContractError(f"{key} cannot be used as an attempt directory name")
        return cls(**fields)

    @classmethod
    def from_job(cls, job: DispatchJob, folder_id: str | None) -> AttemptContext:
        return cls.from_mapping(
            {
                "dispatchId": job.dispatch_id,
                "attemptId": job.attempt_id,
                "leaseToken": job.lease_token,
                "agentId": job.agent_id,
                "requestDriveId": job.request_drive_id,
                "receiptFolderId": folder_id,
            }
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @property
    def receipt_name(self) -> str:
        return f"EXECUTION_RECEIPT__{self.dispatchId}__{self.attemptId}.json"

    def match_receipt(self, receipt: dict[str, Any]) -> None:
        for key, expected in self.to_dict().items():
            if key == "receiptFolderId" and key not in receipt:
                continue  # v1 receipts need not embed the transport destination.
            actual = receipt.get(key)
            if not isinstance(actual, str) or not hmac.compare_digest(
                actual.encode("utf-8"), expected.encode("utf-8")
            ):
                raise ContractError(f"Receipt {key} does not match the attempt")

    def match_current_job(self, job: DispatchJob, now: datetime) -> None:
        self.match_receipt(AttemptContext.from_job(job, self.receiptFolderId).to_dict())
        if job.status not in ACTIVE_STATES:
            raise ContractError("Receipt requires a CLAIMED or RUNNING attempt")
        if not job.lease_expires_at or job.lease_expires_at <= now:
            raise ContractError("Receipt lease has expired")


def validate_receipt(receipt: dict[str, Any]) -> None:
    version = receipt.get("schemaVersion")
    if not ((type(version) is int and version == 1) or version == "1"):
        raise ContractError("Receipt schemaVersion must be 1")
    if receipt.get("artifactType") != "EXECUTION_RECEIPT":
        raise ContractError("Receipt artifactType must be EXECUTION_RECEIPT")
    for key in (
        "dispatchId",
        "attemptId",
        "leaseToken",
        "agentId",
        "requestDriveId",
        "executorStatement",
    ):
        required_string(receipt, key)
    if not isinstance(receipt.get("status"), str) or receipt["status"] not in RECEIPT_STATUSES:
        raise ContractError("Unknown receipt status")
    artifacts = receipt.get("producedArtifacts")
    if not isinstance(artifacts, list) or not all(isinstance(item, dict) for item in artifacts):
        raise ContractError("Receipt producedArtifacts must be an array of objects")
    error = receipt.get("error")
    if not isinstance(error, dict):
        raise ContractError("Receipt error must be an object")
    for key in ("code", "detail"):
        if error.get(key) is not None and not isinstance(error[key], str):
            raise ContractError(f"Receipt error.{key} must be a string or null")
    if "retrySuggested" in error and type(error["retrySuggested"]) is not bool:
        raise ContractError("Receipt error.retrySuggested must be boolean")
    timestamps = []
    for key in ("startedAt", "finishedAt"):
        text = required_string(receipt, key)
        try:
            timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError(f"Receipt {key} must be an ISO timestamp") from exc
        if timestamp.tzinfo is None:
            raise ContractError(f"Receipt {key} requires an explicit timezone")
        timestamps.append(parse_timestamp(text))
    if timestamps[0] > timestamps[1]:
        raise ContractError("Receipt finishedAt precedes startedAt")
