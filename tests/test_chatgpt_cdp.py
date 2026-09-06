from __future__ import annotations

import ast
import json
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from conftest import NOW, FakeGateway, queue_row
from test_chatgpt_browser import (
    FakeClock,
    Page,
)
from test_chatgpt_browser import (
    run_case as shared_run_case,
)

from factory_dispatcher.audit import MemoryAuditLogger
from factory_dispatcher.chatgpt_browser import (
    BrowserBinding,
    ChatGPTError,
    browser_session,
    cdp_endpoint,
    check_environment,
    discover_browser,
    is_chatgpt_page,
    profile_lock,
    require_tab_available,
    tab_lock,
    tab_state_path,
)
from factory_dispatcher.chatgpt_browser_start import start_browser
from factory_dispatcher.chatgpt_selectors import SELECTORS, ChatGPTControls
from factory_dispatcher.engine import Dispatcher
from factory_dispatcher.launchers import BrowserChatGPTLauncher
from factory_dispatcher.models import ExecutorType
from factory_dispatcher.receipts import read_json_object

ENDPOINT = "http://127.0.0.1:9222"
WEBSOCKET = "ws://127.0.0.1:9222/devtools/browser/browser-test"
run_case = shared_run_case


@pytest.fixture
def browser_settings(local_settings):
    executable = local_settings.project_root / "fake-chrome.exe"
    executable.write_bytes(b"FAKE - NEVER EXECUTE")
    return replace(
        local_settings,
        browser_chatgpt_launch_enabled=True,
        chatgpt_browser_mode="ATTACH_CDP",
        chatgpt_cdp_endpoint=ENDPOINT,
        chatgpt_browser_executable=str(executable),
        chatgpt_browser_timeout_seconds=30,
        chatgpt_browser_poll_seconds=5,
    )


class CDPPage(Page):
    def __init__(self, url="https://chatgpt.com/c/existing", target_id="tab-test"):
        super().__init__()
        self.url = url
        self.target_id = target_id
        self.closed = False

    def is_closed(self):
        return self.closed

    def set_default_timeout(self, timeout):
        pass

    def close(self):
        raise AssertionError("Do not close the existing tab")

    def goto(self, *args, **kwargs):
        raise AssertionError("Do not navigate the existing tab")

    def wait_for_timeout(self, timeout):
        raise AssertionError("Tests must never wait for real UI")


@pytest.fixture
def cdp_harness(browser_settings, monkeypatch):
    events = []
    options = []
    page = CDPPage()

    class Context:
        pages = [page]

        def new_cdp_session(self, selected):
            def send(method):
                assert method == "Target.getTargetInfo"
                return {
                    "targetInfo": {
                        "type": "page",
                        "targetId": selected.target_id,
                        "url": selected.url,
                    }
                }

            return SimpleNamespace(send=send, detach=lambda: events.append("metadata_detach"))

        def close(self):
            raise AssertionError("Do not close the existing context")

        def new_page(self):
            raise AssertionError("Do not create a tab")

    context = Context()
    browser = SimpleNamespace(contexts=[context])

    def forbidden(*args, **kwargs):
        raise AssertionError("Dispatch must not launch or close a browser")

    browser.close = forbidden

    def connect(endpoint, **kwargs):
        events.append("connect")
        options.append((endpoint, kwargs))
        return browser

    @contextmanager
    def playwright():
        try:
            yield SimpleNamespace(
                chromium=SimpleNamespace(
                    connect_over_cdp=connect,
                    launch=forbidden,
                    launch_persistent_context=forbidden,
                )
            )
        finally:
            events.append("disconnect")

    module = ModuleType("playwright.sync_api")
    module.sync_playwright = playwright
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    monkeypatch.setitem(sys.modules, "playwright", ModuleType("playwright"))
    monkeypatch.setattr("factory_dispatcher.chatgpt_browser.discover_browser", lambda _: WEBSOCKET)

    def session(settings, endpoint):
        # Only timing is overridden; exercise the actual CDP adapter and real selector logic.
        @contextmanager
        def wrapped():
            with browser_session(settings, endpoint) as controls:
                controls.timeout_seconds = 0
                yield controls

        return wrapped()

    return SimpleNamespace(
        events=events, options=options, context=context, browser=browser, page=page, session=session
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://0.0.0.0:9222",
        "http://192.168.1.1:9222",
        "https://127.0.0.1:9222",
        "ws://127.0.0.1:9222",
        "http://user@127.0.0.1:9222",
        "http://127.0.0.1:9222/json/version",
        "http://127.0.0.1:9222/?x=1",
        "http://127.0.0.1:9222/#fragment",
        "http://localhost",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:bad",
        "http://localhost.evil:9222",
        "http://[::]:9222",
        "http://127.0.0.2:9222",
    ],
)
def test_cdp_endpoint_is_explicit_ipv4_loopback_only(endpoint):
    with pytest.raises(ChatGPTError, match="CDP_ENDPOINT_UNSAFE"):
        cdp_endpoint(endpoint)


