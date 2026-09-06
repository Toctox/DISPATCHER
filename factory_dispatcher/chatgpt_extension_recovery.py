"""Evidence-gated recovery, invoked only by the explicit authenticated recovery RPC.

No tick, browser automation, queue writes, receipt upload, or retry belongs here.
The bridge's rpc_lock is held from validation through the release acknowledgement.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import uuid
from contextlib import ExitStack
from pathlib import Path

from .chatgpt_browser import ChatGPTError
from .chatgpt_extension_protocol import (
    RECOVERY_RELEASE,
    VERSION,
    binding,
    check_settings,
    command,
    durable_write,
    fail,
    recovery_ids,
    tab_lock,
)
from .models import ACTIVE_STATES, DispatchRequest, DispatchState, ExecutorType
from .mutex import LocalMutex, MutexBusyError
from .receipts import MAX_JSON_BYTES, AttemptContext, decode_json_object, read_json_object

# Deliberately narrow: the worker can emit this code only from NEW_CHAT, before SEND.
# Adding a code requires proving its position in BOTH write-ahead state machines.
PRE_SEND_ERRORS = frozenset({"CHATGPT_NEW_CHAT_FAILED"})
PRE_SEND_PHASE = "NEW_CHAT_ATTEMPTED"
ATTEMPT_FILES = frozenset(
    {"launch.json", "status.json", "browser.log", "bootstrap.txt", "worker.lock"}
)


class RecoveryRefused(Exception):
    def __init__(self, outcome):
        self.outcome = outcome
        super().__init__(outcome)


def require(condition, outcome):
    if not condition:
        raise RecoveryRefused(outcome)


def contained(path, root):
    require(path.resolve().is_relative_to(root.resolve()), "REFUSED_PATH")
    current = path
    while current != root:
        require(not linked(current), "REFUSED_PATH")
        require(current != current.parent, "REFUSED_PATH")
        current = current.parent
    require(not linked(root), "REFUSED_PATH")


def linked(path):
    # Path.is_junction only exists in Python 3.12+; the project supports 3.11 too.
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", None) == getattr(
        stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003
    )


class RecoveryAudit:
    """Exactly five non-secret fields; write-ahead record survives an ambiguous ACK."""

    def __init__(self, settings, dispatch_id, attempt_id, previous=None):
        recovery_ids(dispatch_id, attempt_id)
        root = settings.project_root / "state"
        self.path = root / "chatgpt-bridge" / "recovery-audit" / f"{uuid.uuid4()}.json"
        contained(self.path, root)
        self.payload = {
            "dispatchId": dispatch_id,
            "attemptId": attempt_id,
            "previousReservationPresent": previous,
            "evidenceSendAttemptedFalse": False,
            "outcome": "NOT_VALIDATED",
        }

    def record(self, outcome):
        self.payload["outcome"] = outcome
        self.path.parent.mkdir(parents=True, exist_ok=True)
        durable_write(self.path, self.payload)


def bounded_bytes(path):
    with path.open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    require(len(raw) <= MAX_JSON_BYTES, "REFUSED_EVIDENCE_SIZE")
    return raw


def no_send_or_receipt(value):
    if isinstance(value, dict):
        for key, item in value.items():
            require("SEND_ATTEMPTED" not in key, "REFUSED_SEND_EVIDENCE")
            if key == "sendAttempted":
                require(item is False, "REFUSED_SEND_EVIDENCE")
            if key in {"sendAttemptedAt", "sendConfirmedAt"}:
                require(not item, "REFUSED_SEND_EVIDENCE")
            if key == "operationalSuccess":
                require(item is False, "REFUSED_SEND_EVIDENCE")
            if key in {"receiptDriveId", "receiptStatus", "receipt", "receiptFound"}:
                require(not item, "REFUSED_RECEIPT_PRESENT")
            no_send_or_receipt(item)
    elif isinstance(value, list):
        for item in value:
            no_send_or_receipt(item)
    elif isinstance(value, str):
        require("SEND_ATTEMPTED" not in value, "REFUSED_SEND_EVIDENCE")
        require(value != "EXECUTION_RECEIPT", "REFUSED_RECEIPT_PRESENT")


def local_state_evidence(settings, context):
    """Include receipts under noncanonical filenames and evidence outside the attempt."""
    root = settings.state_directory
    identity_bytes = tuple(
        identity.encode("utf-8") for identity in (context.dispatchId, context.attemptId)
    )

    def walk_error(error):
        raise error  # os.walk must not silently omit unreadable evidence.

    def associated(value):
        if isinstance(value, dict):
            return any(associated(item) for item in value.values())
        if isinstance(value, list):
            return any(associated(item) for item in value)
        return isinstance(value, str) and any(
            identity in value for identity in (context.dispatchId, context.attemptId)
        )

    for directory, subdirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        parent = Path(directory)
        # Native browser profile data isn't dispatcher evidence. Never inspect cookies,
        # browser storage, or chat content. The extension uses its authenticated control.
        subdirs[:] = [
            name for name in subdirs if (parent / name).resolve() != settings.chatgpt_profile
        ]
        for name in subdirs:
            contained(parent / name, root)
        for name in names:
            path = parent / name
            if name == context.receipt_name:
                raise RecoveryRefused("REFUSED_RECEIPT_PRESENT")
            if path.suffix not in {".json", ".jsonl", ".log", ".tmp"}:
                continue  # bootstrap text, images and lock bytes are not execution events.
            contained(path, root)
            raw = bounded_bytes(path)
            candidates = (raw,) if path.suffix in {".json", ".tmp"} else raw.splitlines()
            for candidate in candidates:
                # Literal, case-sensitive IDs only. Unrelated bytes need not be JSON
                # (or UTF-8); associated bytes MUST pass the strict decoder below.
                if not any(identity in candidate for identity in identity_bytes):
                    continue
                value = decode_json_object(candidate)
                if associated(value):
                    no_send_or_receipt(value)


class LocalEvidence:
    def __init__(self, settings, dispatch_id, attempt_id, stack):
        self.settings = settings
        self.dispatch_id, self.attempt_id = dispatch_id, attempt_id
        recovery_ids(dispatch_id, attempt_id)
        contained(settings.state_directory, settings.project_root / "state")
        self.root = settings.state_directory / "chatgpt-runs"
        self.directory = self.root / dispatch_id / attempt_id
        contained(self.directory, settings.project_root / "state")
        require((self.directory / "status.json").is_file(), "REFUSED_STATUS_MISSING")
        self.directories = self.attempt_directories()
        # An old-looking status is not proof that its worker has exited.
        for directory in self.directories:
            contained(directory / "worker.lock", self.root)
            stack.enter_context(LocalMutex(directory / "worker.lock"))

    def attempt_directories(self):
        directories = []
        for dispatch in sorted(self.root.iterdir()):
            contained(dispatch, self.root)
            require(dispatch.is_dir(), "REFUSED_LOCAL_LAYOUT")
            for attempt in sorted(dispatch.iterdir()):
                contained(attempt, self.root)
                require(attempt.is_dir(), "REFUSED_LOCAL_LAYOUT")
                directories.append(attempt)
        require(self.directory in directories, "REFUSED_ATTEMPT_MISMATCH")
        return directories

    def verify(self, reservation):
        require(self.attempt_directories() == self.directories, "REFUSED_OTHER_ATTEMPT")
        for directory in self.directories:
            if directory == self.directory:
                continue
            status_path = directory / "status.json"
            contained(status_path, self.root)
            other = read_json_object(status_path)
            require(
                other.get("dispatchId") == directory.parent.name
                and other.get("attemptId") == directory.name
                and other.get("state") in {"STOPPED", "FINISHED"},
                "REFUSED_OTHER_ATTEMPT",
            )
            # A duplicate reservation ID in another manifest makes attribution ambiguous.
            manifest_path = directory / "launch.json"
            contained(manifest_path, self.root)
            other_manifest = read_json_object(manifest_path)
            require(
                other_manifest.get("reservationId") != reservation.get("reservationId"),
                "REFUSED_ATTEMPT_MISMATCH",
            )
        files = {}
        for path in self.directory.iterdir():
            contained(path, self.root)
            require("receipt" not in path.name.lower(), "REFUSED_RECEIPT_PRESENT")
            require(path.is_file() and path.name in ATTEMPT_FILES, "REFUSED_UNKNOWN_EVIDENCE")
            # Windows denies a second read handle to the lock we already own.
            # Its bytes are not execution evidence; ownership is checked above.
            files[path.name] = b"" if path.name == "worker.lock" else bounded_bytes(path)
        require(ATTEMPT_FILES <= files.keys(), "REFUSED_INCOMPLETE_EVIDENCE")
        status = decode_json_object(files["status.json"])
        manifest = decode_json_object(files["launch.json"])
        context = AttemptContext.from_mapping(manifest.get("execution"))
        require(
            context.dispatchId == self.dispatch_id
            and context.attemptId == self.attempt_id
            and status.get("dispatchId") == self.dispatch_id
            and status.get("attemptId") == self.attempt_id
            and status.get("requestDriveId") == context.requestDriveId,
            "REFUSED_ATTEMPT_MISMATCH",
        )
        require(status.get("state") == "STOPPED", "REFUSED_NOT_STOPPED")
        require(status.get("sendAttempted") is False, "REFUSED_SEND_EVIDENCE")
        require(status.get("operationalSuccess") is False, "REFUSED_OPERATIONAL_SUCCESS")
        require(status.get("errorCode") in PRE_SEND_ERRORS, "REFUSED_NOT_PRE_SEND")
        require(status.get("needsReconciliation") is True, "REFUSED_RECONCILIATION_STATE")
        no_send_or_receipt(status)
        require(
            type(manifest.get("manifestVersion")) is int
            and manifest["manifestVersion"] == 3
            and manifest.get("executorType") == "CHATGPT"
            and manifest.get("browserMode") == "EXTENSION_BRIDGE",
            "REFUSED_MANIFEST",
        )
        pinned = binding(manifest.get("extensionBinding"))
        require(pinned["extensionId"] == self.settings.chatgpt_extension_id, "REFUSED_BINDING")
        expected = {
            "reservationId": manifest.get("reservationId"),
            "binding": pinned,
            "phase": PRE_SEND_PHASE,
        }
        # Strict command validation also checks the reservation ID format.
        release = command(
            {
                "protocolVersion": VERSION,
                "type": "CONTROL",
                "control": RECOVERY_RELEASE,
                "requestId": str(uuid.uuid4()),
                "binding": pinned,
                "reservationId": expected["reservationId"],
            }
        )
        require(reservation == expected, "REFUSED_RESERVATION_MISMATCH")
        require(
            hashlib.sha256(files["bootstrap.txt"]).hexdigest() == manifest.get("bootstrapHash"),
            "REFUSED_MANIFEST",
        )
        no_send_or_receipt(manifest)
        entries = [decode_json_object(line) for line in files["browser.log"].splitlines()]
        require(
            [entry.get("state") for entry in entries]
            == ["STARTING", "PREFLIGHT", "PREPARING_TAB", "STOPPED"]
            and entries[-1].get("errorCode") == status["errorCode"],
            "REFUSED_LOG_HISTORY",
        )
        for entry in entries:
            no_send_or_receipt(entry)
        local_state_evidence(self.settings, context)
        digest = hashlib.sha256(b"".join(files[key] for key in sorted(files))).digest()
        return context, release, digest


def readonly_gateway(settings):
    from .google_auth import build_google_services
    from .google_gateway import GoogleWorkspaceGateway

    services = build_google_services(settings, interactive=False, http_timeout=10)
    return GoogleWorkspaceGateway(services, settings)


def verify_remote(settings, context):
    gateway = readonly_gateway(settings)
    factory = gateway.read_factory_config()
    require(factory.receipts_folder_id == context.receiptFolderId, "REFUSED_RECEIPT_FOLDER")
    gateway.validate_receipt_folder(context.receiptFolderId, factory.dispatch_folder_id)
    jobs = gateway.read_queue_for_recovery()
    matches = [job for job in jobs if job.dispatch_id == context.dispatchId]
    require(len(matches) == 1, "REFUSED_QUEUE_IDENTITY")
    current = matches[0]
    context.match_receipt(AttemptContext.from_job(current, context.receiptFolderId).to_dict())
    require(current.status == DispatchState.WAITING_HUMAN, "REFUSED_QUEUE_STATE")
    require(not current.receipt_drive_id, "REFUSED_RECEIPT_PRESENT")
    for job in jobs:
        if job == current or job.status in ACTIVE_STATES:
            request = DispatchRequest(
                gateway.read_json_artifact(job.request_drive_id, factory.requests_folder_id)
            )
            require(request.raw.get("dispatchId") == job.dispatch_id, "REFUSED_QUEUE_IDENTITY")
            if job == current:
                require(request.executor_type == ExecutorType.CHATGPT, "REFUSED_QUEUE_IDENTITY")
            else:
                require(request.executor_type != ExecutorType.CHATGPT, "REFUSED_OTHER_ATTEMPT")
    # Existing invalid/ambiguous receipts also block: validity cannot be used to discard evidence.
    require(
        gateway.find_receipt(context.receiptFolderId, context.receipt_name) is None,
        "REFUSED_RECEIPT_PRESENT",
    )


async def recover_reservation(bridge, request):
    settings = bridge.settings
    audit = RecoveryAudit(
        settings, request["dispatchId"], request["attemptId"], bool(bridge.reservation.active)
    )
    release_started = False
    try:
        check_settings(settings)
        require(bridge.rpc_lock.locked(), "REFUSED_EXCLUSION")
        require(bridge.extension is not None, "REFUSED_EXTENSION_UNAVAILABLE")
        with ExitStack() as stack:
            stack.enter_context(LocalMutex(settings.mutex_file))
            stack.enter_context(tab_lock(settings))
            evidence = LocalEvidence(settings, request["dispatchId"], request["attemptId"], stack)
            contained(bridge.reservation.path, settings.project_root / "state")
            previous = read_json_object(bridge.reservation.path)
            audit.payload["previousReservationPresent"] = bool(previous)
            require(previous == bridge.reservation.active, "REFUSED_RESERVATION_MISMATCH")
            context, release, digest = evidence.verify(previous)
            audit.payload["evidenceSendAttemptedFalse"] = True
            await asyncio.to_thread(verify_remote, settings, context)
            # Locks exclude normal producers; recheck all local evidence after network I/O.
            require(evidence.verify(previous)[2] == digest, "REFUSED_EVIDENCE_CHANGED")
            require(
                read_json_object(bridge.reservation.path) == previous == bridge.reservation.active,
                "REFUSED_RESERVATION_MISMATCH",
            )
            audit.record("VALIDATED_RELEASE_PENDING")  # Failure to persist => no release.
            release_started = True
            response = await bridge.exchange(release)
            if response["status"] != "OK":
                # Extension's comparison failures are explicit no-write refusals.
                if response.get("errorCode") in {
                    "CHATGPT_RESERVATION_INVALID",
                    "CHATGPT_TAB_BUSY",
                    "CHATGPT_PROTOCOL_INVALID",
                }:
                    release_started = False
                    raise RecoveryRefused("REFUSED_EXTENSION_RESERVATION")
                fail("CHATGPT_RECOVERY_UNCERTAIN")
            # ACK first, durable barrier second, in-memory release last. Never clear on timeout.
            durable_write(bridge.reservation.path, {})
            bridge.reservation.active = {}
            audit.record("RELEASED_PRE_SEND")
    except BaseException as exc:
        if release_started:
            outcome = "RECOVERY_UNCERTAIN"
        elif isinstance(exc, RecoveryRefused):
            outcome = exc.outcome
        elif isinstance(exc, MutexBusyError):
            outcome = "REFUSED_ACTIVE_LOCK"
        else:
            outcome = "REFUSED_UNVERIFIABLE_EVIDENCE"
        # No exception details, payloads, paths, credentials, timestamps, or lease tokens.
        try:
            audit.record(outcome)
        except Exception:
            pass  # The durable PENDING record, if any, must never be treated as success.
        if not isinstance(exc, Exception):
            raise
        raise ChatGPTError(
            "CHATGPT_RECOVERY_UNCERTAIN" if release_started else "CHATGPT_RECOVERY_REFUSED"
        ) from None
