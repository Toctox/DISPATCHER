from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import NOW, FakeGateway, queue_row
from test_chatgpt_browser import FakeClock

from factory_dispatcher.audit import MemoryAuditLogger
from factory_dispatcher.bootstrap import build_bootstrap
from factory_dispatcher.chatgpt_browser import ChatGPTError
from factory_dispatcher.chatgpt_extension_bridge import Bridge, quiet_logger
from factory_dispatcher.chatgpt_extension_client import BridgeClient
from factory_dispatcher.chatgpt_extension_pair import create_pairing
from factory_dispatcher.chatgpt_extension_protocol import (
    ACTIONS,
    CAPABILITY,
    CONTROL,
    VERSION,
    Reservation,
    check_settings,
    command,
    decode,
    load_pairing,
    proof,
    result,
    tab_lock,
)
from factory_dispatcher.chatgpt_extension_worker import run_worker
from factory_dispatcher.cli import build_launchers
from factory_dispatcher.engine import Dispatcher
from factory_dispatcher.launchers import BrowserChatGPTLauncher, CodexLauncher
from factory_dispatcher.launchers.chatgpt_extension import ExtensionChatGPTLauncher
from factory_dispatcher.models import DispatchRequest, ExecutorType, Receipt, isoformat
from factory_dispatcher.mutex import MutexBusyError
from factory_dispatcher.receipts import read_json_object

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "browser_extension" / "project_factory_bridge"
BINDING = {
    "extensionId": "a" * 32,
    "browserInstanceId": "browser-test",
    "tabId": 7,
    "documentId": "document-test",
}


@pytest.fixture
def extension_settings(local_settings):
    return replace(
        local_settings,
        chatgpt_browser_mode="EXTENSION_BRIDGE",
        chatgpt_extension_bridge_enabled=True,
        chatgpt_extension_id="a" * 32,
        chatgpt_browser_timeout_seconds=30,
        chatgpt_browser_poll_seconds=5,
    )


def rpc(action="PRECHECK", **fields):
    value = {
        "protocolVersion": VERSION,
        "type": "COMMAND",
        "requestId": "request-1",
        "action": action,
    }
    if action != "PRECHECK":
        value.update(binding=BINDING, reservationId="reservation-1")
    if action == CONTROL:
        value.update(type="CONTROL", control=value.pop("action"))
    if action == "INSERT_BOOTSTRAP":
        value["bootstrap"] = "exact\nbootstrap ç"
    if action == "SEND":
        value["expiresAt"] = 9999999999999
    return command({**value, **fields})


class FakeClient:
    def __init__(self):
        self.calls = []
        self.errors = {}
        self.on_send = lambda: None
        self.on_insert = lambda: None

    def call(self, action, **fields):
        self.calls.append((action, fields))
        if action == "SEND":
            self.on_send()
        if action == "INSERT_BOOTSTRAP":
            self.on_insert()
        if action in self.errors:
            raise ChatGPTError(self.errors[action])
        return {"binding": dict(BINDING)} if action == "PRECHECK" else {}


