from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError

DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)

CHATGPT_EXECUTION_MODES = frozenset(
    {
        "AUTO",
        "FOREGROUND_DESKTOP",
        "EXTENSION_BRIDGE",
        "BROWSER",
        "MANUAL",
    }
)


@dataclass(frozen=True)
class LocalSettings:
    project_root: Path
    spreadsheet_id: str
    credentials_file: Path
    token_file: Path
    state_directory: Path
    claim_owner: str
    writes_enabled: bool
    manual_chatgpt_launch_enabled: bool
    codex_launch_enabled: bool
    chatgpt_url: str
    codex_executable: str
    google_scopes: tuple[str, ...]
    chatgpt_execution_mode: str = "AUTO"
    browser_chatgpt_launch_enabled: bool = False
    chatgpt_browser_executable: str = ""
    chatgpt_browser_profile_directory: Path | None = None
    chatgpt_browser_timeout_seconds: int = 2700
    chatgpt_browser_poll_seconds: int = 15
    chatgpt_browser_mode: str = "DISABLED"
    chatgpt_cdp_endpoint: str = ""
    chatgpt_extension_bridge_enabled: bool = False
    chatgpt_extension_bridge_host: str = "127.0.0.1"
    chatgpt_extension_bridge_port: int = 8765
    chatgpt_extension_id: str = ""
    chatgpt_extension_pairing_secret_file: Path | None = None

    @property
    def chatgpt_pairing_file(self) -> Path:
        return self.chatgpt_extension_pairing_secret_file or (
            self.state_directory / "chatgpt-bridge" / "pairing-secret.json"
        )

    @property
    def chatgpt_profile(self) -> Path:
        return (
            self.chatgpt_browser_profile_directory or (self.state_directory / "factory-browser")
        ).resolve()

    @property
    def log_directory(self) -> Path:
        return self.state_directory / "logs"

    @property
    def mutex_file(self) -> Path:
        return self.state_directory / "dispatcher.lock"


@dataclass(frozen=True)
class FactoryConfig:
    dispatch_folder_id: str
    requests_folder_id: str
    staging_folder_id: str
    receipts_folder_id: str
    archive_folder_id: str
    bootstrap_doc_id: str
    protocol_doc_id: str
    default_lease_minutes: int
    default_max_attempts: int
    retry_backoff_minutes: int
    max_concurrent_chatgpt_executions: int
    max_executions_per_root_run: int

    @classmethod
    def from_mapping(cls, values: dict[str, str]) -> FactoryConfig:
        required = (
            "DISPATCH_FOLDER_ID",
            "REQUESTS_FOLDER_ID",
            "STAGING_FOLDER_ID",
            "RECEIPTS_FOLDER_ID",
            "ARCHIVE_FOLDER_ID",
            "BOOTSTRAP_DOC_ID",
            "DISPATCH_PROTOCOL_DOC_ID",
        )
        missing = [key for key in required if not values.get(key)]
        if missing:
            raise ConfigurationError(f"missing CONFIG keys: {', '.join(missing)}")

        def integer(key: str, default: int) -> int:
            try:
                value = int(values.get(key, default))
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(f"CONFIG {key} must be an integer") from exc
            if value < 1:
                raise ConfigurationError(f"CONFIG {key} must be positive")
            return value

        return cls(
            dispatch_folder_id=values["DISPATCH_FOLDER_ID"],
            requests_folder_id=values["REQUESTS_FOLDER_ID"],
            staging_folder_id=values["STAGING_FOLDER_ID"],
            receipts_folder_id=values["RECEIPTS_FOLDER_ID"],
            archive_folder_id=values["ARCHIVE_FOLDER_ID"],
            bootstrap_doc_id=values["BOOTSTRAP_DOC_ID"],
            protocol_doc_id=values["DISPATCH_PROTOCOL_DOC_ID"],
            default_lease_minutes=integer("DEFAULT_LEASE_MINUTES", 45),
            default_max_attempts=integer("DEFAULT_MAX_ATTEMPTS", 3),
            retry_backoff_minutes=integer("RETRY_BACKOFF_MINUTES", 5),
            max_concurrent_chatgpt_executions=integer("MAX_CONCURRENT_CHATGPT_EXECUTIONS", 1),
            max_executions_per_root_run=integer("MAX_EXECUTIONS_PER_ROOT_RUN", 25),
        )


