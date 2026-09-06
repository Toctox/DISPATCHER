from __future__ import annotations

import json
from contextlib import ExitStack

from test_chatgpt_extension_recovery import recovery_case as recovery_case

from factory_dispatcher import chatgpt_extension_recovery as recovery
from factory_dispatcher.chatgpt_extension_protocol import RECOVERY_RELEASE, durable_write
from factory_dispatcher.receipts import read_json_object


def test_binding_changed_after_preparing_tab_is_exact_pre_send_case(recovery_case):
    case = recovery_case
    status = case.status()
    status["errorCode"] = "CHATGPT_BINDING_CHANGED"
    durable_write(case.directory / "status.json", status)

    entries = [
        json.loads(line)
        for line in (case.directory / "browser.log").read_text(encoding="utf-8").splitlines()
    ]
    entries[-1]["errorCode"] = "CHATGPT_BINDING_CHANGED"
    (case.directory / "browser.log").write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n",
        encoding="utf-8",
    )

    reservation = read_json_object(case.bridge.reservation.path)
    with ExitStack() as stack:
        evidence = recovery.LocalEvidence(
            case.settings, case.job.dispatch_id, case.job.attempt_id, stack
        )
        context, release, _ = evidence.verify(reservation)

    assert context.dispatchId == case.job.dispatch_id
    assert context.attemptId == case.job.attempt_id
    assert reservation["phase"] == "NEW_CHAT_ATTEMPTED"
    assert release["control"] == RECOVERY_RELEASE
