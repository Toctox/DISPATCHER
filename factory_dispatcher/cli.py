from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import JsonlAuditLogger
from .config import load_local_settings
from .engine import Dispatcher
from .errors import DispatcherError
from .google_auth import build_google_services
from .google_gateway import GoogleWorkspaceGateway
from .launchers import (
    BrowserChatGPTLauncher,
    CodexLauncher,
    ForegroundChatGPTLauncher,
    ManualChatGPTLauncher,
)
from .launchers.chatgpt_extension import ExtensionChatGPTLauncher
from .models import ExecutorType
from .mutex import LocalMutex, MutexBusyError


def _legacy_chatgpt_launcher(settings, config_file: Path):
    chatgpt = (
        BrowserChatGPTLauncher(settings, config_file=config_file)
        if settings.browser_chatgpt_launch_enabled
        else ManualChatGPTLauncher(
            settings.chatgpt_url, enabled=settings.manual_chatgpt_launch_enabled
        )
    )
    if settings.chatgpt_browser_mode == "EXTENSION_BRIDGE":
        chatgpt = ExtensionChatGPTLauncher(settings, config_file=config_file)
    return chatgpt


def _chatgpt_launcher(settings, config_file: Path):
    mode = settings.chatgpt_execution_mode

    if mode == "FOREGROUND_DESKTOP":
        # Explicit mode never falls back. If ChatGPT.exe is unavailable the closed
        # foreground launcher fails preflight instead of silently switching surfaces.
        return ForegroundChatGPTLauncher(settings.state_directory)
    if mode == "EXTENSION_BRIDGE":
        return ExtensionChatGPTLauncher(settings, config_file=config_file)
    if mode == "BROWSER":
        return BrowserChatGPTLauncher(settings, config_file=config_file)
    if mode == "MANUAL":
        return ManualChatGPTLauncher(
            settings.chatgpt_url, enabled=settings.manual_chatgpt_launch_enabled
        )

    # AUTO exists only as a migration compatibility mode. The desktop app is
    # preferred when it is already open; otherwise the previous configuration is
    # retained. Production Factory Runtime V2 should use FOREGROUND_DESKTOP.
    if ForegroundChatGPTLauncher.available():
        return ForegroundChatGPTLauncher(settings.state_directory)
    return _legacy_chatgpt_launcher(settings, config_file)


def build_launchers(settings, config_file: Path):
    return {
        ExecutorType.CHATGPT: _chatgpt_launcher(settings, config_file),
        ExecutorType.CODEX: CodexLauncher(
            settings.codex_executable,
            settings.state_directory,
            enabled=settings.codex_launch_enabled,
            config_file=config_file.resolve(),
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one mechanical PROJECT FACTORY dispatcher tick."
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument(
        "--tick",
        action="store_true",
        help="run exactly one dispatcher cycle and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read CONFIG/QUEUE and select mechanically without writes or launch",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not arguments.tick and not arguments.dry_run:
        build_parser().error("use --tick (or --dry-run for a read-only validation)")
    try:
        settings = load_local_settings(arguments.config)
        logger = JsonlAuditLogger(settings.log_directory)
        with LocalMutex(settings.mutex_file):
            services = build_google_services(settings)
            gateway = GoogleWorkspaceGateway(services, settings)
            launchers = build_launchers(settings, arguments.config)
            result = Dispatcher(gateway, settings, logger, launchers).tick(
                dry_run=arguments.dry_run
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 1 if result.get("outcome") in {"FAILED", "PREFLIGHT_BLOCKED"} else 0
    except MutexBusyError as exc:
        print(json.dumps({"outcome": "SKIPPED_MUTEX_BUSY", "detail": str(exc)}))
        return 0
    except DispatcherError as exc:
        print(
            json.dumps(
                {"outcome": "ERROR", "errorType": type(exc).__name__, "detail": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {"outcome": "ERROR", "errorType": type(exc).__name__, "detail": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 3
