"""Runtime entry point for the resilient extension bridge.

This keeps the evidence-gated recovery contract unchanged except for the one
pre-send transport case where the bridge write-ahead record exists at
NEW_CHAT_ATTEMPTED but the worker proves SEND was never attempted.
"""

from . import chatgpt_extension_recovery as recovery
from .chatgpt_extension_bridge import main
from .chatgpt_extension_protocol import RECOVERY_RELEASE

# Transport loss before NEW_CHAT reaches/acknowledges the extension is recoverable
# only through the existing evidence-gated recovery path. All queue, receipt,
# status, lease, reservation and sendAttempted checks remain mandatory.
recovery.PRE_SEND_RECOVERY.setdefault(
    "CHATGPT_BRIDGE_UNAVAILABLE",
    ("NEW_CHAT_ATTEMPTED", RECOVERY_RELEASE),
)


if __name__ == "__main__":
    raise SystemExit(main())
