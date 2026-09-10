from __future__ import annotations

from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "factory-bridge-admin-poller.ps1"


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_admin_poller_normalizes_invoke_restmethod_arrays_before_counting():
    text = source()

    assert "$response = Invoke-RestMethod" in text
    assert "$items = @()" in text
    assert "foreach ($entry in $response) { $items += $entry }" in text
    assert "$items = @(Invoke-RestMethod" not in text
    assert "if ($items.Count -lt 100) { break }" in text


def test_admin_poller_uses_cross_process_exclusive_file_lock():
    text = source()

    assert "$instanceLockPath = Join-Path $stateDir 'admin-poller.lock'" in text
    assert "function Acquire-InstanceLock" in text
    assert "[System.IO.FileShare]::None" in text
    assert "poll skipped; another admin poller instance is active" in text
    assert "$instanceLock.Dispose()" in text


def test_admin_poller_publishes_canonical_fenced_update_result():
    text = source()

    assert "'<!-- FACTORY_BUS_V2 -->'" in text
    assert "'```json'" in text
    assert "+ '```'" in text
    assert "type = 'UPDATE_RESULT'" in text
