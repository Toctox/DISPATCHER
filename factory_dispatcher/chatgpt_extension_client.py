"""Worker-only client; the extension never receives the control credential."""

import argparse
import hmac
import json
import re
import uuid
from pathlib import Path

from .chatgpt_browser import ChatGPTError
from .chatgpt_extension_bridge import quiet_logger
from .chatgpt_extension_protocol import (
    CONTROLS,
    MAX_MESSAGE,
    VERSION,
    check_settings,
    command,
    decode,
    fail,
    load_pairing,
    proof,
    result,
    tab_lock,
)
from .config import load_local_settings


class BridgeClient:
    def __init__(self, settings):
        check_settings(settings)
        self.settings = settings
        self.pairing = load_pairing(settings)

    def call(self, action, *, timeout=35, **fields):
        request = command(
            {
                "protocolVersion": VERSION,
                "type": "CONTROL" if action in CONTROLS else "COMMAND",
                "requestId": str(uuid.uuid4()),
                "control" if action in CONTROLS else "action": action,
                **fields,
            }
        )
        try:
            from websockets.sync.client import connect

            with connect(
                f"ws://127.0.0.1:{self.settings.chatgpt_extension_bridge_port}/worker",
                origin=None,
                proxy=None,
                open_timeout=3,
                close_timeout=1,
                max_size=MAX_MESSAGE,
                compression=None,
                logger=quiet_logger(),
            ) as socket:
                challenge = decode(socket.recv(timeout=3))
                if (
                    set(challenge) != {"protocolVersion", "type", "nonce", "extensionId"}
                    or challenge["type"] != "CHALLENGE"
                    or challenge["extensionId"] != self.settings.chatgpt_extension_id
                    or not re.fullmatch(r"[0-9a-f]{64}", str(challenge["nonce"]))
                ):
                    fail()
                secret = self.pairing["controlSecret"]
                nonce = challenge["nonce"]
                extension_id = self.settings.chatgpt_extension_id
                await_proof = proof(secret, "worker", nonce, extension_id)
                socket.send(
                    json.dumps({"protocolVersion": VERSION, "type": "AUTH", "proof": await_proof})
                )
                ready = decode(socket.recv(timeout=3))
                if (
                    set(ready) != {"protocolVersion", "type", "proof"}
                    or ready["type"] != "READY"
                    or not isinstance(ready["proof"], str)
                    or not hmac.compare_digest(
                        ready["proof"], proof(secret, "server-worker", nonce, extension_id)
                    )
                ):
                    fail("CHATGPT_EXTENSION_NOT_PAIRED")
                socket.send(json.dumps(request, ensure_ascii=False))
                response = result(decode(socket.recv(timeout=timeout)), request)
                if response["status"] == "ERROR":
                    fail(response["errorCode"])
                return response
        except ChatGPTError:
            raise
        except Exception:
            fail("CHATGPT_BRIDGE_UNAVAILABLE")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only local extension preflight; no Google calls."
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args(argv)
    try:
        settings = load_local_settings(args.config)
        with tab_lock(settings):
            BridgeClient(settings).call("PRECHECK")
        print('{"outcome":"PREFLIGHT_OK"}')
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, ChatGPTError) else "CHATGPT_TAB_BUSY"
        print(json.dumps({"outcome": "PREFLIGHT_BLOCKED", "errorCode": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
