from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .errors import ContractError


class DispatchState(StrEnum):
    WAITING_DEPENDENCIES = "WAITING_DEPENDENCIES"
    READY = "READY"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    RESULT_STAGED = "RESULT_STAGED"
    SUCCEEDED = "SUCCEEDED"
    WAITING_HUMAN = "WAITING_HUMAN"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class RetryPolicy(StrEnum):
    SAFE_RETRY = "SAFE_RETRY"
    RECONCILE_BEFORE_RETRY = "RECONCILE_BEFORE_RETRY"
    NO_AUTO_RETRY = "NO_AUTO_RETRY"


class ExecutorType(StrEnum):
    CHATGPT = "CHATGPT"
    CODEX = "CODEX"


ACTIVE_STATES = {DispatchState.CLAIMED, DispatchState.RUNNING}
DEDUPE_PROTECTED_STATES = {
    DispatchState.CLAIMED,
    DispatchState.RUNNING,
    DispatchState.RESULT_STAGED,
    DispatchState.SUCCEEDED,
    DispatchState.WAITING_HUMAN,
    DispatchState.FAILED_FINAL,
}


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_int(value: Any, default: int = 0) -> int:
    if value is None or str(value).strip() == "":
        return default
    return int(value)


def normalize_change_id(value: Any) -> str:
    """Map null and blank IDs to absence without coercing other JSON types."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ContractError("changeId must be a string or null")
    return value.strip()


def parse_dependencies(value: Any) -> tuple[str, ...]:
    if value is None or str(value).strip() == "":
        return ()
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    if text.startswith("["):
        loaded = json.loads(text)
        if not isinstance(loaded, list):
            raise ContractError("dependsOn must be a JSON list or comma-separated IDs")
        return tuple(str(item).strip() for item in loaded if str(item).strip())
    return tuple(item.strip() for item in text.split(",") if item.strip())


@dataclass(frozen=True)
class DispatchJob:
    row_number: int
    dispatch_id: str
    root_run_id: str
    generation: int
    agent_id: str
    change_id: str
    task_type: str
    target_ref: str
    status: DispatchState
    priority: int
    dedupe_key: str
    depends_on: tuple[str, ...]
    attempt: int
    max_attempts: int
    lease_owner: str
    lease_token: str
    lease_expires_at: datetime | None
    not_before: datetime | None
    retry_policy: RetryPolicy
    side_effect_class: str
    request_drive_id: str
    staging_folder_id: str
    receipt_drive_id: str

    @property
    def attempt_id(self) -> str:
        return f"{self.dispatch_id}-A{self.attempt:03d}"

    @classmethod
    def from_row(cls, row_number: int, row: dict[str, Any]) -> DispatchJob:
        try:
            return cls(
                row_number=row_number,
                dispatch_id=str(row["dispatchId"]).strip(),
                root_run_id=str(row.get("rootRunId", "")).strip(),
                generation=parse_int(row.get("generation")),
                agent_id=str(row.get("agentId", "")).strip(),
                change_id=normalize_change_id(row.get("changeId")),
                task_type=str(row.get("taskType", "")).strip(),
                target_ref=str(row.get("targetRef", "")).strip(),
                status=DispatchState(str(row["status"]).strip()),
                priority=parse_int(row.get("priority")),
                dedupe_key=str(row.get("dedupeKey", "")).strip(),
                depends_on=parse_dependencies(row.get("dependsOn")),
                attempt=parse_int(row.get("attempt")),
                max_attempts=parse_int(row.get("maxAttempts"), 1),
                lease_owner=str(row.get("leaseOwner", "")).strip(),
                lease_token=str(row.get("leaseToken", "")).strip(),
                lease_expires_at=parse_timestamp(row.get("leaseExpiresAt")),
                not_before=parse_timestamp(row.get("notBefore")),
                retry_policy=RetryPolicy(str(row.get("retryPolicy", "SAFE_RETRY")).strip()),
                side_effect_class=str(row.get("sideEffectClass", "")).strip(),
                request_drive_id=str(row.get("requestDriveId", "")).strip(),
                staging_folder_id=str(row.get("stagingFolderId", "")).strip(),
                receipt_drive_id=str(row.get("receiptDriveId", "")).strip(),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ContractError(f"invalid queue row {row_number}: {exc}") from exc


@dataclass(frozen=True)
class DispatchRequest:
    raw: dict[str, Any]

    @property
    def change_id(self) -> str:
        return normalize_change_id(self.raw.get("changeId"))

    @property
    def executor_type(self) -> ExecutorType:
        value = str(self.raw.get("executorType", "")).strip().upper()
        if not value:
            raise ContractError("request is missing executorType")
        try:
            return ExecutorType(value)
        except ValueError as exc:
            raise ContractError(f"unsupported executorType: {value}") from exc


@dataclass(frozen=True)
class Receipt:
    drive_id: str
    raw: dict[str, Any]

    @property
    def produced_artifact_ids(self) -> list[str]:
        result: list[str] = []
        for item in self.raw.get("producedArtifacts", []):
            if isinstance(item, dict):
                value = item.get("driveId") or item.get("artifactId")
            else:
                value = item
            if value:
                result.append(str(value))
        return result
