from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from factory_dispatcher.cli import build_launchers
from factory_dispatcher.config import load_local_settings
from factory_dispatcher.errors import ConfigurationError
from factory_dispatcher.launchers import (
    BrowserChatGPTLauncher,
    ForegroundChatGPTLauncher,
    ManualChatGPTLauncher,
)
from factory_dispatcher.launchers.chatgpt_extension import ExtensionChatGPTLauncher
from factory_dispatcher.models import ExecutorType


@pytest.mark.parametrize(
    ("mode", "expected_type"),
    [
        ("FOREGROUND_DESKTOP", ForegroundChatGPTLauncher),
        ("EXTENSION_BRIDGE", ExtensionChatGPTLauncher),
        ("BROWSER", BrowserChatGPTLauncher),
        ("MANUAL", ManualChatGPTLauncher),
    ],
)
def test_explicit_chatgpt_mode_selects_exact_launcher(
    local_settings, tmp_path: Path, mode, expected_type
):
    settings = replace(local_settings, chatgpt_execution_mode=mode)
    launchers = build_launchers(settings, tmp_path / "config.json")
    assert isinstance(launchers[ExecutorType.CHATGPT], expected_type)


def test_foreground_mode_does_not_require_window_during_launcher_selection(
    local_settings, tmp_path: Path, monkeypatch
):
    settings = replace(local_settings, chatgpt_execution_mode="FOREGROUND_DESKTOP")
    monkeypatch.setattr(ForegroundChatGPTLauncher, "available", classmethod(lambda cls: False))
    launchers = build_launchers(settings, tmp_path / "config.json")
    assert isinstance(launchers[ExecutorType.CHATGPT], ForegroundChatGPTLauncher)


def test_auto_preserves_desktop_preference_when_app_is_available(
    local_settings, tmp_path: Path, monkeypatch
):
    settings = replace(local_settings, chatgpt_execution_mode="AUTO")
    monkeypatch.setattr(ForegroundChatGPTLauncher, "available", classmethod(lambda cls: True))
    launchers = build_launchers(settings, tmp_path / "config.json")
    assert isinstance(launchers[ExecutorType.CHATGPT], ForegroundChatGPTLauncher)


def test_invalid_chatgpt_execution_mode_fails_closed(tmp_path: Path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "spreadsheetId": "sheet-test",
                "writesEnabled": False,
                "chatGptExecutionMode": "RANDOM_AUTOMATION",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="chatGptExecutionMode"):
        load_local_settings(config)
