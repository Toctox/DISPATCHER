"""Mechanical receipt upload and explicit recovery; never launches a dispatch."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audit import JsonlAuditLogger
from .config import LocalSettings, load_local_settings
from .errors import ContractError
from .google_auth import build_google_services
from .google_gateway import GoogleWorkspaceGateway
from .models import isoformat
from .mutex import LocalMutex
from .receipts import (
    MAX_JSON_BYTES,
    AttemptContext,
    decode_json_object,
    read_json_object,
    validate_receipt,
    write_json,
)


class ReceiptTransport:
    def __init__(
        self,
        gateway: Any,
        settings: LocalSettings,
        logger: Any,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.gateway = gateway
        self.settings = settings
        self.logger = logger
        self.clock = clock or (lambda: datetime.now(UTC))

    def send(self, file: Path, *, expected: AttemptContext | None = None) -> dict[str, Any]:
        with LocalMutex(self.settings.mutex_file):
            return self._send_locked(file, expected=expected)

    def _send_locked(self, file: Path, *, expected: AttemptContext | None = None) -> dict[str, Any]:
        if not self.settings.writes_enabled:
            raise ContractError("Receipt transport requires writesEnabled=true")
        file = file.resolve()
        with file.open("rb") as handle:
            content = handle.read(MAX_JSON_BYTES + 1)
        receipt = decode_json_object(content)
        validate_receipt(receipt)
        factory = self.gateway.read_factory_config()
        folder_id = factory.receipts_folder_id
        context = expected or AttemptContext.from_mapping({**receipt, "receiptFolderId": folder_id})
        if folder_id != context.receiptFolderId:
            raise ContractError("receiptFolderId changed since the attempt was launched")
        context.match_receipt(receipt)
        if folder_id in {factory.staging_folder_id, factory.archive_folder_id}:
            raise ContractError("Receipt destination cannot be staging or archive")

        # A new worker pins its destination in launch.json; recovery reuses that binding.
        manifest_path = (
            self.settings.state_directory
            / "codex-runs"
            / context.dispatchId
            / context.attemptId
            / "launch.json"
        )
        if manifest_path.exists():
            manifest = read_json_object(manifest_path)
            if "execution" in manifest:
                pinned = AttemptContext.from_mapping(manifest["execution"])
                if pinned != context:
                    raise ContractError("Local attempt manifest does not match receipt transport")

        jobs = [job for job in self.gateway.read_queue() if job.dispatch_id == context.dispatchId]
        if len(jobs) != 1:
            raise ContractError("Receipt dispatchId must identify exactly one QUEUE row")
        context.match_current_job(jobs[0], self.clock())
        self.gateway.validate_receipt_folder(folder_id, factory.dispatch_folder_id)
        existing = self.gateway.find_receipt(folder_id, context.receipt_name)
        if existing is not None:
            validate_receipt(existing.raw)
            context.match_receipt(existing.raw)
            if existing.raw != receipt:
                raise ContractError("A different receipt already exists for this attempt")

        content_hash = hashlib.sha256(content).hexdigest()
        sidecar = file.with_name(file.name + ".transport.json")
        record = read_json_object(sidecar) if sidecar.exists() else {}
        if record and (
            record.get("receiptHash") != content_hash or record.get("receiptFolderId") != folder_id
        ):
            raise ContractError("Receipt content or destination changed since transport started")
        record.update({"receiptHash": content_hash, "receiptFolderId": folder_id})
        write_json(sidecar, record)

        # Do this after folder lookup and duplicate detection, immediately before upload.
        current = self.gateway.read_job(jobs[0].row_number)
        context.match_current_job(current, self.clock())
        if existing is None:
            drive_id = self.gateway.upload_receipt(folder_id, context.receipt_name, content)
        else:
            drive_id = existing.drive_id
        record.update({"receiptDriveId": drive_id, "uploadedAt": isoformat(self.clock())})
        write_json(sidecar, record)

        event_id = (
            "receipt-upload-"
            + hashlib.sha256(
                f"{context.dispatchId}/{context.attemptId}/{drive_id}".encode()
            ).hexdigest()
        )
        if not record.get("eventRecorded"):
            details = json.dumps(
                {"receiptDriveId": drive_id, "receiptHash": content_hash}, sort_keys=True
            )
            self.gateway.append_event(
                [
                    event_id,
                    isoformat(self.clock()),
                    context.dispatchId,
                    context.attemptId,
                    "RECEIPT_UPLOADED",
                    self.settings.claim_owner,
                    hashlib.sha256(details.encode("utf-8")).hexdigest(),
                    details,
                ]
            )
            record.update({"eventRecorded": True, "eventId": event_id})
            write_json(sidecar, record)
        outcome = "ALREADY_UPLOADED" if existing is not None else "UPLOADED"
        self.logger.record(
            "receipt_transport_finished",
            outcome=outcome,
            dispatchId=context.dispatchId,
            attemptId=context.attemptId,
            receiptDriveId=drive_id,
        )
        return {"outcome": outcome, "receiptDriveId": drive_id}


def transport_file(
    file: Path, config_file: Path, *, expected: AttemptContext | None = None
) -> dict[str, Any]:
    settings = load_local_settings(config_file)
    if not settings.writes_enabled:
        raise ContractError("Receipt transport requires writesEnabled=true")
    validate_receipt(read_json_object(file))
    with LocalMutex(settings.mutex_file):
        services = build_google_services(settings, interactive=False)
        gateway = GoogleWorkspaceGateway(services, settings)
        logger = JsonlAuditLogger(settings.log_directory)
        return ReceiptTransport(gateway, settings, logger)._send_locked(file, expected=expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upload a local receipt for its current live lease."
    )
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.json"
    )
    args = parser.parse_args(argv)
    try:
        result = transport_file(args.file, args.config)
    except Exception as exc:
        # Do not expose API error bodies or receipt contents (which contain a lease secret).
        print(json.dumps({"outcome": "TRANSPORT_FAILED", "errorType": type(exc).__name__}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
