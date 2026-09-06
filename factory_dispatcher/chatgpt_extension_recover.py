"""Explicit single-attempt recovery CLI. Never launches Chrome or retries anything."""

import argparse
import json
from pathlib import Path

from .chatgpt_browser import ChatGPTError
from .chatgpt_extension_client import BridgeClient
from .chatgpt_extension_protocol import RECOVER, recovery_ids
from .chatgpt_extension_recovery import RecoveryAudit
from .config import load_local_settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dispatch-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args(argv)
    audit = None
    requested = False
    try:
        recovery_ids(args.dispatch_id, args.attempt_id)
        settings = load_local_settings(args.config)
        audit = RecoveryAudit(settings, args.dispatch_id, args.attempt_id)
        audit.record("RECOVERY_REQUESTED")
        client = BridgeClient(settings)
        requested = True
        # No release-on-timeout, reconnect, auto-retry, or receipt polling.
        # The bridge owns validation/exclusion and uses bounded network I/O.
        client.call(RECOVER, timeout=None, dispatchId=args.dispatch_id, attemptId=args.attempt_id)
        audit.record("RECOVERY_REQUEST_ACKNOWLEDGED")
        print(json.dumps({"outcome": "RELEASED_PRE_SEND"}))
        return 0
    except Exception as exc:
        outcome = "RECOVERY_UNCERTAIN" if requested else "RECOVERY_REFUSED"
        if isinstance(exc, ChatGPTError) and exc.code == "CHATGPT_RECOVERY_REFUSED":
            outcome = "RECOVERY_REFUSED"
        if audit is not None:
            try:
                audit.record(outcome)
            except Exception:
                pass
        print(json.dumps({"outcome": outcome}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