@pytest.fixture
def extension_case(extension_settings, chatgpt_request):
    gateway = FakeGateway(
        [
            queue_row(
                status="CLAIMED",
                attempt=1,
                leaseToken="test-lease",
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
    client = FakeClient()
    processes = []

    def popen(*args, **kwargs):
        processes.append((args, kwargs))
        return SimpleNamespace(pid=987)

    launcher = ExtensionChatGPTLauncher(
        extension_settings,
        config_file=extension_settings.project_root / "config.json",
        client_factory=lambda settings: client,
        popen_factory=popen,
    )
    launcher.preflight(request)
    launcher.launch(job, request, prompt, receipt_folder_id=gateway.factory.receipts_folder_id)
    client.calls.clear()
    directory = (
        extension_settings.state_directory / "chatgpt-runs" / job.dispatch_id / job.attempt_id
    )
    clock = FakeClock()
    case = SimpleNamespace(
        settings=extension_settings,
        gateway=gateway,
        job=job,
        client=client,
        prompt=prompt,
        directory=directory,
        clock=clock,
        processes=processes,
    )

    def publish(**overrides):
        raw = {
            "schemaVersion": 1,
            "artifactType": "EXECUTION_RECEIPT",
            "dispatchId": job.dispatch_id,
            "attemptId": job.attempt_id,
            "leaseToken": job.lease_token,
            "agentId": job.agent_id,
            "requestDriveId": job.request_drive_id,
            "status": "SUCCEEDED",
            "executorStatement": "Fixture",
            "producedArtifacts": [],
            "error": {},
            "startedAt": isoformat(NOW),
            "finishedAt": isoformat(NOW),
            **overrides,
        }
        gateway.receipts[f"EXECUTION_RECEIPT__{job.dispatch_id}__{job.attempt_id}.json"] = Receipt(
            "receipt-1", raw
        )

    case.publish = publish
    case.status = lambda: read_json_object(directory / "status.json")
    case.run = lambda: run_worker(
        directory / "launch.json",
        settings=extension_settings,
        gateway=gateway,
        client_factory=lambda settings: client,
        clock=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return case


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "::1", "127.0.0.2", "192.168.1.2"])
def test_only_literal_loopback(extension_settings, host):
    with pytest.raises(ChatGPTError):
        check_settings(replace(extension_settings, chatgpt_extension_bridge_host=host))


@pytest.mark.parametrize("port", [0, 65536, True, "8765"])
def test_port_validation(extension_settings, port):
    with pytest.raises(ChatGPTError):
        check_settings(replace(extension_settings, chatgpt_extension_bridge_port=port))


def test_pairing_one_time_private_and_separate(extension_settings):
    path = create_pairing(extension_settings)
    original = path.read_bytes()
    value = load_pairing(extension_settings)
    assert len(value["extensionSecret"]) == 64
    assert value["extensionSecret"] != value["controlSecret"]
    with pytest.raises(FileExistsError):
        create_pairing(extension_settings)
    assert path.read_bytes() == original
    with pytest.raises(ChatGPTError, match="NOT_PAIRED"):
        create_pairing(
            replace(
                extension_settings,
                chatgpt_extension_pairing_secret_file=path.parent.parent.parent / "leak.json",
            )
        )


def test_missing_pairing(extension_settings):
    with pytest.raises(ChatGPTError, match="NOT_PAIRED"):
        Bridge(extension_settings)


@pytest.mark.parametrize(
    "remote,path,origins,allowed",
    [
        (("127.0.0.1", 1), "/extension", ["chrome-extension://" + "a" * 32], True),
        (("127.0.0.1", 1), "/extension", [], False),
        (("127.0.0.1", 1), "/extension", ["https://chatgpt.com"], False),
        (("127.0.0.1", 1), "/extension", ["chrome-extension://" + "b" * 32], False),
        (("10.0.0.1", 1), "/extension", ["chrome-extension://" + "a" * 32], False),
        (("127.0.0.1", 1), "/worker", [], True),
        (("127.0.0.1", 1), "/worker", ["chrome-extension://" + "a" * 32], False),
        (("127.0.0.1", 1), "/worker", ["null"], False),
        (("127.0.0.1", 1), "/worker?token=x", [], False),
    ],
)
def test_exact_origin_and_role(extension_settings, remote, path, origins, allowed):
    create_pairing(extension_settings)
    assert Bridge(extension_settings).origin_allowed(remote, path, origins) is allowed


@pytest.mark.parametrize("version", [0, 2, "1", True, None])
def test_protocol_version(version):
    with pytest.raises(ChatGPTError, match="VERSION_MISMATCH"):
        decode(json.dumps({"protocolVersion": version}))


@pytest.mark.parametrize(
    "action",
    ["GET_TEXT", "GET_HTML", "EVALUATE_JS", "EXECUTE_SCRIPT", "CLICK_SELECTOR", "CREATE_TAB"],
)
def test_no_generic_operations(action):
    with pytest.raises(ChatGPTError, match="PROTOCOL_INVALID"):
        rpc(action)
    assert ACTIONS == {"PRECHECK", "NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"}


def test_response_cannot_transport_text_or_unknown_code():
    for field in [
        {"text": "model output"},
        {"html": "<p>output</p>"},
        {"errorCode": "arbitrary secret"},
    ]:
        with pytest.raises(ChatGPTError):
            result(
                {
                    "protocolVersion": 1,
                    "type": "RESULT",
                    "requestId": "request-1",
                    "status": "OK",
                    "binding": BINDING,
                    **field,
                },
                rpc(),
            )


def test_reservation_write_ahead_restart_and_release(tmp_path):
    path = tmp_path / "reservation.json"
    reservation = Reservation(path)
    for action in ["NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"]:
        reservation.before(rpc(action))
        assert Reservation(path).active["phase"] == f"{action}_ATTEMPTED"
        if action != "SEND":
            reservation.after(rpc(action))
    restarted = Reservation(path)
    for action in ["PRECHECK", "NEW_CHAT", "SEND"]:
        with pytest.raises(ChatGPTError):
            restarted.before(rpc(action))
    restarted.before(rpc(CONTROL))
    restarted.after(rpc(CONTROL))
    assert not Reservation(path).active


def test_local_lock_cannot_be_evaded_by_port_or_state(extension_settings):
    alternate = replace(
        extension_settings,
        chatgpt_extension_bridge_port=8888,
        state_directory=extension_settings.project_root / "state-other",
    )
    with tab_lock(extension_settings), pytest.raises(MutexBusyError), tab_lock(alternate):
        pass


@pytest.mark.parametrize(
    "code",
    [
        "CHATGPT_BRIDGE_UNAVAILABLE",
        "CHATGPT_EXTENSION_NOT_PAIRED",
        "CHATGPT_EXTENSION_VERSION_MISMATCH",
        "CHATGPT_FACTORY_TAB_NOT_CONFIGURED",
        "CHATGPT_FACTORY_TAB_NOT_FOUND",
        "CHATGPT_FACTORY_TAB_INVALID",
        "CHATGPT_AUTH_REQUIRED",
        "CHATGPT_SELECTOR_UNAVAILABLE",
        "CHATGPT_TAB_BUSY",
    ],
)
def test_preflight_failure_never_claims(extension_settings, chatgpt_request, code):
    gateway = FakeGateway([queue_row()], {"request-1": chatgpt_request})
    original = dict(gateway.rows[2])
    client = FakeClient()
    client.errors["PRECHECK"] = code
    launcher = ExtensionChatGPTLauncher(
        extension_settings,
        config_file=extension_settings.project_root / "config.json",
        client_factory=lambda settings: client,
    )
    outcome = Dispatcher(
        gateway,
        extension_settings,
        MemoryAuditLogger(),
        {ExecutorType.CHATGPT: launcher},
        clock=lambda: NOW,
    ).tick()
    assert outcome["outcome"] == "PREFLIGHT_BLOCKED"
    assert gateway.rows[2] == original
    assert not gateway.updates and not gateway.uploads and not gateway.events


def test_receipt_only_completion_exact_bootstrap_no_remote_writes(extension_case):
    case = extension_case

    def send():
        assert case.status()["sendAttempted"] is True
        assert case.status()["needsReconciliation"] is True
        case.publish()

    case.client.on_send = send
    assert case.run() == 0
    assert [item[0] for item in case.client.calls] == [
        "PRECHECK",
        "NEW_CHAT",
        "INSERT_BOOTSTRAP",
        "SEND",
        CONTROL,
    ]
    assert case.client.calls[2][1]["bootstrap"] == case.prompt
    assert case.status()["state"] == "FINISHED"
    assert not case.gateway.updates and not case.gateway.uploads and not case.gateway.events
    assert case.run() == 2
    assert sum(item[0] == "SEND" for item in case.client.calls) == 1


def test_timeout_never_resends_or_releases(extension_case):
    assert extension_case.run() == 1
    assert extension_case.status()["errorCode"] == "CHATGPT_RECEIPT_TIMEOUT"
    assert extension_case.status()["needsReconciliation"] is True
    assert [a for a, _ in extension_case.client.calls].count("SEND") == 1
    assert CONTROL not in [a for a, _ in extension_case.client.calls]


def test_uncertain_send_still_accepts_only_receipt(extension_case):
    extension_case.client.on_send = extension_case.publish
    extension_case.client.errors["SEND"] = "CHATGPT_BRIDGE_UNAVAILABLE"
    assert extension_case.run() == 0
    assert [a for a, _ in extension_case.client.calls].count("SEND") == 1


@pytest.mark.parametrize(
    "field", ["dispatchId", "attemptId", "leaseToken", "agentId", "requestDriveId"]
)
def test_wrong_receipt_never_completes(extension_case, field):
    extension_case.client.on_send = lambda: extension_case.publish(**{field: "wrong"})
    assert extension_case.run() == 1
    assert extension_case.status()["needsReconciliation"] is True
    assert CONTROL not in [a for a, _ in extension_case.client.calls]


@pytest.mark.parametrize("when", ["before", "insert", "poll"])
def test_stale_lease_fail_closed(extension_case, when):
    case = extension_case

    def stale():
        case.gateway.rows[2]["leaseToken"] = "replacement"

    if when == "before":
        stale()
    elif when == "insert":
        case.client.on_insert = stale
    else:
        case.client.on_send = stale
    assert case.run() == 1
    assert case.status()["errorCode"] == "CHATGPT_STALE_LEASE"
    assert [a for a, _ in case.client.calls].count("SEND") == (1 if when == "poll" else 0)
    assert CONTROL not in [a for a, _ in case.client.calls]


def test_failed_release_preserves_receipt_completion(extension_case):
    extension_case.client.on_send = extension_case.publish
    extension_case.client.errors[CONTROL] = "CHATGPT_BRIDGE_UNAVAILABLE"
    assert extension_case.run() == 0
    assert extension_case.status()["reservationReleasePending"] is True
    assert extension_case.status()["receiptDriveId"] == "receipt-1"


@pytest.mark.parametrize("action", ["PRECHECK", "NEW_CHAT", "INSERT_BOOTSTRAP"])
def test_factory_binding_changed_before_send_stops_for_reconciliation(extension_case, action):
    extension_case.client.errors[action] = "CHATGPT_BINDING_CHANGED"
    assert extension_case.run() == 1
    assert extension_case.status()["errorCode"] == "CHATGPT_BINDING_CHANGED"
    assert extension_case.status()["sendAttempted"] is False
    assert extension_case.status()["needsReconciliation"] is True
    assert "SEND" not in [name for name, _ in extension_case.client.calls]
    assert CONTROL not in [name for name, _ in extension_case.client.calls]


@pytest.mark.parametrize("capabilities", [None, [], ["EXACTLY_ONE_TAB_V1"]])
def test_legacy_extension_without_factory_tab_capability_is_rejected(
    extension_settings, capabilities
):
    create_pairing(extension_settings)
    bridge = Bridge(extension_settings)

    class FakeSocket:
        request = SimpleNamespace(path="/extension")
        messages = []

        async def send(self, raw):
            self.messages.append(json.loads(raw))

        async def recv(self):
            challenge = self.messages[0]
            message = {
                "protocolVersion": VERSION,
                "type": "AUTH",
                "proof": proof(
                    bridge.pairing["extensionSecret"],
                    "extension",
                    challenge["nonce"],
                    extension_settings.chatgpt_extension_id,
                ),
            }
            if capabilities is not None:
                message["capabilities"] = capabilities
            return json.dumps(message)

    with pytest.raises(ChatGPTError, match="VERSION_MISMATCH"):
        asyncio.run(bridge.authenticate(FakeSocket()))


def test_registration_is_not_remote_browser_control():
    source = (EXTENSION / "controller.js").read_text(encoding="utf-8")
    assert "tabs.query" not in source
    assert "CHATGPT_TAB_AMBIGUOUS" not in source
    registry = (EXTENSION / "factory_tab.js").read_text(encoding="utf-8")
    for forbidden in ["active: true", "lastFocusedWindow", "lastAccessed", ".title"]:
        assert forbidden not in registry
    for action in ["REGISTER", "CLEAR", "FACTORY_TAB_OPTIONS"]:
        with pytest.raises(ChatGPTError, match="PROTOCOL_INVALID"):
            rpc(action)


def test_mode_routing_no_extension_fallback_and_codex_unchanged(extension_settings):
    settings = replace(
        extension_settings,
        chatgpt_extension_bridge_enabled=False,
        browser_chatgpt_launch_enabled=True,
    )
    launchers = build_launchers(settings, settings.project_root / "config.json")
    assert isinstance(launchers[ExecutorType.CHATGPT], ExtensionChatGPTLauncher)
    assert isinstance(launchers[ExecutorType.CODEX], CodexLauncher)
    cdp = build_launchers(replace(settings, chatgpt_browser_mode="ATTACH_CDP"), Path("config.json"))
    assert isinstance(cdp[ExecutorType.CHATGPT], BrowserChatGPTLauncher)


def test_manifest_permissions_and_no_remote_control():
    manifest = json.loads((EXTENSION / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    assert manifest["host_permissions"] == ["https://chatgpt.com/*"]
    assert set(manifest["permissions"]) == {"storage", "alarms"}
    assert manifest["content_scripts"][0]["matches"] == ["https://chatgpt.com/*"]
    assert not manifest.get("externally_connectable")
    assert not manifest.get("web_accessible_resources")
    scripts = "\n".join(path.read_text() for path in EXTENSION.glob("*.js"))
    for forbidden in [
        "eval(",
        "new Function",
        "tabs.create",
        "tabs.update",
        "tabs.remove",
        "fetch(",
        "XMLHttpRequest",
        "innerHTML",
        "outerHTML",
        "document.body",
        "console.",
        "clipboard",
        "conversation-turn",
        "data-message-author-role",
        "EVALUATE_JS",
        "EXECUTE_SCRIPT",
        "CLICK_SELECTOR",
        "GET_TEXT",
        "GET_HTML",
    ]:
        assert forbidden not in scripts
    selectors = (EXTENSION / "selectors.js").read_text()
    assert "assistant" not in selectors and "response" not in selectors
    assert "TRUSTED_CONTEXTS" in scripts


def test_javascript_syntax_and_fake_browser_suite():
    node = shutil.which("node") or str(ROOT / ".venv/Lib/site-packages/playwright/driver/node.exe")
    assert Path(node).is_file(), "Node is required for extension validation (no Chrome needed)."
    for path in EXTENSION.glob("*.js"):
        subprocess.run([node, "--check", str(path)], check=True, capture_output=True, text=True)
    completed = subprocess.run(
        [node, "--test", str(ROOT / "tests/extension_bridge.test.mjs")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_codex_and_cdp_implementation_files_unchanged():
    hashes = {
        "launchers/codex.py": "A3BBAAB94EECFA91153EC47779FDD1F702D2EAA0711004C3CF10D038CCE97BA7",
        "codex_worker.py": "6F9627A1921A4D2495425A42463B3ADB66171C445F0132246C84F1D3D9BF5AC6",
        "chatgpt_browser.py": "D6CEA90308330C63C13A31FA1BB70DC89640480CAD8CFD7D9A5C42EE43EAB95F",
    }
    for name, expected in hashes.items():
        assert (
            hashlib.sha256((ROOT / "factory_dispatcher" / name).read_bytes()).hexdigest().upper()
            == expected
        )


def test_real_loopback_transport_with_fake_extension(extension_settings):
    # Only a local ephemeral TCP socket and a fake extension; no browser/Google/QUEUE.
    from websockets.asyncio.client import connect
    from websockets.asyncio.server import serve
    from websockets.exceptions import ConnectionClosed, InvalidStatus

    create_pairing(extension_settings)

    async def scenario():
        bridge = Bridge(extension_settings)
        origin = "chrome-extension://" + extension_settings.chatgpt_extension_id
        async with serve(
            bridge.handle,
            "127.0.0.1",
            0,
            process_request=bridge.process_request,
            logger=quiet_logger(),
            close_timeout=1,
        ) as server:
            assert all(sock.getsockname()[0] == "127.0.0.1" for sock in server.sockets)
            port = server.sockets[0].getsockname()[1]
            settings = replace(extension_settings, chatgpt_extension_bridge_port=port)
            url = f"ws://127.0.0.1:{port}"
            with pytest.raises(InvalidStatus):
                async with connect(url + "/extension", origin="https://evil.invalid", proxy=None):
                    pass
            for version, key in [(1, "0" * 64), (2, bridge.pairing["extensionSecret"])]:
                async with connect(url + "/extension", origin=origin, proxy=None) as bad:
                    challenge = json.loads(await bad.recv())
                    await bad.send(
                        json.dumps(
                            {
                                "protocolVersion": version,
                                "type": "AUTH",
                                "capabilities": [CAPABILITY],
                                "proof": proof(
                                    key,
                                    "extension",
                                    challenge["nonce"],
                                    settings.chatgpt_extension_id,
                                ),
                            }
                        )
                    )
                    with pytest.raises(ConnectionClosed):
                        await bad.recv()
            # Extension key cannot impersonate the worker, even with a forged/missing Origin.
            async with connect(url + "/worker", proxy=None) as bad:
                challenge = json.loads(await bad.recv())
                await bad.send(
                    json.dumps(
                        {
                            "protocolVersion": 1,
                            "type": "AUTH",
                            "proof": proof(
                                bridge.pairing["extensionSecret"],
                                "worker",
                                challenge["nonce"],
                                settings.chatgpt_extension_id,
                            ),
                        }
                    )
                )
                with pytest.raises(ConnectionClosed):
                    await bad.recv()
            async with connect(url + "/extension", origin=origin, proxy=None) as extension:
                challenge = json.loads(await extension.recv())
                await extension.send(
                    json.dumps(
                        {
                            "protocolVersion": 1,
                            "type": "AUTH",
                            "capabilities": [CAPABILITY],
                            "proof": proof(
                                bridge.pairing["extensionSecret"],
                                "extension",
                                challenge["nonce"],
                                settings.chatgpt_extension_id,
                            ),
                        }
                    )
                )
                ready = json.loads(await extension.recv())
                assert ready["proof"] == proof(
                    bridge.pairing["extensionSecret"],
                    "server-extension",
                    challenge["nonce"],
                    settings.chatgpt_extension_id,
                )

                async def fake_extension():
                    async for raw in extension:
                        request = json.loads(raw)
                        fields = {"binding": BINDING} if request.get("action") == "PRECHECK" else {}
                        await extension.send(
                            json.dumps(
                                {
                                    "protocolVersion": 1,
                                    "type": "RESULT",
                                    "requestId": request["requestId"],
                                    "status": "OK",
                                    **fields,
                                }
                            )
                        )

                task = asyncio.create_task(fake_extension())
                client = BridgeClient(settings)
                try:
                    response = await asyncio.to_thread(client.call, "PRECHECK")
                    assert response["binding"] == BINDING
                    for action in ["NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"]:
                        fields = rpc(action)
                        await asyncio.to_thread(
                            client.call,
                            action,
                            **{
                                k: v
                                for k, v in fields.items()
                                if k not in {"protocolVersion", "type", "requestId", "action"}
                            },
                        )
                    with pytest.raises(ChatGPTError, match="TAB_BUSY"):
                        await asyncio.to_thread(client.call, "PRECHECK")
                    await asyncio.to_thread(
                        client.call, CONTROL, binding=BINDING, reservationId="reservation-1"
                    )
                    assert not bridge.reservation.active
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
