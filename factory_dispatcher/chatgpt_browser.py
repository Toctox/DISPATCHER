"""Attach-only CDP adapter. Browser/profile creation belongs exclusively to the helper."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from .config import LocalSettings
from .errors import LaunchGuardError
from .mutex import LocalMutex, MutexBusyError
from .receipts import decode_json_object, read_json_object, write_json


class ChatGPTError(LaunchGuardError):
    """Only fixed structural codes may cross this boundary, never browser error bodies."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def cdp_endpoint(value: str) -> str:
    """MVP is IPv4 loopback only. Normalize localhost without DNS or proxy resolution."""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
        ):
            raise ValueError
        return f"http://127.0.0.1:{parsed.port}"
    except ValueError as exc:
        raise ChatGPTError("CHATGPT_CDP_ENDPOINT_UNSAFE") from exc


def is_chatgpt_page(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "chatgpt.com"
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ChatGPTError("CHATGPT_CDP_UNAVAILABLE")


def discover_browser(endpoint: str) -> str:
    """Read CDP's browser metadata, not ChatGPT endpoints, content or cookies."""
    endpoint = cdp_endpoint(endpoint)
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(endpoint + "/json/version", timeout=3) as response:
            content = response.read(65537)
        if len(content) > 65536:
            raise ValueError
        raw = decode_json_object(content)
        if not re.match(r"^(Chrome|Chromium|HeadlessChrome|Edg)/", str(raw.get("Browser", ""))):
            raise ValueError
        websocket = raw["webSocketDebuggerUrl"]
        parsed = urlsplit(websocket)
        if (
            parsed.scheme != "ws"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port != urlsplit(endpoint).port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"/devtools/browser/[A-Za-z0-9_.-]{1,160}", parsed.path)
        ):
            raise ValueError
        return f"ws://127.0.0.1:{parsed.port}{parsed.path}"
    except Exception as exc:
        raise ChatGPTError("CHATGPT_CDP_UNAVAILABLE") from exc


@dataclass(frozen=True)
class BrowserBinding:
    endpoint: str
    browser_id: str
    target_id: str

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw):
        try:
            if not isinstance(raw, dict) or set(raw) != {"endpoint", "browser_id", "target_id"}:
                raise ValueError
            endpoint = cdp_endpoint(raw["endpoint"])
            for field in ("browser_id", "target_id"):
                if not isinstance(raw[field], str) or not re.fullmatch(
                    r"[A-Za-z0-9_.-]{1,160}", raw[field]
                ):
                    raise ValueError
            return cls(endpoint, raw["browser_id"], raw["target_id"])
        except (TypeError, ValueError, KeyError) as exc:
            raise ChatGPTError("CHATGPT_CDP_BINDING_INVALID") from exc

    def require_same(self, expected):
        if (self.endpoint, self.browser_id) != (expected.endpoint, expected.browser_id):
            raise ChatGPTError("CHATGPT_BROWSER_CHANGED")
        if self.target_id != expected.target_id:
            raise ChatGPTError("CHATGPT_TAB_CHANGED")


def tab_state_path(settings: LocalSettings) -> Path:
    endpoint = cdp_endpoint(settings.chatgpt_cdp_endpoint)
    key = hashlib.sha256(endpoint.encode("ascii")).hexdigest()
    # Stable within this project even if configs change stateDirectory or profile directory.
    return settings.project_root / "state" / "chatgpt-cdp-locks" / f"{key}.json"


@contextmanager
def tab_lock(settings: LocalSettings):
    try:
        with LocalMutex(tab_state_path(settings).with_suffix(".lock")):
            yield
    except MutexBusyError as exc:
        raise ChatGPTError("CHATGPT_TAB_BUSY") from exc


def require_tab_available(settings: LocalSettings):
    path = tab_state_path(settings)
    if path.exists():
        try:
            if read_json_object(path).get("state") == "AVAILABLE":
                return
        except Exception:
            pass
        raise ChatGPTError("CHATGPT_TAB_RECONCILIATION_REQUIRED")


def reserve_tab(settings, binding, context):
    write_json(
        tab_state_path(settings),
        {
            "state": "SEND_MAY_HAVE_EFFECTS",
            "binding": binding.to_dict(),
            "dispatchId": context.dispatchId,
            "attemptId": context.attemptId,
        },
    )


def release_tab(settings, receipt_id):
    write_json(tab_state_path(settings), {"state": "AVAILABLE", "receiptDriveId": receipt_id})