def test_localhost_normalizes_without_dns():
    assert cdp_endpoint("http://localhost:9222/") == ENDPOINT


@pytest.mark.parametrize(
    "url,eligible",
    [
        ("https://chatgpt.com/", True),
        ("https://chatgpt.com/c/existing", True),
        ("https://chatgpt.com:443/?model=example", True),
        ("https://chatgpt.com/g/example", True),
        ("http://chatgpt.com/", False),
        ("https://chatgpt.com.evil/", False),
        ("https://www.chatgpt.com/", False),
        ("https://chatgpt.com:8443/", False),
        ("https://chatgpt.com@evil/", False),
        ("https://person@chatgpt.com/", False),
        ("https://evil/?url=https://chatgpt.com/", False),
        ("about:blank", False),
    ],
)
def test_exact_origin_tab_eligibility(url, eligible):
    assert is_chatgpt_page(url) is eligible


def test_connect_over_cdp_uses_one_existing_tab_without_launch_or_navigation(
    browser_settings, cdp_harness
):
    with cdp_harness.session(browser_settings, ENDPOINT) as ui:
        assert ui.page is cdp_harness.page
        ui.preflight()
        assert ui.page.new_chat.clicked == 0
        assert ui.page.composer.text == ""
        assert ui.binding == BrowserBinding(ENDPOINT, "browser-test", "tab-test")
    assert cdp_harness.options == [(WEBSOCKET, {"timeout": 10000, "no_defaults": True})]
    assert cdp_harness.events[-1] == "disconnect"
    assert cdp_harness.context.pages == [cdp_harness.page]
    assert not cdp_harness.page.closed


@pytest.mark.parametrize("count,code", [(0, "CHATGPT_TAB_NOT_FOUND"), (2, "CHATGPT_TAB_AMBIGUOUS")])
def test_zero_or_multiple_tabs_block_without_fallback(browser_settings, cdp_harness, count, code):
    cdp_harness.context.pages = [CDPPage(target_id=f"tab-{i}") for i in range(count)]
    with pytest.raises(ChatGPTError, match=code), cdp_harness.session(browser_settings, ENDPOINT):
        pass
    assert len(cdp_harness.context.pages) == count
    assert cdp_harness.events[-1] == "disconnect"


def test_ambiguity_spans_all_browser_contexts(browser_settings, cdp_harness):
    cdp_harness.browser.contexts.append(SimpleNamespace(pages=[CDPPage(target_id="other")]))
    with (
        pytest.raises(ChatGPTError, match="TAB_AMBIGUOUS"),
        cdp_harness.session(browser_settings, ENDPOINT),
    ):
        pass


def test_unrelated_tabs_are_never_closed(browser_settings, cdp_harness):
    unrelated = CDPPage("https://example.test/")
    cdp_harness.context.pages.append(unrelated)
    with cdp_harness.session(browser_settings, ENDPOINT) as ui:
        assert ui.page is cdp_harness.page
    assert not unrelated.closed


