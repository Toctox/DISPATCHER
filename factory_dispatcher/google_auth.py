from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import LocalSettings
from .errors import ConfigurationError


@dataclass(frozen=True)
class GoogleServices:
    sheets: Any
    drive: Any


def build_google_services(
    settings: LocalSettings, *, interactive: bool = True, http_timeout: float | None = None
) -> GoogleServices:
    credentials: Credentials | None = None
    if settings.token_file.exists():
        credentials = Credentials.from_authorized_user_file(
            str(settings.token_file), list(settings.google_scopes)
        )
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not interactive:
                raise ConfigurationError(
                    "Google OAuth requires an existing valid token or refresh token"
                )
            if not settings.credentials_file.exists():
                raise ConfigurationError(
                    f"OAuth Desktop credentials not found: {settings.credentials_file}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(settings.credentials_file), list(settings.google_scopes)
            )
            credentials = flow.run_local_server(port=0)
        settings.token_file.parent.mkdir(parents=True, exist_ok=True)
        settings.token_file.write_text(credentials.to_json(), encoding="utf-8")
        try:
            os.chmod(settings.token_file, 0o600)
        except OSError:
            pass
    if http_timeout is not None:
        # Opt-in browser polling bound; existing dispatcher/CODEX callers keep their path.
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp

        http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=http_timeout))
        return GoogleServices(
            sheets=build("sheets", "v4", http=http, cache_discovery=False),
            drive=build("drive", "v3", http=http, cache_discovery=False),
        )
    return GoogleServices(
        sheets=build("sheets", "v4", credentials=credentials, cache_discovery=False),
        drive=build("drive", "v3", credentials=credentials, cache_discovery=False),
    )
