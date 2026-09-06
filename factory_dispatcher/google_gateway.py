from __future__ import annotations

import io
import json
from datetime import datetime, timedelta
from typing import Any

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from .config import FactoryConfig, LocalSettings
from .errors import ClaimLostError, ContractError
from .models import DispatchJob, DispatchState, Receipt, isoformat

QUEUE_HEADERS = (
    "dispatchId",
    "rootRunId",
    "generation",
    "agentId",
    "changeId",
    "taskType",
    "targetRef",
    "status",
    "priority",
    "dedupeKey",
    "dependsOn",
    "attempt",
    "maxAttempts",
    "leaseOwner",
    "leaseToken",
    "leaseExpiresAt",
    "notBefore",
    "retryPolicy",
    "sideEffectClass",
    "requestDriveId",
    "stagingFolderId",
    "receiptDriveId",
    "producedArtifactIds",
    "lastErrorCode",
    "lastErrorDetail",
    "createdAt",
    "claimedAt",
    "startedAt",
    "finishedAt",
    "updatedAt",
)

EVENT_HEADERS = (
    "eventId",
    "timestamp",
    "dispatchId",
    "attemptId",
    "eventType",
    "actor",
    "detailsHash",
    "details",
)


def column_letter(number: int) -> str:
    if number < 1:
        raise ValueError("column number must be positive")
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _drive_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _parse_json_bytes(data: bytes) -> dict[str, Any]:
    text = data.decode("utf-8-sig").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"Drive artifact is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ContractError("Drive JSON artifact root must be an object")
    return payload


