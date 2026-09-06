from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .bootstrap import build_bootstrap
from .chatgpt_state import browser_attempt_status
from .codex_prompt import require_workspace_write
from .config import FactoryConfig, LocalSettings
from .errors import ClaimLostError, ContractError, DispatcherError
from .launchers.base import Launcher
from .models import (
    ACTIVE_STATES,
    DEDUPE_PROTECTED_STATES,
    DispatchJob,
    DispatchRequest,
    DispatchState,
    ExecutorType,
    Receipt,
    RetryPolicy,
    isoformat,
    normalize_change_id,
)
from .receipts import validate_receipt


class Gateway(Protocol):
    def read_factory_config(self) -> FactoryConfig: ...

    def read_queue(self) -> list[DispatchJob]: ...

    def read_job(self, row_number: int) -> DispatchJob: ...

    def update_job(self, row_number: int, fields: dict[str, Any]) -> None: ...

    def claim_job(
        self,
        job: DispatchJob,
        *,
        owner: str,
        lease_token: str,
        now: datetime,
        lease_minutes: int,
    ) -> DispatchJob: ...

    def append_event(self, values: list[Any]) -> None: ...

    def read_json_artifact(
        self, file_id: str, expected_parent_id: str | None = None
    ) -> dict[str, Any]: ...

    def create_staging(self, root_id: str, dispatch_id: str, attempt_id: str) -> str: ...

    def upload_text(self, parent_id: str, name: str, content: str) -> str: ...

    def find_receipt(self, receipt_folder_id: str, name: str) -> Receipt | None: ...


class AuditLogger(Protocol):
    def record(self, event: str, **fields: Any) -> None: ...


