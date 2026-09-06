"""Legacy persistent-context setup is disabled; never launch another browser."""

from __future__ import annotations

import json


def main(argv=None) -> int:
    print(
        json.dumps(
            {
                "outcome": "CHATGPT_LEGACY_PROFILE_DISABLED",
                "use": "python -m factory_dispatcher.chatgpt_browser_start --config .\\config.json",
            }
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
