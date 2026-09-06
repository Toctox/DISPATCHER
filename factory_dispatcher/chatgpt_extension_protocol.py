"""Closed, versioned protocol. No browser scripting or model-output transport."""

from __future__ import annotations

import hashlib
import hmac
import os
import re

from .chatgpt_browser import ChatGPTError
from .mutex import LocalMutex
from .receipts import decode_json_object, read_json_object, write_json

VERSION = 1
CAPABILITY = "FACTORY_TAB_V1"
MAX_MESSAGE = 512 * 1024
MAX_BOOTSTRAP = 64 * 1024
ACTIONS = frozenset({"PRECHECK", "NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"})
CONTROL = "RECEIPT_CONFIRMED"
RECOVER = "RECOVER_PRE_SEND"
# Internal bridge-to-extension controls; never accepted directly from a worker.
RECOVERY_RELEASE = "PRE_SEND_RECOVERY_RELEASE"
RECOVERY_RELEASE_BOOTSTRAP = "PRE_BOOTSTRAP_RECOVERY_RELEASE"
CONTROLS = frozenset({CONTROL, RECOVER, RECOVERY_RELEASE, RECOVERY_RELEASE_BOOTSTRAP})
CODES = frozenset(
    {
        "CHATGPT_BRIDGE_UNAVAILABLE",
        "CHATGPT_EXTENSION_NOT_PAIRED",
        "CHATGPT_EXTENSION_VERSION_MISMATCH",
        "CHATGPT_FACTORY_TAB_NOT_CONFIGURED",
        "CHATGPT_FACTORY_TAB_NOT_FOUND",
        "CHATGPT_FACTORY_TAB_INVALID",
        "CHATGPT_AUTH_REQUIRED",
        "CHATGPT_SELECTOR_UNAVAILABLE",
        "CHATGPT_TAB_BUSY",
        "CHATGPT_BINDING_CHANGED",
        "CHATGPT_PROTOCOL_INVALID",
        "CHATGPT_BOOTSTRAP_MISMATCH",
        "CHATGPT_SEND_UNCERTAIN",
        "CHATGPT_NEW_CHAT_FAILED",
        "CHATGPT_RESERVATION_INVALID",
        "CHATGPT_RECOVERY_REFUSED",
        "CHATGPT_RECOVERY_UNCERTAIN",
    }
)


def fail(code="CHATGPT_PROTOCOL_INVALID"):
    raise ChatGPTError(code)


def durable_write(path, payload, *, exclusive=False):
    write_json(path, payload, exclusive=exclusive)
    # Flush write-ahead evidence before issuing any side effect.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def check_settings(settings, *, enabled=True):
    if enabled and (
        settings.chatgpt_browser_mode != "EXTENSION_BRIDGE"
        or not settings.chatgpt_extension_bridge_enabled
    ):
        fail("CHATGPT_BRIDGE_UNAVAILABLE")
    if settings.chatgpt_extension_bridge_host != "127.0.0.1":
        fail()
    port = settings.chatgpt_extension_bridge_port
    if type(port) is not int or not 1 <= port <= 65535:
        fail()
    if not re.fullmatch(r"[a-p]{32}", settings.chatgpt_extension_id):
        fail("CHATGPT_EXTENSION_NOT_PAIRED")
    # Secrets always stay in the already ignored project state tree.
    if not settings.chatgpt_pairing_file.resolve().is_relative_to(
        (settings.project_root / "state").resolve()
    ):
        fail("CHATGPT_EXTENSION_NOT_PAIRED")