class GoogleWorkspaceGateway:
    def __init__(self, services: Any, settings: LocalSettings) -> None:
        self.sheets = services.sheets
        self.drive = services.drive
        self.spreadsheet_id = settings.spreadsheet_id
        self._queue_headers: list[str] = []

    def _values(self) -> Any:
        return self.sheets.spreadsheets().values()

    def read_factory_config(self) -> FactoryConfig:
        response = (
            self._values().get(spreadsheetId=self.spreadsheet_id, range="CONFIG!A1:D200").execute()
        )
        rows = response.get("values", [])
        if not rows or rows[0][:4] != ["key", "value", "type", "notes"]:
            raise ContractError("CONFIG header does not match the expected contract")
        mapping = {str(row[0]): str(row[1]) for row in rows[1:] if len(row) >= 2 and row[0]}
        return FactoryConfig.from_mapping(mapping)

    def read_queue(self) -> list[DispatchJob]:
        return self._read_queue("QUEUE!A1:AN1000")

    def read_queue_for_recovery(self) -> list[DispatchJob]:
        # Recovery must not overlook an active attempt below the tick's bounded window.
        return self._read_queue("QUEUE!A:AN")

    def _read_queue(self, queue_range: str) -> list[DispatchJob]:
        response = (
            self._values().get(spreadsheetId=self.spreadsheet_id, range=queue_range).execute()
        )
        rows = response.get("values", [])
        if not rows:
            raise ContractError("QUEUE is empty")
        self._queue_headers = [str(value) for value in rows[0]]
        missing = [header for header in QUEUE_HEADERS if header not in self._queue_headers]
        if missing:
            raise ContractError(f"QUEUE is missing headers: {', '.join(missing)}")
        jobs: list[DispatchJob] = []
        for row_number, values in enumerate(rows[1:], start=2):
            padded = list(values) + [""] * (len(self._queue_headers) - len(values))
            row = dict(zip(self._queue_headers, padded, strict=True))
            if str(row.get("dispatchId", "")).strip():
                jobs.append(DispatchJob.from_row(row_number, row))
        return jobs

    def read_job(self, row_number: int) -> DispatchJob:
        if not self._queue_headers:
            self.read_queue()
        last_column = column_letter(len(self._queue_headers))
        response = (
            self._values()
            .get(
                spreadsheetId=self.spreadsheet_id,
                range=f"QUEUE!A{row_number}:{last_column}{row_number}",
            )
            .execute()
        )
        rows = response.get("values", [])
        if not rows:
            raise ClaimLostError(f"queue row {row_number} disappeared")
        values = list(rows[0]) + [""] * (len(self._queue_headers) - len(rows[0]))
        return DispatchJob.from_row(row_number, dict(zip(self._queue_headers, values, strict=True)))

    def update_job(self, row_number: int, fields: dict[str, Any]) -> None:
        if not self._queue_headers:
            self.read_queue()
        unknown = [key for key in fields if key not in self._queue_headers]
        if unknown:
            raise ContractError(f"cannot update unknown QUEUE fields: {', '.join(unknown)}")
        data = []
        for key, value in fields.items():
            index = self._queue_headers.index(key) + 1
            data.append({"range": f"QUEUE!{column_letter(index)}{row_number}", "values": [[value]]})
        (
            self._values()
            .batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "RAW", "data": data},
            )
            .execute()
        )

    def claim_job(
        self,
        job: DispatchJob,
        *,
        owner: str,
        lease_token: str,
        now: datetime,
        lease_minutes: int,
    ) -> DispatchJob:
        current = self.read_job(job.row_number)
        if current.dispatch_id != job.dispatch_id or current.status != DispatchState.READY:
            raise ClaimLostError(f"dispatch {job.dispatch_id} is no longer READY")
        if current.not_before and current.not_before > now:
            raise ClaimLostError(f"dispatch {job.dispatch_id} is not due")
        if current.attempt >= current.max_attempts:
            raise ClaimLostError(f"dispatch {job.dispatch_id} exhausted maxAttempts")
        attempt = current.attempt + 1
        expires = now + timedelta(minutes=lease_minutes)
        self.update_job(
            current.row_number,
            {
                "status": DispatchState.CLAIMED.value,
                "attempt": attempt,
                "leaseOwner": owner,
                "leaseToken": lease_token,
                "leaseExpiresAt": isoformat(expires),
                "claimedAt": isoformat(now),
                "updatedAt": isoformat(now),
                "lastErrorCode": "",
                "lastErrorDetail": "",
            },
        )
        claimed = self.read_job(current.row_number)
        if (
            claimed.status != DispatchState.CLAIMED
            or claimed.attempt != attempt
            or claimed.lease_owner != owner
            or claimed.lease_token != lease_token
        ):
            raise ClaimLostError(f"post-write claim verification failed for {job.dispatch_id}")
        return claimed

    def append_event(self, values: list[Any]) -> None:
        if len(values) != len(EVENT_HEADERS):
            raise ContractError("EVENTS row must contain exactly eight values")
        (
            self._values()
            .append(
                spreadsheetId=self.spreadsheet_id,
                range="EVENTS!A:H",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [values]},
            )
            .execute()
        )

    def read_json_artifact(
        self, file_id: str, expected_parent_id: str | None = None
    ) -> dict[str, Any]:
        metadata = (
            self.drive.files()
            .get(fileId=file_id, fields="id,name,mimeType,parents", supportsAllDrives=True)
            .execute()
        )
        if expected_parent_id and expected_parent_id not in metadata.get("parents", []):
            raise ContractError(f"Drive artifact {file_id} is outside the required parent folder")
        mime_type = str(metadata.get("mimeType", ""))
        buffer = io.BytesIO()
        if mime_type == "application/vnd.google-apps.document":
            request = self.drive.files().export_media(fileId=file_id, mimeType="text/plain")
        elif mime_type.startswith("application/vnd.google-apps"):
            raise ContractError(f"unsupported native Drive request type: {mime_type}")
        else:
            request = self.drive.files().get_media(fileId=file_id, supportsAllDrives=True)
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return _parse_json_bytes(buffer.getvalue())

    def _find_folder(self, parent_id: str, name: str) -> str | None:
        query = (
            f"'{_drive_literal(parent_id)}' in parents and "
            f"name = '{_drive_literal(name)}' and "
            "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        response = self.drive.files().list(q=query, fields="files(id,name)", pageSize=2).execute()
        files = response.get("files", [])
        if len(files) > 1:
            raise ContractError(f"ambiguous staging folder: {name}")
        return str(files[0]["id"]) if files else None

    def _find_or_create_folder(self, parent_id: str, name: str) -> str:
        existing = self._find_folder(parent_id, name)
        if existing:
            return existing
        created = (
            self.drive.files()
            .create(
                body={
                    "name": name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                },
                fields="id",
            )
            .execute()
        )
        return str(created["id"])

    def create_staging(self, root_id: str, dispatch_id: str, attempt_id: str) -> str:
        dispatch_folder = self._find_or_create_folder(root_id, dispatch_id)
        return self._find_or_create_folder(dispatch_folder, attempt_id)

    def upload_text(self, parent_id: str, name: str, content: str) -> str:
        media = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")), mimetype="text/plain", resumable=False
        )
        created = (
            self.drive.files()
            .create(body={"name": name, "parents": [parent_id]}, media_body=media, fields="id")
            .execute()
        )
        return str(created["id"])

    def find_receipt(self, receipt_folder_id: str, name: str) -> Receipt | None:
        query = (
            f"'{_drive_literal(receipt_folder_id)}' in parents and "
            f"name = '{_drive_literal(name)}' and trashed = false"
        )
        response = self.drive.files().list(q=query, fields="files(id,name)", pageSize=2).execute()
        files = response.get("files", [])
        if not files:
            return None
        if len(files) > 1:
            raise ContractError(f"multiple terminal receipts found: {name}")
        drive_id = str(files[0]["id"])
        return Receipt(drive_id=drive_id, raw=self.read_json_artifact(drive_id))

    def validate_receipt_folder(self, folder_id: str, dispatch_folder_id: str) -> None:
        metadata = (
            self.drive.files()
            .get(fileId=folder_id, fields="id,mimeType,parents,trashed", supportsAllDrives=True)
            .execute()
        )
        if (
            metadata.get("id") != folder_id
            or metadata.get("mimeType") != "application/vnd.google-apps.folder"
            or metadata.get("trashed", False)
            or dispatch_folder_id not in metadata.get("parents", [])
        ):
            raise ContractError("Receipt destination must be a live folder directly under DISPATCH")

    def upload_receipt(self, folder_id: str, name: str, content: bytes) -> str:
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json", resumable=False)
        created = (
            self.drive.files()
            .create(
                body={"name": name, "parents": [folder_id], "mimeType": "application/json"},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return str(created["id"])
