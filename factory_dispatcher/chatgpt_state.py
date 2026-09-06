"""Local browser evidence, readable even after the browser launcher is disabled."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .errors import ContractError
from .receipts import AttemptContext, read_json_object


def request_digest(raw: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def browser_attempt_status(state_directory: Path, job) -> dict | None:
    """Never let malformed/missing local evidence authorize an automatic resend."""
    for value in (job.dispatch_id, job.attempt_id):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", value):
            return None
    directory = state_directory / "chatgpt-runs" / job.dispatch_id / job.attempt_id
    manifest = directory / "launch.json"
    if not manifest.exists():
        return None
    try:
        raw = read_json_object(manifest)
        context = AttemptContext.from_mapping(raw["execution"])
        current = AttemptContext.from_job(job, context.receiptFolderId)
        if context != current:
            return None
        status = read_json_object(directory / "status.json")
        if not isinstance(status.get("state"), str):
            raise ContractError("Missing local browser state")
        return status
    except (ContractError, KeyError, TypeError):
        return {"state": "STOPPED", "errorCode": "CHATGPT_LOCAL_STATE_INVALID"}
