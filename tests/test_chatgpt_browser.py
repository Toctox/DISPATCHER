from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import NOW, FakeGateway, FakeLauncher, queue_row

from factory_dispatcher.audit import MemoryAuditLogger
from factory_dispatcher.bootstrap import build_bootstrap
from factory_dispatcher.chatgpt_browser import (
    BrowserBinding,
    ChatGPTError,
    check_environment,
    profile_lock,
    require_chatgpt_root,
    resolve_browser,
    tab_lock,
    tab_state_path,
)
from factory_dispatcher.chatgpt_profile_setup import main as legacy_profile_main
from factory_dispatcher.chatgpt_selectors import SELECTORS, ChatGPTControls
from factory_dispatcher.chatgpt_worker import run_worker
from factory_dispatcher.cli import build_launchers
from factory_dispatcher.config import load_local_settings
from factory_dispatcher.engine import Dispatcher
from factory_dispatcher.launchers import (
    BrowserChatGPTLauncher,
    CodexLauncher,
    ManualChatGPTLauncher,
)
from factory_dispatcher.models import (
    DispatchRequest,
    DispatchState,
    ExecutorType,
    Receipt,
    isoformat,
)
from factory_dispatcher.mutex import LocalMutex
from factory_dispatcher.receipts import read_json_object, write_json


class FakeClock:
    def __init__(self):
        self.seconds = 0

    def now(self):
        return NOW + timedelta(seconds=self.seconds)

    def monotonic(self):
        return self.seconds

    def sleep(self, seconds):
        self.seconds += seconds


class FakeUI:
    def __init__(self):
        self.events = []
        self.auth_error = None
        self.prepare_error = None
        self.send_error = None
        self.on_prepare = lambda: None
        self.on_send = lambda: None
        self.prompt = None
        self.binding = BrowserBinding("http://127.0.0.1:9222", "browser-test", "tab-test")

    def verify_binding(self, expected):
        self.binding.require_same(expected)

    def preflight(self):
        self.require_auth()
        if self.prepare_error:
            raise self.prepare_error

    @contextmanager
    def session(self, settings, executable):
        self.events.append("attach")
        try:
            yield self
        finally:
            self.events.append("detach")

    def require_auth(self):
        self.events.append("auth")
        if self.auth_error:
            raise self.auth_error

    def prepare(self, prompt):
        self.events.append("new_chat")
        if self.prepare_error:
            raise self.prepare_error
        self.prompt = prompt
        self.events.append("fill")
        self.on_prepare()

    def send(self, *, timeout_ms=10000):
        assert 0 < timeout_ms <= 10000
        self.events.append("send")
        self.on_send()
        if self.send_error:
            raise self.send_error

    def wait_for_human_close(self):
        self.events.append("human_close")


@pytest.fixture
def browser_settings(local_settings):
    executable = local_settings.project_root / "fake-browser.exe"
    executable.write_bytes(b"TEST DOUBLE - NEVER EXECUTE")
    return replace(
        local_settings,
        browser_chatgpt_launch_enabled=True,
        chatgpt_browser_mode="ATTACH_CDP",
        chatgpt_cdp_endpoint="http://127.0.0.1:9222",
        chatgpt_browser_executable=str(executable),
        chatgpt_browser_timeout_seconds=30,
        chatgpt_browser_poll_seconds=5,
    )


