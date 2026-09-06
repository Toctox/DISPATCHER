from __future__ import annotations

import json

from factory_dispatcher.audit import JsonlAuditLogger
from factory_dispatcher.google_gateway import column_letter


def test_structured_log_redacts_tokens_and_prompts(tmp_path):
    logger = JsonlAuditLogger(tmp_path, console=False)
    logger.record(
        "test",
        leaseToken="super-secret",
        prompt="private-prompt",
        nested={"access_token": "abc", "safe": "visible"},
    )

    record = json.loads(logger.path.read_text(encoding="utf-8"))
    assert record["leaseToken"] == "[REDACTED]"
    assert record["prompt"] == "[REDACTED]"
    assert record["nested"]["access_token"] == "[REDACTED]"
    assert record["nested"]["safe"] == "visible"


def test_sheet_column_mapping_supports_columns_after_z():
    assert column_letter(1) == "A"
    assert column_letter(26) == "Z"
    assert column_letter(27) == "AA"
    assert column_letter(30) == "AD"