@pytest.mark.parametrize(
    "issue,code",
    [
        ("no_tab", "CHATGPT_TAB_NOT_FOUND"),
        ("multiple", "CHATGPT_TAB_AMBIGUOUS"),
        ("auth", "CHATGPT_AUTH_REQUIRED"),
        ("new_chat", "CHATGPT_SELECTOR_UNAVAILABLE"),
        ("composer", "CHATGPT_SELECTOR_UNAVAILABLE"),
        ("route_error", "CHATGPT_SELECTOR_UNAVAILABLE"),
        ("challenge", "CHATGPT_AUTH_UNVERIFIED"),
        ("cdp", "CHATGPT_CDP_UNAVAILABLE"),
    ],
)
def test_real_preflight_contract_blocks_before_claim(
    browser_settings, chatgpt_request, cdp_harness, monkeypatch, issue, code
):
    page = cdp_harness.page
    if issue == "no_tab":
        cdp_harness.context.pages = []
    elif issue == "multiple":
        cdp_harness.context.pages.append(CDPPage(target_id="second"))
    elif issue == "auth":
        page.elements[SELECTORS["login"][0]] = page.elements[SELECTORS["account"][0]]
    elif issue in {"new_chat", "composer"}:
        del page.elements[SELECTORS[issue][0]]
    elif issue == "route_error":
        del page.elements[SELECTORS["composer"][0]]  # Even if account chrome is still present.
    elif issue == "challenge":
        page.elements.clear()
    elif issue == "cdp":

        def unavailable(_):
            raise ChatGPTError("CHATGPT_CDP_UNAVAILABLE")

        monkeypatch.setattr("factory_dispatcher.chatgpt_browser.discover_browser", unavailable)
    gateway = FakeGateway([queue_row()], {"request-1": chatgpt_request})
    launcher = BrowserChatGPTLauncher(
        browser_settings,
        config_file=Path("config.json"),
        environment_check=lambda _: ENDPOINT,
        session_factory=cdp_harness.session,
    )
    result = Dispatcher(
        gateway,
        browser_settings,
        MemoryAuditLogger(),
        {ExecutorType.CHATGPT: launcher},
        clock=lambda: NOW,
    ).tick()
    assert result["outcome"] == "PREFLIGHT_BLOCKED"
    assert result["errorCode"] == code
    assert gateway.read_job(2).attempt == 0
    assert gateway.updates == gateway.events == gateway.uploads == []
    assert page.new_chat.clicked == page.send.clicked == 0


def test_worker_real_adapter_new_conversation_exact_input_and_leave_browser_alive(
    run_case, cdp_harness
):
    cdp_harness.page.send.on_click = run_case.publish
    assert run_case.run(session_factory=cdp_harness.session) == 0
    page = cdp_harness.page
    assert page.new_chat.clicked == 1
    assert page.send.clicked == 1
    assert page.composer.text.encode("utf-8") == run_case.prompt.encode("utf-8")
    assert not page.closed
    assert cdp_harness.context.pages == [page]
    assert cdp_harness.events[-1] == "disconnect"
    assert run_case.status()["receiptDriveId"] == "receipt-valid"


@pytest.mark.parametrize(
    "field,code", [("browser_id", "CHATGPT_BROWSER_CHANGED"), ("target_id", "CHATGPT_TAB_CHANGED")]
)
def test_worker_pins_preflight_browser_and_tab(run_case, field, code):
    run_case.ui.binding = replace(run_case.ui.binding, **{field: "replacement"})
    assert run_case.run() == 1
    assert run_case.status()["errorCode"] == code
    assert "new_chat" not in run_case.ui.events
    assert "send" not in run_case.ui.events


def test_target_changed_while_preparing_never_sends(run_case):
    def change():
        run_case.ui.binding = replace(run_case.ui.binding, target_id="replacement")

    run_case.ui.on_prepare = change
    assert run_case.run() == 1
    assert run_case.status()["errorCode"] == "CHATGPT_TAB_CHANGED"
    assert "send" not in run_case.ui.events