@pytest.fixture
def run_case(browser_settings, chatgpt_request):
    gateway = FakeGateway(
        [
            queue_row(
                status="CLAIMED",
                attempt=1,
                leaseToken="browser-lease-secret",
                leaseOwner="TEST-WORKER",
                leaseExpiresAt=isoformat(NOW + timedelta(minutes=45)),
                stagingFolderId="stage-test",
                retryPolicy="RECONCILE_BEFORE_RETRY",
            )
        ],
        {"request-1": chatgpt_request},
    )
    job = gateway.read_job(2)
    request = DispatchRequest(chatgpt_request)
    prompt = build_bootstrap(job, gateway.factory, job.staging_folder_id, request)
    processes = []

    def popen(*args, **kwargs):
        processes.append((args, kwargs))
        return SimpleNamespace(pid=321)

    def environment(settings):
        return settings.chatgpt_cdp_endpoint

    ui = FakeUI()
    launcher = BrowserChatGPTLauncher(
        browser_settings,
        config_file=browser_settings.project_root / "config.json",
        environment_check=environment,
        session_factory=ui.session,
        popen_factory=popen,
    )
    launcher.preflight(request)
    ui.events.clear()
    launcher.launch(job, request, prompt, receipt_folder_id="receipts-root")
    directory = browser_settings.state_directory / "chatgpt-runs" / job.dispatch_id / job.attempt_id
    case = SimpleNamespace(
        settings=browser_settings,
        gateway=gateway,
        job=job,
        request=request,
        prompt=prompt,
        ui=ui,
        launcher=launcher,
        processes=processes,
        directory=directory,
        manifest=directory / "launch.json",
        clock=FakeClock(),
        environment=environment,
    )
    case.receipt = {
        "schemaVersion": 1,
        "artifactType": "EXECUTION_RECEIPT",
        "dispatchId": job.dispatch_id,
        "attemptId": job.attempt_id,
        "leaseToken": job.lease_token,
        "agentId": job.agent_id,
        "requestDriveId": job.request_drive_id,
        "status": "SUCCEEDED",
        "executorStatement": "Mechanical test fixture",
        "producedArtifacts": [],
        "error": {},
        "startedAt": isoformat(NOW),
        "finishedAt": isoformat(NOW),
    }

    def publish(**overrides):
        name = f"EXECUTION_RECEIPT__{job.dispatch_id}__{job.attempt_id}.json"
        gateway.receipts[name] = Receipt("receipt-valid", {**case.receipt, **overrides})

    case.publish = publish

    def run(**overrides):
        options = dict(
            settings=browser_settings,
            gateway=gateway,
            session_factory=ui.session,
            environment_check=environment,
            clock=case.clock.now,
            monotonic=case.clock.monotonic,
            sleep=case.clock.sleep,
        )
        return run_worker(case.manifest, **{**options, **overrides})

    case.run = run
    case.status = lambda: read_json_object(directory / "status.json")
    return case


def test_routing_preserves_codex_and_browser_takes_priority(browser_settings):
    launchers = build_launchers(browser_settings, browser_settings.project_root / "config.json")
    assert isinstance(launchers[ExecutorType.CHATGPT], BrowserChatGPTLauncher)
    assert isinstance(launchers[ExecutorType.CODEX], CodexLauncher)
    migrated = build_launchers(
        replace(browser_settings, browser_chatgpt_launch_enabled=False),
        browser_settings.project_root / "config.json",
    )
    assert isinstance(migrated[ExecutorType.CHATGPT], ManualChatGPTLauncher)
    assert migrated[ExecutorType.CHATGPT].enabled


def test_configuration_browser_is_opt_in_and_profile_tracks_state(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"spreadsheetId": "sheet", "stateDirectory": "custom-state"}))
    settings = load_local_settings(config)
    assert not settings.browser_chatgpt_launch_enabled
    assert settings.chatgpt_profile == tmp_path / "custom-state" / "factory-browser"
    assert settings.chatgpt_browser_mode == "DISABLED"
    assert settings.chatgpt_cdp_endpoint == ""
    config.write_text(
        json.dumps(
            {
                "spreadsheetId": "sheet",
                "browserChatGptLaunchEnabled": True,
                "chatGptBrowserProfileDirectory": "profiles/factory",
            }
        )
    )
    settings = load_local_settings(config)
    assert settings.browser_chatgpt_launch_enabled
    assert settings.chatgpt_profile == tmp_path / "profiles" / "factory"


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "30", None, 14401])
def test_timeout_config_rejects_invalid_types_and_range(tmp_path, value):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"spreadsheetId": "sheet", "chatGptBrowserTimeoutSeconds": value}))
    with pytest.raises(Exception, match="chatGptBrowserTimeoutSeconds"):
        load_local_settings(config)


@pytest.mark.parametrize(
    "url",
    [
        "http://chatgpt.com/",
        "https://evil.test/",
        "https://chatgpt.com/c/old",
        "https://chatgpt.com/?q=hello",
        "https://chatgpt.com/#old",
        "https://chatgpt.com@evil.test/",
    ],
)
def test_unsafe_browser_url_fails_closed(url):
    with pytest.raises(ChatGPTError, match="UNSAFE_URL"):
        require_chatgpt_root(url)


def test_existing_browser_resolution_does_not_execute(browser_settings):
    assert resolve_browser(browser_settings) == Path(browser_settings.chatgpt_browser_executable)


