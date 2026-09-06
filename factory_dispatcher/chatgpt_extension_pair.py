"""Explicit one-time pairing; never print either credential."""

import argparse
import json
import secrets
from pathlib import Path

from .chatgpt_extension_protocol import VERSION, check_settings, durable_write
from .config import load_local_settings


def create_pairing(settings):
    check_settings(settings, enabled=False)
    path = settings.chatgpt_pairing_file
    path.parent.mkdir(parents=True, exist_ok=True)
    durable_write(
        path,
        {
            "protocolVersion": VERSION,
            "extensionId": settings.chatgpt_extension_id,
            "extensionSecret": secrets.token_hex(32),
            "controlSecret": secrets.token_hex(32),
        },
        exclusive=True,
    )
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate local, one-time extension pairing.")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args(argv)
    try:
        path = create_pairing(load_local_settings(args.config))
    except FileExistsError:
        print('{"outcome":"PAIRING_EXISTS_NOT_OVERWRITTEN"}')
        return 2
    except Exception:
        print('{"outcome":"PAIRING_REJECTED"}')
        return 2
    print(
        json.dumps(
            {
                "outcome": "PAIRING_CREATED",
                "file": str(path),
                "instruction": (
                    "Copy ONLY extensionSecret to extension options; never share controlSecret."
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
