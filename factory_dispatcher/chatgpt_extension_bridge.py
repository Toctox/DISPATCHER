"""Loopback-only, mutually authenticated, closed-operation WebSocket relay."""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import secrets
from http import HTTPStatus
from pathlib import Path

from .chatgpt_browser import ChatGPTError
from .chatgpt_extension_protocol import (
    CAPABILITY,
    MAX_MESSAGE,
    RECOVER,
    RECOVERY_RELEASE,
    VERSION,
    Reservation,
    check_settings,
    command,
    decode,
    fail,
    load_pairing,
    operation,
    proof,
    result,
)
from .config import load_local_settings
from .mutex import LocalMutex


def quiet_logger():
    # websockets debug logs can contain complete frames; never enable them here.
    logger = logging.getLogger("factory_dispatcher.private_bridge_transport")
    logger.disabled = True
    return logger


class Bridge:
    def __init__(self, settings):
        check_settings(settings)
        self.settings = settings
        self.pairing = load_pairing(settings)
        self.extension = None
        self.extension_error = "CHATGPT_EXTENSION_NOT_PAIRED"
        self.pending = None
        self.rpc_lock = asyncio.Lock()
        self.reservation = Reservation(
            settings.project_root / "state" / "chatgpt-bridge" / "reservation.json"
        )

    def origin_allowed(self, remote, path, origins):
        if not remote or remote[0] != "127.0.0.1":
            return False
        if path == "/extension":
            return origins == [f"chrome-extension://{self.settings.chatgpt_extension_id}"]
        return path == "/worker" and not origins

    def process_request(self, connection, request):
        if not self.origin_allowed(
            connection.remote_address, request.path, request.headers.get_all("Origin")
        ):
            return connection.respond(HTTPStatus.FORBIDDEN, "BRIDGE_ACCESS_REJECTED\n")
        return None

    async def authenticate(self, socket):
        role = "extension" if socket.request.path == "/extension" else "worker"
        nonce = secrets.token_hex(32)
        await socket.send(
            json.dumps(
                {
                    "protocolVersion": VERSION,
                    "type": "CHALLENGE",
                    "nonce": nonce,
                    "extensionId": self.settings.chatgpt_extension_id,
                }
            )
        )
        message = decode(await asyncio.wait_for(socket.recv(), timeout=10))
        keys = {"protocolVersion", "type", "proof"}
        if role == "extension":
            keys.add("capabilities")
            if message.get("capabilities") != [CAPABILITY]:
                fail("CHATGPT_EXTENSION_VERSION_MISMATCH")
        secret = self.pairing["extensionSecret" if role == "extension" else "controlSecret"]
        expected = proof(secret, role, nonce, self.settings.chatgpt_extension_id)
        if (
            set(message) != keys
            or message.get("type") != "AUTH"
            or not isinstance(message.get("proof"), str)
            or not hmac.compare_digest(message["proof"], expected)
        ):
            fail("CHATGPT_EXTENSION_NOT_PAIRED")
        await socket.send(
            json.dumps(
                {
                    "protocolVersion": VERSION,
                    "type": "READY",
                    "proof": proof(
                        secret, f"server-{role}", nonce, self.settings.chatgpt_extension_id
                    ),
                }
            )
        )
        return role

    async def relay(self, request):
        command(request)
        if operation(request) == RECOVERY_RELEASE:
            # A worker cannot turn caller-supplied assertions into a storage release.
            fail("CHATGPT_RECOVERY_REFUSED")
        # Don't queue a second dispatch behind an active operation.
        if self.rpc_lock.locked():
            fail("CHATGPT_TAB_BUSY")
        async with self.rpc_lock:
            if operation(request) == RECOVER:
                from .chatgpt_extension_recovery import recover_reservation

                await recover_reservation(self, request)
                return {
                    "protocolVersion": VERSION,
                    "type": "RESULT",
                    "requestId": request["requestId"],
                    "status": "OK",
                }
            extension = self.extension
            if extension is None:
                fail(self.extension_error)
            self.reservation.before(request)
            response = await self.exchange(request)
            if response["status"] == "OK":
                if operation(request) == "PRECHECK":
                    observed = response["binding"]
                    if observed["extensionId"] != self.settings.chatgpt_extension_id or (
                        "binding" in request and request["binding"] != observed
                    ):
                        fail("CHATGPT_BINDING_CHANGED")
                self.reservation.after(request)
            return response

    async def exchange(self, request):
        """One exchange, under rpc_lock. A lost ACK never clears the bridge barrier."""
        command(request)
        if self.extension is None:
            fail(self.extension_error)
        future = asyncio.get_running_loop().create_future()
        self.pending = (request, future)
        try:
            await self.extension.send(json.dumps(request, ensure_ascii=False))
            return result(await asyncio.wait_for(future, timeout=30), request)
        except ChatGPTError:
            raise
        except Exception:
            fail("CHATGPT_BRIDGE_UNAVAILABLE")
        finally:
            self.pending = None

    async def handle(self, socket):
        role = None
        try:
            role = await self.authenticate(socket)
            if role == "extension":
                if self.extension is not None:
                    fail("CHATGPT_TAB_BUSY")
                self.extension = socket
                self.extension_error = "CHATGPT_EXTENSION_NOT_PAIRED"
            async for raw in socket:
                message = decode(raw)
                if role == "extension":
                    if message == {"protocolVersion": VERSION, "type": "PING"}:
                        await socket.send(json.dumps({"protocolVersion": VERSION, "type": "PONG"}))
                    elif self.pending is not None:
                        request, future = self.pending
                        response = result(message, request)
                        if not future.done():
                            future.set_result(response)
                    else:
                        fail()
                else:
                    try:
                        response = await self.relay(message)
                    except ChatGPTError as exc:
                        response = {
                            "protocolVersion": VERSION,
                            "type": "RESULT",
                            "requestId": message.get("requestId", "invalid"),
                            "status": "ERROR",
                            "errorCode": exc.code,
                        }
                    await socket.send(json.dumps(response))
        except ChatGPTError as exc:
            if socket.request.path == "/extension" and self.extension in (None, socket):
                self.extension_error = exc.code
            await socket.close(code=1008, reason=exc.code)
        except Exception:
            await socket.close(code=1011, reason="CHATGPT_BRIDGE_UNAVAILABLE")
        finally:
            if self.extension is socket:
                self.extension = None
                if self.pending is not None:
                    future = self.pending[1]
                    if not future.done():
                        future.set_exception(ChatGPTError("CHATGPT_BRIDGE_UNAVAILABLE"))

    async def serve(self):
        from websockets.asyncio.server import serve

        async with serve(
            self.handle,
            "127.0.0.1",
            self.settings.chatgpt_extension_bridge_port,
            process_request=self.process_request,
            compression=None,
            max_size=MAX_MESSAGE,
            max_queue=4,
            open_timeout=5,
            close_timeout=1,
            logger=quiet_logger(),
        ):
            print('{"outcome":"BRIDGE_LISTENING","host":"127.0.0.1"}', flush=True)
            await asyncio.Future()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Start the loopback-only ChatGPT extension bridge."
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args(argv)
    try:
        settings = load_local_settings(args.config)
        bridge = Bridge(settings)
        with LocalMutex(settings.project_root / "state" / "chatgpt-bridge" / "server.lock"):
            asyncio.run(bridge.serve())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, ChatGPTError) else "CHATGPT_BRIDGE_UNAVAILABLE"
        print(json.dumps({"outcome": "BRIDGE_REJECTED", "errorCode": code}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