@pytest.mark.parametrize(
    "code",
    [
        "CHATGPT_BROWSER_DISABLED",
        "CHATGPT_BROWSER_NOT_FOUND",
        "CHATGPT_BROWSER_DEPENDENCY_MISSING",
        "CHATGPT_AUTH_REQUIRED",
        "CHATGPT_AUTH_UNVERIFIED",
        "CHATGPT_PROFILE_BUSY",
    ],
)
def test_preflight_error_never_claims(browser_settings, chatgpt_request, code):
    gateway = FakeGateway([queue_row()], {"request-1": chatgpt_request})
    logger = MemoryAuditLogger()
    launcher = BrowserChatGPTLauncher(browser_settings, config_file=Path("config.json"))

    def preflight(request):
        raise ChatGPTError(code)

    launcher.preflight = preflight
    result = Dispatcher(
        gateway, browser_settings, logger, {ExecutorType.CHATGPT: launcher}, clock=lambda: NOW
    ).tick()
    assert result["outcome"] == "PREFLIGHT_BLOCKED"
    assert result["errorCode"] == code
    assert gateway.read_job(2).attempt == 0
    assert gateway.updates == gateway.events == gateway.uploads == []
    assert any(record["event"] == "chatgpt_preflight_blocked" for record in logger.records)


def test_unsafe_cdp_endpoint_preflight_no_claim(browser_settings, chatgpt_request):
    settings = replace(browser_settings, chatgpt_cdp_endpoint="http://192.168.1.10:9222")
    gateway = FakeGateway([queue_row()], {"request-1": chatgpt_request})
    launcher = BrowserChatGPTLauncher(settings, config_file=Path("config.json"))
    result = Dispatcher(
        gateway, settings, MemoryAuditLogger(), {ExecutorType.CHATGPT: launcher}, clock=lambda: NOW
    ).tick()
    assert result["errorCode"] == "CHATGPT_CDP_ENDPOINT_UNSAFE"
    assert gateway.updates == []


def test_disabled_and_dependency_preflight(browser_settings, monkeypatch):
    with pytest.raises(ChatGPTError, match="BROWSER_DISABLED"):
        check_environment(replace(browser_settings, browser_chatgpt_launch_enabled=False))
    monkeypatch.setattr(
        "factory_dispatcher.chatgpt_browser.importlib.util.find_spec", lambda _: None
    )
    with pytest.raises(ChatGPTError, match="DEPENDENCY_MISSING"):
        check_environment(browser_settings)


def test_preflight_only_checks_ui_then_detaches(run_case):
    run_case.launcher.preflight(run_case.request)
    assert run_case.ui.events == ["attach", "auth", "detach"]


def test_auth_missing_preflight_is_environment_error(run_case):
    run_case.ui.auth_error = ChatGPTError("CHATGPT_AUTH_REQUIRED")
    with pytest.raises(ChatGPTError, match="AUTH_REQUIRED"):
        run_case.launcher.preflight(run_case.request)
    assert run_case.ui.events[-1] == "detach"
    assert "send" not in run_case.ui.events


def test_profile_lock_blocks_concurrent_users_and_releases(browser_settings):
    with profile_lock(browser_settings):
        with pytest.raises(ChatGPTError, match="PROFILE_BUSY"), profile_lock(browser_settings):
            pass
    with profile_lock(browser_settings):
        pass


def test_profile_rejects_unknown_existing_data(browser_settings):
    profile = browser_settings.chatgpt_profile
    profile.mkdir(parents=True)
    sentinel = profile / "user-data.txt"
    sentinel.write_text("must preserve")
    with pytest.raises(ChatGPTError, match="NOT_DEDICATED"), profile_lock(browser_settings):
        pass
    assert sentinel.read_text() == "must preserve"


@pytest.mark.parametrize("location", ["project", "state", "personal"])
def test_profile_cannot_be_broad_or_personal(browser_settings, monkeypatch, location):
    monkeypatch.setenv("LOCALAPPDATA", str(browser_settings.project_root / "AppData"))
    profiles = {
        "project": browser_settings.project_root,
        "state": browser_settings.state_directory,
        "personal": browser_settings.project_root / "AppData/Google/Chrome/User Data/Default",
    }
    with (
        pytest.raises(ChatGPTError, match="PROFILE_UNSAFE"),
        profile_lock(
            replace(browser_settings, chatgpt_browser_profile_directory=profiles[location])
        ),
    ):
        pass


def test_legacy_setup_is_disabled_without_opening_browser(capsys):
    assert legacy_profile_main([]) == 2
    assert "CHATGPT_LEGACY_PROFILE_DISABLED" in capsys.readouterr().out


