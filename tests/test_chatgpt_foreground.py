from __future__ import annotations

from conftest import queue_row

from factory_dispatcher.launchers.chatgpt_foreground import (
    ForegroundChatGPTLauncher,
    ForegroundLaunchError,
)
from factory_dispatcher.models import DispatchJob, DispatchRequest


def _job() -> DispatchJob:
    return DispatchJob.from_row(2, queue_row(agentId="REDTEAM"))


def _request(chatgpt_request) -> DispatchRequest:
    raw = dict(chatgpt_request)
    raw["agentId"] = "REDTEAM"
    return DispatchRequest(raw)


def _wire_success(monkeypatch, launcher, events):
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_find_chatgpt_window",
        classmethod(lambda cls: 123),
    )
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_window_pid",
        classmethod(lambda cls, hwnd: 456),
    )
    monkeypatch.setattr(launcher, "_record_phase", lambda *args, **kwargs: events.append(("phase", args[3])))
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_activate_and_maximize",
        classmethod(lambda cls, hwnd: events.append(("activate", hwnd))),
    )
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_ensure_foreground_chatgpt",
        classmethod(lambda cls, hwnd, pid: events.append(("ensure", hwnd, pid))),
    )
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_send_escape_once",
        classmethod(lambda cls: events.append(("escape",))),
    )
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_send_ctrl_n",
        classmethod(lambda cls: events.append(("ctrl_n",))),
    )
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_click_composer",
        classmethod(lambda cls, hwnd: events.append(("click", hwnd))),
    )
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_set_clipboard_text",
        classmethod(lambda cls, text: events.append(("clipboard", text))),
    )
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_send_ctrl_v",
        classmethod(lambda cls: events.append(("ctrl_v",))),
    )
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_verify_pasted_prompt",
        classmethod(lambda cls, text: events.append(("verify", text))),
    )
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_send_right_once",
        classmethod(lambda cls: events.append(("right",))),
    )
    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_send_enter_once",
        classmethod(lambda cls: events.append(("enter",))),
    )
    monkeypatch.setattr(
        "factory_dispatcher.launchers.chatgpt_foreground.time.sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )


def test_foreground_launcher_stabilizes_new_chat_and_verifies_before_enter(
    monkeypatch, tmp_path, chatgpt_request
):
    events = []
    launcher = ForegroundChatGPTLauncher(tmp_path)
    _wire_success(monkeypatch, launcher, events)

    result = launcher.launch(_job(), _request(chatgpt_request), "BOOTSTRAP")

    assert result.launcher == "FOREGROUND_CHATGPT_DESKTOP"
    assert [event for event in events if event[0] == "ctrl_n"] == [("ctrl_n",), ("ctrl_n",)]
    assert events.count(("sleep", launcher._NEW_CHAT_WAIT_SECONDS)) == 2
    assert events.index(("verify", "BOOTSTRAP")) < events.index(("enter",))
    phases = [event[1] for event in events if event[0] == "phase"]
    assert "NEW_CHAT_REQUESTED_1" in phases
    assert "NEW_CHAT_REQUESTED_2" in phases
    assert "NEW_CHAT_STABILIZED" in phases
    assert phases.index("BOOTSTRAP_VERIFIED") < phases.index("SEND_ATTEMPTED")


def test_foreground_launcher_never_enters_when_bootstrap_verification_fails(
    monkeypatch, tmp_path, chatgpt_request
):
    events = []
    launcher = ForegroundChatGPTLauncher(tmp_path)
    _wire_success(monkeypatch, launcher, events)

    def fail_verify(cls, text):
        events.append(("verify_failed", text))
        raise ForegroundLaunchError("CHATGPT_DESKTOP_BOOTSTRAP_VERIFY_FAILED")

    monkeypatch.setattr(
        ForegroundChatGPTLauncher,
        "_verify_pasted_prompt",
        classmethod(fail_verify),
    )

    try:
        launcher.launch(_job(), _request(chatgpt_request), "BOOTSTRAP")
    except ForegroundLaunchError as exc:
        assert exc.code == "CHATGPT_DESKTOP_BOOTSTRAP_VERIFY_FAILED"
    else:
        raise AssertionError("verification failure must abort the launch")

    assert ("enter",) not in events
    phases = [event[1] for event in events if event[0] == "phase"]
    assert "SEND_ATTEMPTED" not in phases
    assert phases[-1] == "FAILED"