def test_tab_ambiguity_appearing_before_send_is_rejected(run_case, cdp_harness):
    original = cdp_harness.page.composer.fill

    def fill(prompt):
        original(prompt)
        cdp_harness.context.pages.append(CDPPage(target_id="second"))

    cdp_harness.page.composer.fill = fill
    assert run_case.run(session_factory=cdp_harness.session) == 1
    assert run_case.status()["errorCode"] == "CHATGPT_TAB_AMBIGUOUS"
    assert cdp_harness.page.send.clicked == 0


def test_timeout_keeps_tab_but_prevents_next_dispatch_reusing_it(run_case):
    assert run_case.run() == 1
    assert run_case.ui.events[-1] == "detach"
    assert read_json_object(tab_state_path(run_case.settings))["state"] == "SEND_MAY_HAVE_EFFECTS"
    with pytest.raises(ChatGPTError, match="TAB_RECONCILIATION_REQUIRED"):
        run_case.launcher.preflight(run_case.request)


def test_tab_lock_covers_aliases_and_different_state_paths(browser_settings):
    alias = replace(
        browser_settings,
        chatgpt_cdp_endpoint="http://localhost:9222/",
        state_directory=browser_settings.project_root / "different-state",
    )
    with tab_lock(browser_settings):
        with pytest.raises(ChatGPTError, match="TAB_BUSY"), tab_lock(alias):
            pass


def test_corrupt_reservation_is_not_treated_as_idle(browser_settings):
    with tab_lock(browser_settings):
        tab_state_path(browser_settings).write_text("broken json")
        with pytest.raises(ChatGPTError, match="TAB_RECONCILIATION_REQUIRED"):
            require_tab_available(browser_settings)


def test_legacy_modes_do_not_compete_with_attach(browser_settings):
    for mode in ("DISABLED", "PERSISTENT_CONTEXT", "", "attach_cdp"):
        with pytest.raises(ChatGPTError, match="MODE_DISABLED"):
            check_environment(replace(browser_settings, chatgpt_browser_mode=mode))


@pytest.mark.parametrize(
    "websocket",
    [
        "ws://evil.test:9222/devtools/browser/id",
        "ws://127.0.0.1:9999/devtools/browser/id",
        "ws://user@127.0.0.1:9222/devtools/browser/id",
        "ws://127.0.0.1:9222/devtools/page/id",
        "wss://127.0.0.1:9222/devtools/browser/id",
    ],
)
def test_discovery_rejects_nonlocal_or_wrong_browser_websocket(monkeypatch, websocket):
    @contextmanager
    def opened(*args, **kwargs):
        yield SimpleNamespace(
            read=lambda _: json.dumps(
                {
                    "Browser": "Chrome/152.0",
                    "webSocketDebuggerUrl": websocket,
                }
            ).encode()
        )

    monkeypatch.setattr(
        "factory_dispatcher.chatgpt_browser.build_opener",
        lambda *args: SimpleNamespace(open=opened),
    )
    with pytest.raises(ChatGPTError, match="CDP_UNAVAILABLE"):
        discover_browser(ENDPOINT)


def test_discovery_has_no_proxy_and_no_redirect_and_bounded_read(monkeypatch):
    seen = []

    @contextmanager
    def opened(url, *, timeout):
        assert url == ENDPOINT + "/json/version"
        assert timeout == 3
        yield SimpleNamespace(
            read=lambda size: json.dumps(
                {
                    "Browser": "Chrome/152.0",
                    "webSocketDebuggerUrl": WEBSOCKET,
                }
            ).encode()
        )

    def opener(*handlers):
        seen.extend(handlers)
        return SimpleNamespace(open=opened)

    monkeypatch.setattr("factory_dispatcher.chatgpt_browser.build_opener", opener)
    assert discover_browser(ENDPOINT) == WEBSOCKET
    assert seen[0].proxies == {}
    with pytest.raises(ChatGPTError):
        seen[1].redirect_request(None, None, 302, "", {}, "http://evil.test/")


def test_starter_reuses_valid_endpoint_without_creating_profile_or_process(browser_settings):
    def forbidden(*args, **kwargs):
        raise AssertionError("No second browser")

    result = start_browser(
        browser_settings, discover=lambda _: WEBSOCKET, popen_factory=forbidden, occupied=forbidden
    )
    assert result["outcome"] == "BROWSER_ALREADY_RUNNING"
    assert not browser_settings.chatgpt_profile.exists()