def test_launcher_is_async_and_does_not_put_secrets_in_argv(run_case):
    ((args, kwargs),) = run_case.processes
    assert args[0][1:3] == ["-m", "factory_dispatcher.chatgpt_worker"]
    assert run_case.job.lease_token not in str(args)
    assert not kwargs["shell"]
    assert run_case.ui.events == []
    assert (run_case.directory / "bootstrap.txt").read_bytes() == run_case.prompt.encode("utf-8")


@pytest.mark.parametrize(
    "receipt_status", ["SUCCEEDED", "BLOCKED", "WAITING_HUMAN", "FAILED_FINAL"]
)
def test_receipt_is_completion_and_detaches_only_after_detection(run_case, receipt_status):
    case = run_case
    case.ui.on_send = lambda: case.publish(status=receipt_status)
    original_find = case.gateway.find_receipt

    def find(*args):
        assert "detach" not in case.ui.events
        case.ui.events.append("receipt_read")
        return original_find(*args)

    case.gateway.find_receipt = find
    assert case.run() == 0
    assert case.ui.events == [
        "attach",
        "auth",
        "new_chat",
        "fill",
        "send",
        "receipt_read",
        "detach",
    ]
    assert case.ui.prompt.encode("utf-8") == case.prompt.encode("utf-8")
    assert case.status()["receiptDriveId"] == "receipt-valid"
    assert case.status()["receiptStatus"] == receipt_status
    assert case.status()["operationalSuccess"] is True
    assert case.gateway.updates == []  # Browser worker NEVER promotes QUEUE.
    log = (case.directory / "browser.log").read_text()
    assert case.job.lease_token not in log
    assert case.prompt not in log
    assert "FINISHED" in log


@pytest.mark.parametrize(
    "field,value",
    [
        ("attemptId", "D-TEST-0001-A999"),
        ("dispatchId", "D-OTHER"),
        ("leaseToken", "wrong"),
        ("agentId", "OTHER"),
        ("requestDriveId", "wrong"),
        ("artifactType", "OTHER"),
        ("status", "UNKNOWN"),
        ("schemaVersion", 99),
    ],
)
def test_other_or_invalid_receipt_never_completes(run_case, field, value):
    run_case.ui.on_send = lambda: run_case.publish(**{field: value})
    assert run_case.run() == 1
    assert run_case.status()["errorCode"] == "CHATGPT_RECEIPT_TIMEOUT"
    assert "receiptDriveId" not in run_case.status()
    assert run_case.status()["needsReconciliation"]
    assert "CHATGPT_RECEIPT_REJECTED" in (run_case.directory / "browser.log").read_text()


def test_receipt_on_later_poll_keeps_browser_open(run_case):
    sleeps = []

    def pause(seconds):
        assert "detach" not in run_case.ui.events
        sleeps.append(seconds)
        run_case.clock.sleep(seconds)
        if len(sleeps) == 2:
            run_case.publish()

    assert run_case.run(sleep=pause) == 0
    assert len(sleeps) == 2
    assert run_case.ui.events.count("send") == 1


def test_expired_lease_before_browser_never_sends(run_case):
    run_case.gateway.rows[2]["leaseExpiresAt"] = isoformat(NOW)
    assert run_case.run() == 1
    assert run_case.status()["errorCode"] == "CHATGPT_STALE_LEASE"
    assert run_case.ui.events == []


def test_lease_expiry_during_polling_rejects_late_receipt(run_case):
    run_case.gateway.rows[2]["leaseExpiresAt"] = isoformat(NOW + timedelta(seconds=4))

    def pause(seconds):
        run_case.clock.sleep(seconds)
        run_case.publish()

    assert run_case.run(sleep=pause) == 1
    assert run_case.status()["errorCode"] == "CHATGPT_STALE_LEASE"
    assert run_case.gateway.updates == []


def test_lease_change_between_prepare_and_send_fails_closed(run_case):
    run_case.ui.on_prepare = lambda: run_case.gateway.rows[2].update(leaseToken="replacement")
    assert run_case.run() == 1
    assert "send" not in run_case.ui.events


def test_request_change_between_prepare_and_send_fails_closed(run_case):
    run_case.ui.on_prepare = lambda: run_case.gateway.requests["request-1"].update(task="changed")
    assert run_case.run() == 1
    assert run_case.status()["errorCode"] == "CHATGPT_REQUEST_CHANGED"
    assert "send" not in run_case.ui.events


def test_bootstrap_tamper_fails_before_browser(run_case):
    (run_case.directory / "bootstrap.txt").write_bytes(b"tampered")
    assert run_case.run() == 1
    assert run_case.status()["errorCode"] == "CHATGPT_BOOTSTRAP_MISMATCH"
    assert run_case.ui.events == []