class Dispatcher:
    def __init__(
        self,
        gateway: Gateway,
        settings: LocalSettings,
        logger: AuditLogger,
        launchers: dict[ExecutorType, Launcher],
        *,
        clock: Any | None = None,
        token_factory: Any | None = None,
    ) -> None:
        self.gateway = gateway
        self.settings = settings
        self.logger = logger
        self.launchers = launchers
        self.clock = clock or (lambda: datetime.now(UTC))
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(32))

    def _event(
        self,
        job: DispatchJob,
        event_type: str,
        now: datetime,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details = details or {}
        serialized = json.dumps(
            safe_details, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        details_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        self.gateway.append_event(
            [
                str(uuid.uuid4()),
                isoformat(now),
                job.dispatch_id,
                job.attempt_id if job.attempt else "",
                event_type,
                self.settings.claim_owner,
                details_hash,
                serialized,
            ]
        )

    def _validate_request_identity(self, job: DispatchJob, request: DispatchRequest) -> None:
        raw = request.raw
        if str(raw.get("dispatchId", "")).strip() != job.dispatch_id:
            raise ContractError("request dispatchId does not match QUEUE")
        if str(raw.get("agentId", "")).strip() != job.agent_id:
            raise ContractError("request agentId does not match QUEUE")
        if request.change_id != normalize_change_id(job.change_id):
            raise ContractError("request changeId does not match QUEUE")

    def _receipt_error(self, job: DispatchJob, receipt: Receipt, now: datetime) -> str | None:
        raw = receipt.raw
        if raw.get("artifactType") != "EXECUTION_RECEIPT":
            return "artifactType is not EXECUTION_RECEIPT"
        expected = {
            "dispatchId": job.dispatch_id,
            "attemptId": job.attempt_id,
            "agentId": job.agent_id,
            "requestDriveId": job.request_drive_id,
        }
        for key, value in expected.items():
            if str(raw.get(key, "")) != value:
                return f"{key} mismatch"
        supplied_token = str(raw.get("leaseToken", ""))
        if not supplied_token or not hmac.compare_digest(supplied_token, job.lease_token):
            return "leaseToken mismatch"
        if not job.lease_expires_at or job.lease_expires_at <= now:
            return "receipt belongs to an expired lease"
        allowed_statuses = {
            "SUCCEEDED",
            "BLOCKED",
            "WAITING_HUMAN",
            "FAILED_RETRYABLE",
            "FAILED_FINAL",
        }
        if raw.get("status") not in allowed_statuses:
            return "invalid terminal receipt status"
        return None

    def _reconcile_receipts(
        self, jobs: list[DispatchJob], factory: FactoryConfig, now: datetime
    ) -> int:
        reconciled = 0
        for job in jobs:
            if job.status not in ACTIVE_STATES or not job.lease_token:
                continue
            name = f"EXECUTION_RECEIPT__{job.dispatch_id}__{job.attempt_id}.json"
            receipt = self.gateway.find_receipt(factory.receipts_folder_id, name)
            if receipt is None:
                continue
            error = self._receipt_error(job, receipt, now)
            if not error and browser_attempt_status(self.settings.state_directory, job) is not None:
                try:
                    validate_receipt(receipt.raw)
                except ContractError:
                    error = "browser receipt contract invalid"
            if error:
                self.logger.record(
                    "receipt_rejected",
                    dispatchId=job.dispatch_id,
                    attemptId=job.attempt_id,
                    reason=error,
                )
                continue
            self.gateway.update_job(
                job.row_number,
                {
                    "status": DispatchState.RESULT_STAGED.value,
                    "receiptDriveId": receipt.drive_id,
                    "producedArtifactIds": json.dumps(
                        receipt.produced_artifact_ids, ensure_ascii=False, separators=(",", ":")
                    ),
                    "finishedAt": str(receipt.raw.get("finishedAt") or isoformat(now)),
                    "updatedAt": isoformat(now),
                    "lastErrorCode": "",
                    "lastErrorDetail": "",
                },
            )
            self._event(
                job,
                "RECEIPT_FOUND",
                now,
                {"receiptDriveId": receipt.drive_id, "receiptStatus": receipt.raw["status"]},
            )
            reconciled += 1
        return reconciled

    def _settle_browser_stops(self, jobs: list[DispatchJob], now: datetime) -> None:
        for job in jobs:
            if job.status not in ACTIVE_STATES:
                continue
            status = browser_attempt_status(self.settings.state_directory, job)
            if status is None or status.get("state") != "STOPPED":
                continue
            # Local status is operational evidence only; never publish task success from it.
            self._reject_candidate(
                job,
                now,
                state=DispatchState.WAITING_HUMAN,
                code="CHATGPT_RECONCILIATION_REQUIRED",
                detail=(
                    "Browser worker stopped without confirmed completion. "
                    "Inspect local status; reconcile effects before retry."
                ),
                event_type="WAITING_HUMAN",
            )

    def _expire_leases(self, jobs: list[DispatchJob], factory: FactoryConfig, now: datetime) -> int:
        expired = 0
        for job in jobs:
            if (
                job.status not in ACTIVE_STATES
                or not job.lease_expires_at
                or job.lease_expires_at > now
            ):
                continue
            if browser_attempt_status(self.settings.state_directory, job) is not None:
                next_state = DispatchState.WAITING_HUMAN
                not_before = ""
            elif job.attempt >= job.max_attempts:
                next_state = DispatchState.FAILED_FINAL
                not_before = ""
            elif job.retry_policy == RetryPolicy.SAFE_RETRY:
                next_state = DispatchState.FAILED_RETRYABLE
                not_before = isoformat(now + timedelta(minutes=factory.retry_backoff_minutes))
            else:
                next_state = DispatchState.WAITING_HUMAN
                not_before = ""
            self.gateway.update_job(
                job.row_number,
                {
                    "status": next_state.value,
                    "notBefore": not_before,
                    "lastErrorCode": "LEASE_EXPIRED",
                    "lastErrorDetail": "No valid terminal receipt was found before lease expiry.",
                    "updatedAt": isoformat(now),
                },
            )
            self._event(job, "LEASE_EXPIRED", now, {"nextState": next_state.value})
            if next_state == DispatchState.FAILED_RETRYABLE:
                self._event(job, "RETRY_SCHEDULED", now, {"notBefore": not_before})
            elif next_state == DispatchState.FAILED_FINAL:
                self._event(job, "FAILED_FINAL", now, {"reason": "maxAttempts"})
            else:
                self._event(job, "WAITING_HUMAN", now, {"reason": job.retry_policy.value})
            expired += 1
        return expired

    def _promote_retryable(self, jobs: list[DispatchJob], now: datetime) -> int:
        promoted = 0
        for job in jobs:
            if job.status != DispatchState.FAILED_RETRYABLE:
                continue
            if browser_attempt_status(self.settings.state_directory, job) is not None:
                self._reject_candidate(
                    job,
                    now,
                    state=DispatchState.WAITING_HUMAN,
                    code="CHATGPT_RECONCILIATION_REQUIRED",
                    detail="Existing browser attempt evidence prohibits automatic resend.",
                    event_type="WAITING_HUMAN",
                )
                continue
            if job.attempt >= job.max_attempts:
                self.gateway.update_job(
                    job.row_number,
                    {
                        "status": DispatchState.FAILED_FINAL.value,
                        "lastErrorCode": "MAX_ATTEMPTS",
                        "updatedAt": isoformat(now),
                    },
                )
                self._event(job, "FAILED_FINAL", now, {"reason": "maxAttempts"})
                continue
            if job.not_before and job.not_before > now:
                continue
            self.gateway.update_job(
                job.row_number,
                {
                    "status": DispatchState.READY.value,
                    "leaseOwner": "",
                    "leaseToken": "",
                    "leaseExpiresAt": "",
                    "claimedAt": "",
                    "startedAt": "",
                    "notBefore": "",
                    "updatedAt": isoformat(now),
                },
            )
            promoted += 1
        return promoted

    def _release_dependencies(self, jobs: list[DispatchJob], now: datetime) -> int:
        by_id = {job.dispatch_id: job for job in jobs}
        released = 0
        for job in jobs:
            if job.status != DispatchState.WAITING_DEPENDENCIES:
                continue
            dependencies = [by_id.get(item) for item in job.depends_on]
            if job.depends_on and all(
                dependency and dependency.status == DispatchState.SUCCEEDED
                for dependency in dependencies
            ):
                self.gateway.update_job(
                    job.row_number,
                    {"status": DispatchState.READY.value, "updatedAt": isoformat(now)},
                )
                released += 1
            elif any(
                dependency
                and dependency.status
                in {DispatchState.FAILED_FINAL, DispatchState.CANCELLED, DispatchState.SUPERSEDED}
                for dependency in dependencies
            ):
                self.gateway.update_job(
                    job.row_number,
                    {
                        "status": DispatchState.WAITING_HUMAN.value,
                        "lastErrorCode": "DEPENDENCY_TERMINAL_FAILURE",
                        "lastErrorDetail": "At least one dependency cannot reach SUCCEEDED.",
                        "updatedAt": isoformat(now),
                    },
                )
                self._event(job, "WAITING_HUMAN", now, {"reason": "dependency"})
        return released

    def _active_chatgpt_count(self, jobs: list[DispatchJob], factory: FactoryConfig) -> int:
        count = 0
        for job in jobs:
            if job.status not in ACTIVE_STATES:
                continue
            try:
                request = DispatchRequest(
                    self.gateway.read_json_artifact(
                        job.request_drive_id, factory.requests_folder_id
                    )
                )
                count += request.executor_type == ExecutorType.CHATGPT
            except DispatcherError:
                count += 1
        return count

    def _reject_candidate(
        self,
        job: DispatchJob,
        now: datetime,
        *,
        state: DispatchState,
        code: str,
        detail: str,
        event_type: str,
    ) -> None:
        self.gateway.update_job(
            job.row_number,
            {
                "status": state.value,
                "lastErrorCode": code,
                "lastErrorDetail": detail[:500],
                "updatedAt": isoformat(now),
            },
        )
        self._event(job, event_type, now, {"reason": code})

    def _candidate_blocker(
        self, candidate: DispatchJob, jobs: list[DispatchJob], factory: FactoryConfig
    ) -> tuple[DispatchState, str, str, str] | None:
        if candidate.attempt >= candidate.max_attempts:
            return (
                DispatchState.FAILED_FINAL,
                "MAX_ATTEMPTS",
                "READY row has already exhausted maxAttempts.",
                "FAILED_FINAL",
            )
        if candidate.dedupe_key and any(
            other.dispatch_id != candidate.dispatch_id
            and other.dedupe_key == candidate.dedupe_key
            and other.status in DEDUPE_PROTECTED_STATES
            for other in jobs
        ):
            return (
                DispatchState.SUPERSEDED,
                "DUPLICATE_DEDUPE_KEY",
                "An active or concluded dispatch already owns this dedupeKey.",
                "DUPLICATE_REJECTED",
            )
        executions = sum(job.attempt for job in jobs if job.root_run_id == candidate.root_run_id)
        if candidate.root_run_id and executions >= factory.max_executions_per_root_run:
            return (
                DispatchState.WAITING_HUMAN,
                "ROOT_RUN_EXECUTION_CAP",
                "MAX_EXECUTIONS_PER_ROOT_RUN was reached.",
                "WAITING_HUMAN",
            )
        return None

    def _fail_attempt(
        self, job: DispatchJob, factory: FactoryConfig, now: datetime, exc: Exception
    ) -> None:
        if browser_attempt_status(self.settings.state_directory, job) is not None:
            state = DispatchState.WAITING_HUMAN
            event = "WAITING_HUMAN"
            not_before = ""
        elif job.attempt >= job.max_attempts:
            state = DispatchState.FAILED_FINAL
            event = "FAILED_FINAL"
            not_before = ""
        elif job.retry_policy == RetryPolicy.SAFE_RETRY:
            state = DispatchState.FAILED_RETRYABLE
            event = "RETRY_SCHEDULED"
            not_before = isoformat(now + timedelta(minutes=factory.retry_backoff_minutes))
        else:
            state = DispatchState.WAITING_HUMAN
            event = "WAITING_HUMAN"
            not_before = ""
        self.gateway.update_job(
            job.row_number,
            {
                "status": state.value,
                "notBefore": not_before,
                "lastErrorCode": type(exc).__name__.upper(),
                "lastErrorDetail": str(exc)[:500],
                "updatedAt": isoformat(now),
            },
        )
        self._event(job, event, now, {"reason": type(exc).__name__})

    def tick(self, *, dry_run: bool = False) -> dict[str, Any]:
        now = self.clock()
        factory = self.gateway.read_factory_config()
        jobs = self.gateway.read_queue()
        self.logger.record(
            "tick_started",
            dryRun=dry_run,
            queueSize=len(jobs),
            writesEnabled=self.settings.writes_enabled,
        )

        if not dry_run and self.settings.writes_enabled:
            reconciled = self._reconcile_receipts(jobs, factory, now)
            jobs = self.gateway.read_queue()
            self._settle_browser_stops(jobs, now)
            jobs = self.gateway.read_queue()
            expired = self._expire_leases(jobs, factory, now)
            jobs = self.gateway.read_queue()
            promoted = self._promote_retryable(jobs, now)
            jobs = self.gateway.read_queue()
            released = self._release_dependencies(jobs, now)
            jobs = self.gateway.read_queue()
        else:
            reconciled = expired = promoted = released = 0

        candidates = sorted(
            (
                job
                for job in jobs
                if job.status == DispatchState.READY
                and (job.not_before is None or job.not_before <= now)
            ),
            key=lambda job: (-job.priority, job.row_number),
        )
        for candidate in candidates:
            blocker = self._candidate_blocker(candidate, jobs, factory)
            if blocker:
                if not dry_run and self.settings.writes_enabled:
                    state, code, detail, event_type = blocker
                    self._reject_candidate(
                        candidate,
                        now,
                        state=state,
                        code=code,
                        detail=detail,
                        event_type=event_type,
                    )
                continue
            try:
                request = DispatchRequest(
                    self.gateway.read_json_artifact(
                        candidate.request_drive_id, factory.requests_folder_id
                    )
                )
                self._validate_request_identity(candidate, request)
                executor_type = request.executor_type
                if executor_type == ExecutorType.CODEX:
                    require_workspace_write(request)
            except DispatcherError as exc:
                if not dry_run and self.settings.writes_enabled:
                    self._reject_candidate(
                        candidate,
                        now,
                        state=DispatchState.WAITING_HUMAN,
                        code="INVALID_REQUEST_CONTRACT",
                        detail=str(exc),
                        event_type="WAITING_HUMAN",
                    )
                self.logger.record(
                    "candidate_rejected", dispatchId=candidate.dispatch_id, reason=str(exc)
                )
                continue
            if executor_type == ExecutorType.CHATGPT and self._active_chatgpt_count(
                jobs, factory
            ) >= min(1, factory.max_concurrent_chatgpt_executions):
                self.logger.record("capacity_full", dispatchId=candidate.dispatch_id)
                break
            if dry_run or not self.settings.writes_enabled:
                result = {
                    "outcome": "DRY_RUN" if dry_run else "WRITES_DISABLED",
                    "selectedDispatchId": candidate.dispatch_id,
                    "executorType": executor_type.value,
                    "reconciled": reconciled,
                    "expired": expired,
                    "promoted": promoted,
                    "released": released,
                }
                self.logger.record("tick_finished", **result)
                return result

            launcher = self.launchers.get(executor_type)
            if executor_type == ExecutorType.CHATGPT:
                try:
                    if launcher is None:
                        raise ContractError("CHATGPT_LAUNCHER_MISSING")
                    if preflight := getattr(launcher, "preflight", None):
                        preflight(request)
                except Exception as exc:
                    # Local operational condition: do not claim, increment attempts or mutate QUEUE.
                    code = getattr(exc, "code", "CHATGPT_PREFLIGHT_FAILED")
                    result = {
                        "outcome": "PREFLIGHT_BLOCKED",
                        "dispatchId": candidate.dispatch_id,
                        "errorCode": code,
                    }
                    self.logger.record("chatgpt_preflight_blocked", **result)
                    return result
                now = self.clock()  # Browser auth preflight must not shorten the new lease.

            claimed: DispatchJob | None = None
            try:
                claimed = self.gateway.claim_job(
                    candidate,
                    owner=self.settings.claim_owner,
                    lease_token=self.token_factory(),
                    now=now,
                    lease_minutes=factory.default_lease_minutes,
                )
                self._event(claimed, "CLAIMED", now, {"executorType": executor_type.value})
                staging_id = self.gateway.create_staging(
                    factory.staging_folder_id, claimed.dispatch_id, claimed.attempt_id
                )
                self.gateway.update_job(
                    claimed.row_number,
                    {"stagingFolderId": staging_id, "updatedAt": isoformat(now)},
                )
                claimed = self.gateway.read_job(claimed.row_number)
                if (
                    claimed.status != DispatchState.CLAIMED
                    or claimed.lease_owner != self.settings.claim_owner
                ):
                    raise ClaimLostError("claim changed before launcher invocation")
                prompt = build_bootstrap(claimed, factory, staging_id, request)
                self.gateway.upload_text(
                    staging_id,
                    f"FACTORY_BOOTSTRAP__{claimed.dispatch_id}__{claimed.attempt_id}.txt",
                    prompt,
                )
                launcher = self.launchers.get(executor_type)
                if launcher is None:
                    raise ContractError(f"no launcher configured for {executor_type.value}")
                if getattr(launcher, "requires_reconciliation", False):
                    # Durable guard before spawning, including crashes/lost local state.
                    # Keep NO_AUTO_RETRY if the queue already requires that stricter policy.
                    if claimed.retry_policy == RetryPolicy.SAFE_RETRY:
                        self.gateway.update_job(
                            claimed.row_number,
                            {
                                "retryPolicy": RetryPolicy.RECONCILE_BEFORE_RETRY.value,
                                "updatedAt": isoformat(now),
                            },
                        )
                    verified = self.gateway.read_job(claimed.row_number)
                    if (
                        verified.status != DispatchState.CLAIMED
                        or verified.lease_token != claimed.lease_token
                        or verified.retry_policy == RetryPolicy.SAFE_RETRY
                    ):
                        raise ClaimLostError("browser reconciliation policy or claim not confirmed")
                    claimed = verified
                launch_result = launcher.launch(
                    claimed, request, prompt, receipt_folder_id=factory.receipts_folder_id
                )
                current = self.gateway.read_job(claimed.row_number)
                if current.status != DispatchState.CLAIMED or not hmac.compare_digest(
                    current.lease_token, claimed.lease_token
                ):
                    raise ClaimLostError("claim changed after launcher invocation")
                self.gateway.update_job(
                    claimed.row_number,
                    {
                        "status": DispatchState.RUNNING.value,
                        "startedAt": isoformat(now),
                        "updatedAt": isoformat(now),
                    },
                )
                self._event(
                    claimed,
                    "LAUNCHED",
                    now,
                    {"launcher": launch_result.launcher, "pid": launch_result.pid},
                )
                result = {
                    "outcome": "LAUNCHED",
                    "dispatchId": claimed.dispatch_id,
                    "attemptId": claimed.attempt_id,
                    "executorType": executor_type.value,
                    "launcher": launch_result.launcher,
                    "reconciled": reconciled,
                    "expired": expired,
                }
                self.logger.record("tick_finished", **result)
                return result
            except Exception as exc:
                if claimed is not None and not isinstance(exc, ClaimLostError):
                    self._fail_attempt(claimed, factory, now, exc)
                self.logger.record(
                    "launch_failed",
                    dispatchId=candidate.dispatch_id,
                    errorType=type(exc).__name__,
                    error=str(exc),
                )
                return {
                    "outcome": "FAILED",
                    "dispatchId": candidate.dispatch_id,
                    "errorType": type(exc).__name__,
                }

        result = {
            "outcome": "IDLE",
            "reconciled": reconciled,
            "expired": expired,
            "promoted": promoted,
            "released": released,
        }
        self.logger.record("tick_finished", **result)
        return result
