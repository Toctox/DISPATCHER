from __future__ import annotations

import json

import pytest

from factory_dispatcher.config import load_local_settings
from factory_dispatcher.errors import ConfigurationError
from factory_dispatcher.google_auth import build_google_services


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_configuration_accepts_utf8_with_or_without_bom(tmp_path, encoding):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "spreadsheetId": "sheet-test",
                "claimOwner": "Operador de execução",
                "writesEnabled": False,
                "codexLaunchEnabled": False,
            },
            ensure_ascii=False,
        ),
        encoding=encoding,
    )
    original = path.read_bytes()
    settings = load_local_settings(path)
    assert settings.spreadsheet_id == "sheet-test"
    assert settings.claim_owner == "Operador de execução"
    assert settings.codex_launch_enabled is False
    assert settings.writes_enabled is False
    assert path.read_bytes() == original


def test_worker_oauth_does_not_start_interactive_login(local_settings, monkeypatch):
    def forbidden_browser(*args, **kwargs):
        raise AssertionError("Worker must never start OAuth browser login")

    monkeypatch.setattr(
        "factory_dispatcher.google_auth.InstalledAppFlow.from_client_secrets_file",
        forbidden_browser,
    )
    with pytest.raises(ConfigurationError, match="existing valid token"):
        build_google_services(local_settings, interactive=False)
