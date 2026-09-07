from __future__ import annotations

from conftest import queue_row

from factory_dispatcher.launchers.chatgpt_foreground import ForegroundLaunchError
from factory_dispatcher.launchers.chatgpt_foreground_v2 import GuardedForegroundChatGPTLauncher
from factory_dispatcher.models import DispatchJob, DispatchRequest


def _job() -> DispatchJob:
    return DispatchJob.from_row(2, queue_row(agentId="QA"))


def _request(chatgpt_request) -> DispatchRequest:
    raw = dict(chatgpt_request)
    raw["agentId"] = "QA"
    return DispatchRequest(raw)


def _wire_success(monkeypatch, launcher, events):
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_find_chatgpt_window",
        classmethod(lambda cls: 123),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_window_pid",
        classmethod(lambda cls, hwnd: 456 if hwnd == 123 else 654),
    )
    monkeypatch.setattr(
        launcher,
        "_record_phase",
        lambda *args, **kwargs: events.append(("phase", args[3], args[1], args[2])),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_activate_and_maximize",
        classmethod(lambda cls, hwnd: events.append(("activate", hwnd))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_ensure_foreground_chatgpt",
        classmethod(lambda cls, hwnd, pid: events.append(("ensure", hwnd, pid))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_send_escape_once",
        classmethod(lambda cls: events.append(("escape",))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_send_ctrl_alt_n",
        classmethod(lambda cls: events.append(("ctrl_alt_n",))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_wait_for_foreground_chatgpt_surface",
        classmethod(lambda cls: (789, 654)),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_click_composer",
        classmethod(lambda cls, hwnd: events.append(("click", hwnd))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_verify_empty_composer",
        classmethod(lambda cls: events.append(("empty",))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_set_clipboard_text",
        classmethod(lambda cls, text: events.append(("clipboard", text))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_send_ctrl_v",
        classmethod(lambda cls: events.append(("ctrl_v",))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_verify_pasted_prompt",
        classmethod(lambda cls, text: events.append(("verify", text))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_send_right_once",
        classmethod(lambda cls: events.append(("right",))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_send_enter_once",
        classmethod(lambda cls: events.append(("enter",))),
    )
    monkeypatch.setattr(
        "factory_dispatcher.launchers.chatgpt_foreground_v2.time.sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )


def test_v4_forces_chat_rebinds_window_and_verifies_before_enter(
    monkeypatch, tmp_path, chatgpt_request
):
    events = []
    launcher = GuardedForegroundChatGPTLauncher(tmp_path)
    _wire_success(monkeypatch, launcher, events)

    result = launcher.launch(_job(), _request(chatgpt_request), "BOOTSTRAP")

    assert result.launcher == "FOREGROUND_CHATGPT_DESKTOP_V4"
    assert events.count(("ctrl_alt_n",)) == 1
    assert ("activate", 789) in events
    assert ("click", 789) in events
    assert events.index(("empty",)) < events.index(("verify", "BOOTSTRAP"))
    assert events.index(("verify", "BOOTSTRAP")) < events.index(("enter",))

    phases = [item[1] for item in events if item[0] == "phase"]
    assert "CHAT_NEW_SHORTCUT_REQUESTED" in phases
    assert "CHAT_SURFACE_REACQUIRED" in phases
    assert "EMPTY_COMPOSER_VERIFIED" in phases
    assert phases.index("BOOTSTRAP_VERIFIED") < phases.index("SEND_ATTEMPTED")


def test_v4_never_enters_when_fresh_chat_proof_fails(
    monkeypatch, tmp_path, chatgpt_request
):
    events = []
    launcher = GuardedForegroundChatGPTLauncher(tmp_path)
    _wire_success(monkeypatch, launcher, events)

    def fail_empty(cls):
        events.append(("empty_failed",))
        raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_NOT_EMPTY")

    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_verify_empty_composer",
        classmethod(fail_empty),
    )

    try:
        launcher.launch(_job(), _request(chatgpt_request), "BOOTSTRAP")
    except ForegroundLaunchError as exc:
        assert exc.code == "CHATGPT_DESKTOP_COMPOSER_NOT_EMPTY"
    else:
        raise AssertionError("non-empty composer must abort before send")

    assert ("enter",) not in events
    phases = [item[1] for item in events if item[0] == "phase"]
    assert "SEND_ATTEMPTED" not in phases
    assert phases[-1] == "FAILED"