def test_selector_error_and_auth_failure_are_not_task_failure(run_case):
    run_case.ui.prepare_error = ChatGPTError("CHATGPT_SELECTOR_UNAVAILABLE")
    assert run_case.run() == 1
    assert run_case.status()["errorCode"] == "CHATGPT_SELECTOR_UNAVAILABLE"
    assert not run_case.status()["sendAttempted"]
    assert "receiptStatus" not in run_case.status()
    assert run_case.ui.events[-1] == "detach"


def test_auth_expires_after_preflight_no_send(run_case):
    run_case.ui.auth_error = ChatGPTError("CHATGPT_AUTH_REQUIRED")
    assert run_case.run() == 1
    assert run_case.status()["errorCode"] == "CHATGPT_AUTH_REQUIRED"
    assert not run_case.status()["sendAttempted"]


def test_write_ahead_send_error_still_waits_for_receipt_without_second_click(run_case):
    def on_send():
        assert run_case.status()["state"] == "SEND_ATTEMPTED"
        assert run_case.status()["sendAttempted"]
        run_case.publish()

    run_case.ui.on_send = on_send
    run_case.ui.send_error = RuntimeError("sensitive page error must not be logged")
    assert run_case.run() == 0
    assert run_case.ui.events.count("send") == 1
    assert "sensitive page" not in (run_case.directory / "browser.log").read_text()


def test_timeout_records_uncertainty_and_next_tick_requires_human(run_case):
    assert run_case.run() == 1
    status = run_case.status()
    assert status["errorCode"] == "CHATGPT_RECEIPT_TIMEOUT"
    assert status["sendAttempted"]
    assert status["retryPolicy"] == "RECONCILE_BEFORE_RETRY"
    assert status["needsReconciliation"]
    assert not status["operationalSuccess"]
    assert run_case.ui.events[-1] == "detach"
    dispatcher = Dispatcher(
        run_case.gateway,
        run_case.settings,
        MemoryAuditLogger(),
        {ExecutorType.CHATGPT: FakeLauncher()},
        clock=run_case.clock.now,
    )
    dispatcher.tick()
    assert run_case.gateway.read_job(2).status == DispatchState.WAITING_HUMAN
    assert run_case.gateway.read_job(2).attempt == 1


def test_crashed_browser_lease_at_max_attempts_requires_reconciliation(run_case):
    run_case.gateway.rows[2].update(maxAttempts=1, leaseExpiresAt=isoformat(NOW))
    dispatcher = Dispatcher(
        run_case.gateway, run_case.settings, MemoryAuditLogger(), {}, clock=lambda: NOW
    )
    dispatcher.tick()
    assert run_case.gateway.read_job(2).status == DispatchState.WAITING_HUMAN


def test_no_blind_retry_if_row_was_manually_moved_to_retryable(run_case):
    run_case.gateway.rows[2].update(status="FAILED_RETRYABLE", retryPolicy="SAFE_RETRY")
    Dispatcher(
        run_case.gateway, run_case.settings, MemoryAuditLogger(), {}, clock=lambda: NOW
    ).tick()
    assert run_case.gateway.read_job(2).status == DispatchState.WAITING_HUMAN


def test_same_attempt_worker_replay_does_not_send_or_overwrite_evidence(run_case):
    run_case.ui.on_send = run_case.publish
    assert run_case.run() == 0
    before = run_case.status()
    assert run_case.run() == 2
    assert run_case.status() == before
    assert run_case.ui.events.count("send") == 1


def test_worker_lock_blocks_duplicate_invocation(run_case):
    with LocalMutex(run_case.directory / "worker.lock"):
        assert run_case.run() == 2
    assert run_case.status()["state"] == "PREPARED"


def test_receipt_reconciled_by_dispatcher_before_worker_poll_is_accepted(run_case):
    def publish_and_reconcile():
        run_case.publish()
        run_case.gateway.rows[2].update(status="RESULT_STAGED", receiptDriveId="receipt-valid")

    run_case.ui.on_send = publish_and_reconcile
    assert run_case.run() == 0
    assert run_case.gateway.updates == []


def test_stale_result_staged_receipt_does_not_extend_lease(run_case):
    def stale():
        run_case.publish()
        run_case.gateway.rows[2].update(
            status="RESULT_STAGED", receiptDriveId="receipt-valid", leaseExpiresAt=isoformat(NOW)
        )

    run_case.ui.on_send = stale
    assert run_case.run() == 1
    assert run_case.status()["errorCode"] == "CHATGPT_STALE_LEASE"