def load_pairing(settings):
    check_settings(settings, enabled=False)
    try:
        value = read_json_object(settings.chatgpt_pairing_file)
    except Exception:
        fail("CHATGPT_EXTENSION_NOT_PAIRED")
    if type(value.get("protocolVersion")) is not int or value["protocolVersion"] != VERSION:
        fail("CHATGPT_EXTENSION_VERSION_MISMATCH")
    if value.get("extensionId") != settings.chatgpt_extension_id:
        fail("CHATGPT_EXTENSION_NOT_PAIRED")
    for key in ("extensionSecret", "controlSecret"):
        if not isinstance(value.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            fail("CHATGPT_EXTENSION_NOT_PAIRED")
    if value["extensionSecret"] == value["controlSecret"]:
        fail("CHATGPT_EXTENSION_NOT_PAIRED")
    return value


def proof(secret, role, nonce, extension_id):
    payload = f"PROJECT_FACTORY_BRIDGE_V1\n{role}\n{nonce}\n{extension_id}".encode()
    return hmac.new(bytes.fromhex(secret), payload, hashlib.sha256).hexdigest()


def decode(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MESSAGE:
        fail()
    try:
        result = decode_json_object(raw.encode("utf-8"))
    except Exception:
        fail()
    if type(result.get("protocolVersion")) is not int or result["protocolVersion"] != VERSION:
        fail("CHATGPT_EXTENSION_VERSION_MISMATCH")
    return result


def binding(value):
    if not isinstance(value, dict) or set(value) != {
        "extensionId",
        "browserInstanceId",
        "tabId",
        "documentId",
    }:
        fail()
    if not re.fullmatch(r"[a-p]{32}", str(value["extensionId"])):
        fail()
    if type(value["tabId"]) is not int or value["tabId"] < 0:
        fail()
    for key in ("browserInstanceId", "documentId"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", value[key]):
            fail()
    return dict(value)


def operation(value):
    return value.get("control") if value.get("type") == "CONTROL" else value.get("action")


def command(value):
    if type(value.get("protocolVersion")) is not int or value["protocolVersion"] != VERSION:
        fail("CHATGPT_EXTENSION_VERSION_MISMATCH")
    action = operation(value)
    if not (
        (value.get("type") == "COMMAND" and action in ACTIONS)
        or (value.get("type") == "CONTROL" and action in CONTROLS)
    ):
        fail()
    required = {"protocolVersion", "type", "requestId"}
    required.add("control" if value["type"] == "CONTROL" else "action")
    if action == RECOVER:
        required |= {"dispatchId", "attemptId"}
    elif action != "PRECHECK":
        required |= {"binding", "reservationId"}
    if action == "INSERT_BOOTSTRAP":
        required.add("bootstrap")
    if action == "SEND":
        required.add("expiresAt")
    if action == "PRECHECK" and "binding" in value:
        required.add("binding")
    if set(value) != required:
        fail()
    if action == RECOVER:
        recovery_ids(value["dispatchId"], value["attemptId"])
    for key in ("requestId", "reservationId"):
        if key in value and (
            not isinstance(value[key], str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", value[key])
        ):
            fail()
    if "binding" in value:
        binding(value["binding"])
    if action == "SEND" and (type(value["expiresAt"]) is not int or value["expiresAt"] <= 0):
        fail()
    if action == "INSERT_BOOTSTRAP" and (
        not isinstance(value["bootstrap"], str)
        or not 1 <= len(value["bootstrap"].encode("utf-8")) <= MAX_BOOTSTRAP
    ):
        fail("CHATGPT_BOOTSTRAP_MISMATCH")
    return value


def recovery_ids(dispatch_id, attempt_id):
    for value in (dispatch_id, attempt_id):
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,158}[A-Za-z0-9_-]|[A-Za-z0-9]", value
        ):
            fail("CHATGPT_RECOVERY_REFUSED")
    if not re.fullmatch(re.escape(dispatch_id) + r"-A[0-9]{3,}", attempt_id):
        fail("CHATGPT_RECOVERY_REFUSED")


def result(value, request):
    if type(value.get("protocolVersion")) is not int or value["protocolVersion"] != VERSION:
        fail("CHATGPT_EXTENSION_VERSION_MISMATCH")
    required = {"protocolVersion", "type", "requestId", "status"}
    if value.get("status") == "ERROR":
        required.add("errorCode")
        if value.get("errorCode") not in CODES:
            fail()
    elif value.get("status") == "OK":
        if operation(request) == "PRECHECK":
            required.add("binding")
            binding(value.get("binding"))
    else:
        fail()
    if (
        set(value) != required
        or value.get("type") != "RESULT"
        or (value.get("requestId") != request["requestId"])
    ):
        fail()
    return value


def tab_lock(settings):
    # Fixed project-wide lock, not keyed by configurable port/stateDirectory.
    return LocalMutex(settings.project_root / "state" / "chatgpt-bridge" / "tab.lock")


class Reservation:
    """Never clear uncertainty on disconnect, timeout, restart, or failed acknowledgement."""

    def __init__(self, path):
        self.path = path
        self.active = read_json_object(path) if path.exists() else {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        durable_write(self.path, self.active)

    def before(self, request):
        action = operation(request)
        if action in {"PRECHECK", "NEW_CHAT"}:
            if self.active:
                fail("CHATGPT_TAB_BUSY")
            if action == "NEW_CHAT":
                self.active = {
                    "reservationId": request["reservationId"],
                    "binding": request["binding"],
                    "phase": "NEW_CHAT_ATTEMPTED",
                }
                self.save()
            return
        if not self.active or any(
            self.active.get(key) != request[key] for key in ("reservationId", "binding")
        ):
            fail("CHATGPT_RESERVATION_INVALID")
        expected = {
            "INSERT_BOOTSTRAP": "NEW_CHAT",
            "SEND": "INSERT_BOOTSTRAP",
            CONTROL: "SEND_ATTEMPTED",
        }
        if self.active.get("phase") != expected[action]:
            fail("CHATGPT_RESERVATION_INVALID")
        if action != CONTROL:
            self.active["phase"] = f"{action}_ATTEMPTED"
            self.save()

    def after(self, request):
        action = operation(request)
        if action == CONTROL:
            self.active = {}
        elif action in {"NEW_CHAT", "INSERT_BOOTSTRAP"}:
            self.active["phase"] = action
        else:
            return
        self.save()