def _resolve(base: Path, value: Any, default: str) -> Path:
    path = Path(str(value or default)).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_local_settings(path: Path) -> LocalSettings:
    path = path.resolve()
    if not path.exists():
        raise ConfigurationError(
            f"configuration not found: {path}; copy config.example.json to config.json"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read configuration: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError("configuration root must be a JSON object")
    project_root = path.parent
    spreadsheet_id = str(data.get("spreadsheetId", "")).strip()
    if not spreadsheet_id:
        raise ConfigurationError("spreadsheetId is required")
    scopes = tuple(str(item).strip() for item in data.get("googleScopes", DEFAULT_SCOPES))
    if not scopes or any(not item for item in scopes):
        raise ConfigurationError("googleScopes must contain non-empty OAuth scopes")

    def browser_integer(key: str, default: int, maximum: int) -> int:
        value = data.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ConfigurationError(f"{key} must be an integer between 1 and {maximum}")
        return value

    execution_mode = str(data.get("chatGptExecutionMode") or "AUTO").strip().upper()
    if execution_mode not in CHATGPT_EXECUTION_MODES:
        allowed = ", ".join(sorted(CHATGPT_EXECUTION_MODES))
        raise ConfigurationError(
            f"chatGptExecutionMode must be one of: {allowed}"
        )

    return LocalSettings(
        project_root=project_root,
        spreadsheet_id=spreadsheet_id,
        credentials_file=_resolve(
            project_root, data.get("credentialsFile"), "secrets/credentials.json"
        ),
        token_file=_resolve(project_root, data.get("tokenFile"), "secrets/token.json"),
        state_directory=_resolve(project_root, data.get("stateDirectory"), "state"),
        claim_owner=str(data.get("claimOwner") or socket.gethostname()).strip(),
        writes_enabled=data.get("writesEnabled") is True,
        manual_chatgpt_launch_enabled=data.get("manualChatGptLaunchEnabled") is True,
        codex_launch_enabled=data.get("codexLaunchEnabled") is True,
        chatgpt_url=str(data.get("chatGptUrl") or "https://chatgpt.com/").strip(),
        codex_executable=str(data.get("codexExecutable") or "codex").strip(),
        google_scopes=scopes,
        chatgpt_execution_mode=execution_mode,
        browser_chatgpt_launch_enabled=data.get("browserChatGptLaunchEnabled") is True,
        chatgpt_browser_executable=str(data.get("chatGptBrowserExecutable") or "").strip(),
        chatgpt_browser_profile_directory=(
            _resolve(project_root, data["chatGptBrowserProfileDirectory"], "")
            if data.get("chatGptBrowserProfileDirectory")
            else None
        ),
        chatgpt_browser_timeout_seconds=browser_integer(
            "chatGptBrowserTimeoutSeconds", 2700, 14400
        ),
        chatgpt_browser_poll_seconds=browser_integer("chatGptBrowserPollSeconds", 15, 60),
        chatgpt_browser_mode=str(data.get("chatGptBrowserMode") or "DISABLED"),
        chatgpt_cdp_endpoint=str(data.get("chatGptCdpEndpoint") or ""),
        chatgpt_extension_bridge_enabled=data.get("chatGptExtensionBridgeEnabled") is True,
        chatgpt_extension_bridge_host=str(data.get("chatGptExtensionBridgeHost", "127.0.0.1")),
        chatgpt_extension_bridge_port=browser_integer("chatGptExtensionBridgePort", 8765, 65535),
        chatgpt_extension_id=str(data.get("chatGptExtensionId") or ""),
        chatgpt_extension_pairing_secret_file=(
            _resolve(project_root, data["chatGptExtensionPairingSecretFile"], "")
            if data.get("chatGptExtensionPairingSecretFile")
            else None
        ),
    )
