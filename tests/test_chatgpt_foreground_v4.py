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
        "_open_or_find_chatgpt_window",
        classmethod(lambda cls: (123, 456, True)),
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
        "_send_ctrl_n",
        classmethod(lambda cls: events.append(("ctrl_n",))),
    )
    surfaces = iter([(789, 654), (790, 655), (791, 656)])
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_wait_for_foreground_chatgpt_surface",
        classmethod(lambda cls: next(surfaces)),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_send_alt_number",
        classmethod(lambda cls, number: events.append(("alt", number))),
    )
    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_focus_composer_by_placeholder",
        classmethod(lambda cls, hwnd: events.append(("focus_placeholder", hwnd))),
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


def test_v6_runs_required_desktop_sequence_before_prompt(
    monkeypatch, tmp_path, chatgpt_request
):
    events = []
    launcher = GuardedForegroundChatGPTLauncher(tmp_path)
    _wire_success(monkeypatch, launcher, events)

    result = launcher.launch(_job(), _request(chatgpt_request), "BOOTSTRAP")

    assert result.launcher == "FOREGROUND_CHATGPT_DESKTOP_V6"
    ctrl_n = events.index(("ctrl_n",))
    wait_6 = events.index(("sleep", 6.0))
    alt_2 = events.index(("alt", 2))
    wait_3_first = events.index(("sleep", 3.0))
    alt_1 = events.index(("alt", 1))
    wait_3_second = events.index(("sleep", 3.0), wait_3_first + 1)
    focus = events.index(("focus_placeholder", 791))
    verify = events.index(("verify", "BOOTSTRAP"))
    enter = events.index(("enter",))

    assert ctrl_n < wait_6 < alt_2 < wait_3_first < alt_1 < wait_3_second < focus
    assert focus < events.index(("empty",)) < verify < enter

    phases = [item[1] for item in events if item[0] == "phase"]
    assert "APP_LAUNCHED" in phases
    assert "CTRL_N_DISPATCHED" in phases
    assert "ALT_2_DISPATCHED" in phases
    assert "ALT_1_DISPATCHED" in phases
    assert "DESKTOP_SEQUENCE_STABILIZED" in phases
    assert "COMPOSER_PLACEHOLDER_FOCUSED" in phases
    assert "EMPTY_COMPOSER_VERIFIED" in phases
    assert phases.index("BOOTSTRAP_VERIFIED") < phases.index("SEND_ATTEMPTED")


def test_v6_never_enters_when_placeholder_focus_fails(
    monkeypatch, tmp_path, chatgpt_request
):
    events = []
    launcher = GuardedForegroundChatGPTLauncher(tmp_path)
    _wire_success(monkeypatch, launcher, events)

    def fail_focus(cls, hwnd):
        events.append(("focus_failed", hwnd))
        raise ForegroundLaunchError("CHATGPT_DESKTOP_COMPOSER_PLACEHOLDER_NOT_FOUND")

    monkeypatch.setattr(
        GuardedForegroundChatGPTLauncher,
        "_focus_composer_by_placeholder",
        classmethod(fail_focus),
    )

    try:
        launcher.launch(_job(), _request(chatgpt_request), "BOOTSTRAP")
    except ForegroundLaunchError as exc:
        assert exc.code == "CHATGPT_DESKTOP_COMPOSER_PLACEHOLDER_NOT_FOUND"
    else:
        raise AssertionError("missing composer placeholder must abort before send")

    assert ("enter",) not in events
    assert ("ctrl_v",) not in events
    phases = [item[1] for item in events if item[0] == "phase"]
    assert "SEND_ATTEMPTED" not in phases
    assert phases[-1] == "FAILED"


def test_v6_never_enters_when_fresh_chat_proof_fails(
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