def test_dispatcher_persists_reconcile_policy_before_worker_spawn(
    browser_settings, chatgpt_request
):
    gateway = FakeGateway([queue_row()], {"request-1": chatgpt_request})
    ui = FakeUI()
    launched = []

    def popen(*args, **kwargs):
        assert gateway.rows[2]["retryPolicy"] == "RECONCILE_BEFORE_RETRY"
        launched.append(args)
        return SimpleNamespace(pid=456)

    launcher = BrowserChatGPTLauncher(
        browser_settings,
        config_file=Path("config.json"),
        environment_check=lambda settings: settings.chatgpt_cdp_endpoint,
        session_factory=ui.session,
        popen_factory=popen,
    )
    result = Dispatcher(
        gateway,
        browser_settings,
        MemoryAuditLogger(),
        {ExecutorType.CHATGPT: launcher},
        clock=lambda: NOW,
    ).tick()
    assert result["outcome"] == "LAUNCHED"
    assert result["launcher"] == "BROWSER_CHATGPT_WORKER"
    assert len(launched) == 1
    assert gateway.read_job(2).status == DispatchState.RUNNING
    assert gateway.requests["request-1"] == chatgpt_request


def test_dry_run_never_opens_browser_for_auth(browser_settings, chatgpt_request):
    launcher = BrowserChatGPTLauncher(browser_settings, config_file=Path("config.json"))
    gateway = FakeGateway([queue_row()], {"request-1": chatgpt_request})
    result = Dispatcher(
        gateway,
        browser_settings,
        MemoryAuditLogger(),
        {ExecutorType.CHATGPT: launcher},
        clock=lambda: NOW,
    ).tick(dry_run=True)
    assert result["outcome"] == "DRY_RUN"
    assert gateway.updates == []


class Element:
    def __init__(self, *, text="", contenteditable=True, click=None):
        self.text = text
        self.contenteditable = contenteditable
        self.clicked = 0
        self.on_click = click or (lambda: None)

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def is_editable(self):
        return True

    def get_attribute(self, name):
        assert name == "contenteditable"
        return "true" if self.contenteditable else None

    def inner_text(self):
        assert self.contenteditable
        return self.text

    def input_value(self):
        assert not self.contenteditable
        return self.text

    def fill(self, value):
        self.text = value

    def click(self, **kwargs):
        if kwargs.get("trial"):
            return
        self.clicked += 1
        self.on_click()


class Locator:
    def __init__(self, elements):
        self.elements = elements

    def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]


class Page:
    def __init__(self, *, contenteditable=True):
        self.url = "https://chatgpt.com/c/old"
        self.composer = Element(contenteditable=contenteditable)
        self.new_chat = Element(click=self.reset)
        self.send = Element()
        self.elements = {
            SELECTORS["account"][0]: [Element()],
            SELECTORS["new_chat"][0]: [self.new_chat],
            SELECTORS["composer"][0 if contenteditable else 1]: [self.composer],
            SELECTORS["send"][0]: [self.send],
        }

    def reset(self):
        self.url = "https://chatgpt.com/"

    def get_by_test_id(self, value):
        return Locator(self.elements.get(("testid", value), []))

    def get_by_role(self, role, *, name):
        return Locator(self.elements.get((role, name.pattern), []))

    def locator(self, value):
        return Locator(self.elements.get(("css", value), []))

    def wait_for_url(self, url, **kwargs):
        assert self.url == url


@pytest.mark.parametrize("editable", [True, False])
def test_controls_new_chat_exact_utf8_and_only_one_send(editable):
    page = Page(contenteditable=editable)
    ui = ChatGPTControls(page, timeout_seconds=0)
    prompt = "FACTORY_EXECUTION_V1\nAção: café 🛠\n  espaços preservados\n"
    ui.prepare(prompt)
    assert page.new_chat.clicked == 1
    assert page.composer.text.encode("utf-8") == prompt.encode("utf-8")
    ui.send()
    assert page.send.clicked == 1
    with pytest.raises(ChatGPTError, match="ALREADY_ATTEMPTED"):
        ui.send()


@pytest.mark.parametrize("control", ["new_chat", "composer", "send"])
def test_missing_selector_fails_without_send(control):
    page = Page()
    del page.elements[SELECTORS[control][0]]
    with pytest.raises(ChatGPTError, match="SELECTOR_UNAVAILABLE"):
        ChatGPTControls(page, timeout_seconds=0).prepare("bootstrap")
    assert page.send.clicked == 0