def unavailable(_):
    raise ChatGPTError("CHATGPT_CDP_UNAVAILABLE")


def test_starter_refuses_port_occupied_by_unknown_service(browser_settings):
    with pytest.raises(ChatGPTError, match="CDP_PORT_OCCUPIED"):
        start_browser(
            browser_settings,
            discover=unavailable,
            occupied=lambda _: True,
            popen_factory=lambda *args, **kwargs: pytest.fail("must not launch"),
        )


def test_starter_uses_loopback_and_dedicated_profile_and_returns_without_closing(browser_settings):
    launched = []

    def popen(arguments, **kwargs):
        launched.append((arguments, kwargs))
        return SimpleNamespace(pid=789, poll=lambda: None)

    def discover(endpoint):
        if not launched:
            raise ChatGPTError("CHATGPT_CDP_UNAVAILABLE")
        return WEBSOCKET

    result = start_browser(
        replace(browser_settings, browser_chatgpt_launch_enabled=False),
        discover=discover,
        occupied=lambda _: False,
        popen_factory=popen,
    )
    assert result["outcome"] == "BROWSER_STARTED"
    args, kwargs = launched[0]
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--remote-debugging-port=9222" in args
    assert f"--user-data-dir={browser_settings.state_directory / 'factory-browser'}" in args
    assert args[-1] == "https://chatgpt.com/"
    assert "--remote-allow-origins=*" not in args
    assert not kwargs["shell"]


def test_starter_timeout_never_blindly_relaunches_or_kills(browser_settings):
    calls = []
    clock = FakeClock()

    def popen(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(pid=456, poll=lambda: None)

    with pytest.raises(ChatGPTError, match="START_UNCERTAIN"):
        start_browser(
            browser_settings,
            discover=unavailable,
            occupied=lambda _: False,
            popen_factory=popen,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert len(calls) == 1
    with pytest.raises(ChatGPTError, match="START_UNCERTAIN"):
        start_browser(
            browser_settings,
            discover=unavailable,
            occupied=lambda _: False,
            popen_factory=popen,
            alive=lambda _: True,
        )
    assert len(calls) == 1


def test_starter_profile_cannot_be_outside_state(browser_settings):
    settings = replace(
        browser_settings,
        chatgpt_browser_profile_directory=browser_settings.project_root / "outside-state",
    )
    with pytest.raises(ChatGPTError, match="PROFILE_UNSAFE"), profile_lock(settings):
        pass


def test_starter_missing_executable_fails_without_launch(browser_settings):
    settings = replace(browser_settings, chatgpt_browser_executable="nonexistent-browser.exe")
    with pytest.raises(ChatGPTError, match="BROWSER_NOT_FOUND"):
        start_browser(
            settings,
            discover=unavailable,
            occupied=lambda _: False,
            popen_factory=lambda *args, **kwargs: pytest.fail("must not launch"),
        )


def test_attach_source_cannot_create_close_refresh_or_read_answers():
    root = Path(__file__).resolve().parents[1] / "factory_dispatcher"
    forbidden = {
        "new_page",
        "new_context",
        "launch",
        "launch_persistent_context",
        "goto",
        "reload",
        "close",
        "evaluate",
        "content",
        "text_content",
        "screenshot",
    }
    for name in ("chatgpt_worker.py", "chatgpt_selectors.py", "chatgpt_browser.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden
                if node.func.attr in {"inner_text", "input_value"}:
                    assert ast.unparse(node.func.value) == "self._composer"


def test_new_chat_preflight_does_not_click_retry_or_read_error_text():
    page = CDPPage()
    del page.elements[SELECTORS["new_chat"][0]]
    with pytest.raises(ChatGPTError, match="SELECTOR_UNAVAILABLE"):
        ChatGPTControls(page, timeout_seconds=0).preflight()
    assert page.new_chat.clicked == page.send.clicked == 0
