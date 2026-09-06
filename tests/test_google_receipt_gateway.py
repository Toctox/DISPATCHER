from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory_dispatcher.errors import ContractError
from factory_dispatcher.google_gateway import GoogleWorkspaceGateway


def test_google_upload_uses_json_mime_type_and_original_content(local_settings):
    drive = Mock()
    drive.files.return_value.create.return_value.execute.return_value = {"id": "receipt-id"}
    gateway = GoogleWorkspaceGateway(SimpleNamespace(sheets=Mock(), drive=drive), local_settings)
    content = b'{"artifactType":"EXECUTION_RECEIPT"}\n'
    assert gateway.upload_receipt("receipt-folder", "receipt.json", content) == "receipt-id"
    call = drive.files.return_value.create.call_args.kwargs
    assert call["body"] == {
        "name": "receipt.json",
        "parents": ["receipt-folder"],
        "mimeType": "application/json",
    }
    assert call["media_body"].mimetype() == "application/json"
    assert call["media_body"].getbytes(0, len(content)) == content
    assert call["supportsAllDrives"] is True


@pytest.mark.parametrize(
    ("field", "value", "valid"),
    [
        ("id", "receipt-folder", True),
        ("id", "other-folder", False),
        ("mimeType", "application/json", False),
        ("trashed", True, False),
        ("parents", ["canonical-folder"], False),
        ("parents", [], False),
    ],
)
def test_receipt_destination_requires_live_folder_under_dispatch(
    local_settings, field, value, valid
):
    drive = Mock()
    metadata = {
        "id": "receipt-folder",
        "mimeType": "application/vnd.google-apps.folder",
        "trashed": False,
        "parents": ["dispatch-root"],
    }
    metadata[field] = value
    drive.files.return_value.get.return_value.execute.return_value = metadata
    gateway = GoogleWorkspaceGateway(SimpleNamespace(sheets=Mock(), drive=drive), local_settings)
    if valid:
        gateway.validate_receipt_folder("receipt-folder", "dispatch-root")
    else:
        with pytest.raises(ContractError):
            gateway.validate_receipt_folder("receipt-folder", "dispatch-root")
    drive.files.return_value.create.assert_not_called()