def test_ambiguous_selector_does_not_pick_first():
    page = Page()
    page.elements[SELECTORS["new_chat"][0]].append(Element())
    with pytest.raises(ChatGPTError, match="SELECTOR_AMBIGUOUS"):
        ChatGPTControls(page, timeout_seconds=0).prepare("bootstrap")
    assert page.new_chat.clicked == page.send.clicked == 0


def test_semantic_selector_fallback_is_limited():
    page = Page()
    del page.elements[SELECTORS["new_chat"][0]]
    page.elements[SELECTORS["new_chat"][1]] = [page.new_chat]
    ChatGPTControls(page, timeout_seconds=0).prepare("bootstrap")
    assert page.new_chat.clicked == 1


@pytest.mark.parametrize("login_present", [True, False])
def test_missing_auth_distinguishes_unknown_ui(login_present):
    page = Page()
    del page.elements[SELECTORS["account"][0]]
    if login_present:
        page.elements[SELECTORS["login"][0]] = [Element()]
    expected = "CHATGPT_AUTH_REQUIRED" if login_present else "CHATGPT_AUTH_UNVERIFIED"
    with pytest.raises(ChatGPTError, match=expected):
        ChatGPTControls(page, timeout_seconds=0).require_auth()


def test_nonempty_composer_is_not_reused():
    page = Page()
    page.composer.text = "old draft"
    with pytest.raises(ChatGPTError, match="NEW_CHAT_NOT_EMPTY"):
        ChatGPTControls(page, timeout_seconds=0).prepare("bootstrap")
    assert page.composer.text == "old draft"


def test_mutated_composer_before_send_fails_closed():
    page = Page()
    ui = ChatGPTControls(page, timeout_seconds=0)
    ui.prepare("bootstrap")
    page.composer.text = "changed"
    with pytest.raises(ChatGPTError, match="BOOTSTRAP_MISMATCH"):
        ui.send()
    assert page.send.clicked == 0