def require_chatgpt_root(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "chatgpt.com"
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ChatGPTError("CHATGPT_UNSAFE_URL")


def resolve_browser(settings: LocalSettings) -> Path:
    configured = settings.chatgpt_browser_executable
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = settings.project_root / candidate
        if not candidate.is_file():
            located = shutil.which(configured)
            if not located:
                raise ChatGPTError("CHATGPT_BROWSER_NOT_FOUND")
            candidate = Path(located)
        return candidate.resolve()
    candidates = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if base := os.environ.get(variable):
            candidates.extend(
                [
                    Path(base) / "Microsoft/Edge/Application/msedge.exe",
                    Path(base) / "Google/Chrome/Application/chrome.exe",
                ]
            )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ChatGPTError("CHATGPT_BROWSER_NOT_FOUND")


def check_environment(settings: LocalSettings, *, setup: bool = False) -> str:
    if not setup and not settings.browser_chatgpt_launch_enabled:
        raise ChatGPTError("CHATGPT_BROWSER_DISABLED")
    if settings.chatgpt_browser_mode != "ATTACH_CDP":
        raise ChatGPTError("CHATGPT_BROWSER_MODE_DISABLED")
    endpoint = cdp_endpoint(settings.chatgpt_cdp_endpoint)
    if setup:
        return endpoint
    if importlib.util.find_spec("playwright") is None:
        raise ChatGPTError("CHATGPT_BROWSER_DEPENDENCY_MISSING")
    # Playwright debug output can include the input to fill(). Do not emit it.
    if os.environ.get("DEBUG") or os.environ.get("PWDEBUG"):
        raise ChatGPTError("CHATGPT_UNSAFE_DEBUG_ENV")
    try:
        if tuple(int(part) for part in version("playwright").split(".")[:2]) < (1, 60):
            raise ChatGPTError("CHATGPT_BROWSER_DEPENDENCY_OUTDATED")
    except (PackageNotFoundError, ValueError) as exc:
        raise ChatGPTError("CHATGPT_BROWSER_DEPENDENCY_MISSING") from exc
    return endpoint


@contextmanager
def profile_lock(settings: LocalSettings):
    profile = settings.chatgpt_profile
    if (
        not profile.is_relative_to(settings.state_directory.resolve())
        or profile == Path(profile.anchor)
        or settings.project_root.resolve().is_relative_to(profile)
        or settings.state_directory.resolve().is_relative_to(profile)
    ):
        raise ChatGPTError("CHATGPT_PROFILE_UNSAFE")
    if local := os.environ.get("LOCALAPPDATA"):
        for suffix in ("Google/Chrome/User Data", "Microsoft/Edge/User Data"):
            personal = (Path(local) / suffix).resolve()
            if profile.is_relative_to(personal) or personal.is_relative_to(profile):
                raise ChatGPTError("CHATGPT_PROFILE_UNSAFE")
    try:
        # Used only by the explicit starter, never by dispatch/preflight/worker.
        with LocalMutex(profile.with_name(profile.name + ".factory.lock")):
            profile.mkdir(parents=True, exist_ok=True)
            marker = profile / ".factory-profile.json"
            if not marker.exists():
                if any(profile.iterdir()):
                    raise ChatGPTError("CHATGPT_PROFILE_NOT_DEDICATED")
                write_json(marker, {"profileType": "FACTORY_CHATGPT", "version": 1}, exclusive=True)
            if read_json_object(marker) != {"profileType": "FACTORY_CHATGPT", "version": 1}:
                raise ChatGPTError("CHATGPT_PROFILE_NOT_DEDICATED")
            # Actual write/access check, not os.access (which misses Windows ACL restrictions).
            with LocalMutex(profile / ".access-check.lock"):
                pass
            yield profile
    except MutexBusyError as exc:
        raise ChatGPTError("CHATGPT_PROFILE_BUSY") from exc
    except OSError as exc:
        raise ChatGPTError("CHATGPT_PROFILE_UNAVAILABLE") from exc


def _discover_tab(browser, endpoint, browser_id):
    pages = [
        (context, page)
        for context in browser.contexts
        for page in context.pages
        if not page.is_closed() and is_chatgpt_page(page.url)
    ]
    if not pages:
        raise ChatGPTError("CHATGPT_TAB_NOT_FOUND")
    if len(pages) != 1:
        raise ChatGPTError("CHATGPT_TAB_AMBIGUOUS")
    context, page = pages[0]
    session = context.new_cdp_session(page)
    try:
        info = session.send("Target.getTargetInfo")["targetInfo"]
        if info.get("type") != "page" or not is_chatgpt_page(info.get("url", "")):
            raise ChatGPTError("CHATGPT_TAB_CHANGED")
        binding = BrowserBinding.from_mapping(
            {
                "endpoint": endpoint,
                "browser_id": browser_id,
                "target_id": info["targetId"],
            }
        )
        return page, binding
    finally:
        session.detach()


@contextmanager
def browser_session(settings: LocalSettings, endpoint: str):
    # Lazy import: base/CODEX installs and unit tests do not require Playwright.
    from playwright.sync_api import sync_playwright

    from .chatgpt_selectors import ChatGPTControls

    endpoint = cdp_endpoint(endpoint)
    websocket = discover_browser(endpoint)
    browser_id = urlsplit(websocket).path.rsplit("/", 1)[-1]
    # Stopping Playwright detaches. Never call Browser/Context/Page.close.
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(
                websocket, timeout=10000, no_defaults=True
            )
        except Exception as exc:
            raise ChatGPTError("CHATGPT_CDP_UNAVAILABLE") from exc
        page, binding = _discover_tab(browser, endpoint, browser_id)
        page.set_default_timeout(10000)

        def probe():
            selected, current = _discover_tab(browser, endpoint, browser_id)
            if selected is not page:
                raise ChatGPTError("CHATGPT_TAB_CHANGED")
            return current

        yield ChatGPTControls(page, binding=binding, binding_probe=probe)