def test_browser_code_has_no_response_reading_or_capture_api():
    root = Path(__file__).resolve().parents[1] / "factory_dispatcher"
    prohibited = {
        "content",
        "evaluate",
        "evaluate_all",
        "screenshot",
        "text_content",
        "all_text_contents",
        "all_inner_texts",
        "cookies",
        "storage_state",
        "on",
        "expect_response",
        "wait_for_response",
        "expect_request",
        "route",
    }
    for name in ("chatgpt_worker.py", "chatgpt_selectors.py", "chatgpt_browser.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in prohibited
                if node.func.attr in {"inner_text", "input_value"}:
                    assert ast.unparse(node.func.value) == "self._composer"
    assert set(SELECTORS) == {"account", "login", "new_chat", "composer", "send"}


def test_invalid_manifest_path_cannot_redirect_control_files(run_case):
    other = run_case.directory / "different.json"
    write_json(other, read_json_object(run_case.manifest))
    with pytest.raises(ChatGPTError, match="MANIFEST_INVALID"):
        run_worker(other, settings=run_case.settings)


def test_receipt_arriving_after_slow_network_timeout_is_not_success(run_case):
    run_case.ui.on_send = run_case.publish
    original = run_case.gateway.find_receipt

    def slow(*args):
        run_case.clock.sleep(31)
        return original(*args)

    run_case.gateway.find_receipt = slow
    assert run_case.run() == 1
    assert run_case.status()["errorCode"] == "CHATGPT_RECEIPT_TIMEOUT"
    assert "receiptDriveId" not in run_case.status()


def test_network_outage_after_send_keeps_browser_until_timeout(run_case):
    def unavailable(*args):
        assert "detach" not in run_case.ui.events
        raise OSError("server response with private fields")

    run_case.gateway.find_receipt = unavailable
    assert run_case.run() == 1
    assert run_case.clock.seconds == 30
    assert run_case.status()["errorCode"] == "CHATGPT_RECEIPT_TIMEOUT"
    assert "private fields" not in (run_case.directory / "browser.log").read_text()


def test_external_lease_extension_does_not_extend_worker_authority(run_case):
    run_case.gateway.rows[2]["leaseExpiresAt"] = isoformat(NOW + timedelta(seconds=4))

    def extend():
        run_case.gateway.rows[2]["leaseExpiresAt"] = isoformat(NOW + timedelta(hours=1))

    run_case.ui.on_send = extend
    assert run_case.run() == 1
    assert run_case.clock.seconds == 4
    assert run_case.status()["errorCode"] == "CHATGPT_STALE_LEASE"


def test_worker_tab_conflict_fails_without_attaching(run_case):
    with tab_lock(run_case.settings):
        assert run_case.run() == 1
    assert run_case.status()["errorCode"] == "CHATGPT_TAB_BUSY"
    assert run_case.ui.events == []


def test_google_login_is_not_started_by_worker(run_case, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("interactive login forbidden")

    monkeypatch.setattr(
        "factory_dispatcher.google_auth.InstalledAppFlow.from_client_secrets_file", forbidden
    )
    assert run_case.run(gateway=None) == 1
    assert run_case.status()["errorCode"] == "CHATGPT_GOOGLE_AUTH_REQUIRED"
    assert run_case.ui.events == []


def test_google_browser_transport_sets_http_timeout_without_login(browser_settings, monkeypatch):
    from factory_dispatcher.google_auth import build_google_services

    browser_settings.token_file.write_text("{}")
    credentials = SimpleNamespace(valid=True)
    monkeypatch.setattr(
        "factory_dispatcher.google_auth.Credentials.from_authorized_user_file",
        lambda *args: credentials,
    )
    calls = []

    def build(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr("factory_dispatcher.google_auth.build", build)
    build_google_services(browser_settings, interactive=False, http_timeout=10)
    assert len(calls) == 2
    assert all(call["http"].http.timeout == 10 for call in calls)
    assert browser_settings.token_file.read_text() == "{}"


def test_environment_refuses_debug_logging_that_could_expose_prompt(browser_settings, monkeypatch):
    monkeypatch.setattr(
        "factory_dispatcher.chatgpt_browser.importlib.util.find_spec", lambda _: True
    )
    monkeypatch.setenv("DEBUG", "pw:api")
    with pytest.raises(ChatGPTError, match="UNSAFE_DEBUG_ENV"):
        check_environment(browser_settings)


def test_profile_permission_failure_prevents_claim(browser_settings, monkeypatch):
    original = Path.mkdir

    def denied(path, *args, **kwargs):
        if path == browser_settings.chatgpt_profile:
            raise PermissionError("no write access")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(ChatGPTError, match="PROFILE_UNAVAILABLE"), profile_lock(browser_settings):
        pass


def test_receipt_releases_tab_without_browser_close_status(run_case):
    run_case.ui.on_send = run_case.publish
    assert run_case.run() == 0
    assert "browserClosedAt" not in run_case.status()
    assert "disconnectedAt" in run_case.status()
    assert read_json_object(tab_state_path(run_case.settings))["state"] == "AVAILABLE"


def test_disconnect_failure_is_not_operational_success(run_case):
    @contextmanager
    def failing_close(settings, executable):
        yield run_case.ui
        raise RuntimeError("private browser details")

    run_case.ui.on_send = run_case.publish
    assert run_case.run(session_factory=failing_close) == 1
    assert run_case.status()["receiptDriveId"] == "receipt-valid"
    assert not run_case.status()["operationalSuccess"]
    assert run_case.status()["needsReconciliation"]


def test_worker_never_invents_receipt_or_task_success_in_local_failure(run_case):
    assert run_case.run() == 1
    assert "receiptStatus" not in run_case.status()
    assert not list(run_case.directory.glob("*RECEIPT*"))
    assert run_case.gateway.receipts == {}


def test_engine_uses_same_strict_receipt_contract_for_browser_attempt(run_case):
    run_case.publish(schemaVersion=99)
    Dispatcher(
        run_case.gateway, run_case.settings, MemoryAuditLogger(), {}, clock=lambda: NOW
    ).tick()
    assert run_case.gateway.read_job(2).status == DispatchState.CLAIMED
    assert run_case.gateway.updates == []


def test_valid_receipt_is_reconciled_before_local_timeout_evidence(run_case):
    assert run_case.run() == 1
    run_case.publish()
    Dispatcher(
        run_case.gateway, run_case.settings, MemoryAuditLogger(), {}, clock=run_case.clock.now
    ).tick()
    assert run_case.gateway.read_job(2).status == DispatchState.RESULT_STAGED
    assert run_case.gateway.rows[2]["receiptDriveId"] == "receipt-valid"


def test_worker_code_only_uses_read_only_gateway_methods():
    path = Path(__file__).resolve().parents[1] / "factory_dispatcher/chatgpt_worker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed = {
        "read_queue",
        "read_job",
        "read_factory_config",
        "read_json_artifact",
        "find_receipt",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if ast.unparse(node.func.value) == "gateway":
                assert node.func.attr in allowed
