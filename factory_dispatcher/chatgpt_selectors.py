"""Closed allowlist of input/navigation controls, NOT a DOM/answer scraping interface.

These selectors are compatibility assumptions, not an official stable ChatGPT API.
Only the composer text that WE supplied is read, exclusively for equality checks.
"""

from __future__ import annotations

import re
import time

from .chatgpt_browser import ChatGPTError, is_chatgpt_page, require_chatgpt_root

# At most three alternatives per control. No arbitrary text or coordinate fallback.
SELECTORS = {
    "account": (
        ("testid", "profile-button"),
        ("testid", "user-menu-button"),
        (
            "button",
            r"^(Open profile menu|Open user menu|Abrir menu de perfil|Abrir menu do usuário)$",
        ),
    ),
    "login": (
        ("testid", "login-button"),
        ("button", r"^(Log in|Sign in|Entrar|Fazer login)$"),
        ("link", r"^(Log in|Sign in|Entrar|Fazer login)$"),
    ),
    "new_chat": (
        ("testid", "create-new-chat-button"),
        ("link", r"^(New chat|Novo chat)$"),
        ("button", r"^(New chat|Novo chat)$"),
    ),
    "composer": (
        ("css", '#prompt-textarea[contenteditable="true"]'),
        ("css", "textarea#prompt-textarea"),
        ("textbox", r"^(Message ChatGPT|Mensagem para o ChatGPT)$"),
    ),
    "send": (
        ("testid", "send-button"),
        ("button", r"^(Send prompt|Send message|Enviar prompt|Enviar mensagem)$"),
    ),
}


class ChatGPTControls:
    def __init__(
        self,
        page,
        *,
        binding=None,
        binding_probe=None,
        timeout_seconds=15,
        monotonic=time.monotonic,
    ):
        self.page = page
        self.binding = binding
        self.binding_probe = binding_probe
        self.timeout_seconds = timeout_seconds
        self.monotonic = monotonic
        self._composer = None
        self._prompt = None
        self._send = None
        self._send_attempted = False

    def _find(self, control):
        for kind, value in SELECTORS[control]:
            if kind == "testid":
                locator = self.page.get_by_test_id(value)
            elif kind == "css":
                locator = self.page.locator(value)
            else:
                locator = self.page.get_by_role(kind, name=re.compile(value, re.IGNORECASE))
            count = locator.count()
            if count > 20:
                raise ChatGPTError("CHATGPT_SELECTOR_AMBIGUOUS")
            visible = [locator.nth(i) for i in range(count) if locator.nth(i).is_visible()]
            if len(visible) > 1:
                raise ChatGPTError("CHATGPT_SELECTOR_AMBIGUOUS")
            if visible:
                return visible[0]
        return None

    def _require(self, control):
        deadline = self.monotonic() + self.timeout_seconds
        while True:
            match = self._find(control)
            if match is not None and (control not in {"send", "composer"} or match.is_enabled()):
                return match
            if self.monotonic() >= deadline:
                raise ChatGPTError("CHATGPT_SELECTOR_UNAVAILABLE")
            self.page.wait_for_timeout(250)

    def verify_binding(self, expected):
        if self.binding_probe is None:
            raise ChatGPTError("CHATGPT_CDP_BINDING_INVALID")
        self.binding_probe().require_same(expected)

    def preflight(self):
        if not is_chatgpt_page(self.page.url):
            raise ChatGPTError("CHATGPT_TAB_CHANGED")
        self.require_auth()
        new_chat = self._require("new_chat")
        composer = self._require("composer")
        try:
            if not composer.is_editable():
                raise ChatGPTError("CHATGPT_SELECTOR_UNAVAILABLE")
            # Actionability only; never create a chat in preflight.
            new_chat.click(trial=True, timeout=3000)
        except Exception as exc:
            raise ChatGPTError("CHATGPT_SELECTOR_UNAVAILABLE") from exc

    def require_auth(self):
        deadline = self.monotonic() + self.timeout_seconds
        while True:
            if self._find("login") is not None:
                raise ChatGPTError("CHATGPT_AUTH_REQUIRED")
            if self._find("account") is not None:
                return
            if self.monotonic() >= deadline:
                # Unknown UI is NOT evidence that login is missing or successful.
                raise ChatGPTError("CHATGPT_AUTH_UNVERIFIED")
            self.page.wait_for_timeout(250)

    def _composer_text(self):
        if self._composer.get_attribute("contenteditable") == "true":
            return self._composer.inner_text()
        return self._composer.input_value()

    def prepare(self, prompt: str):
        if self._prompt is not None or self._send_attempted:
            raise ChatGPTError("CHATGPT_SEND_ALREADY_ATTEMPTED")
        self.preflight()
        self._require("new_chat").click()
        self.page.wait_for_url("https://chatgpt.com/", timeout=10000)
        require_chatgpt_root(self.page.url)
        self._composer = self._require("composer")
        if self._composer_text() != "":
            raise ChatGPTError("CHATGPT_NEW_CHAT_NOT_EMPTY")
        self._composer.fill(prompt)
        if self._composer_text().encode("utf-8") != prompt.encode("utf-8"):
            raise ChatGPTError("CHATGPT_BOOTSTRAP_MISMATCH")
        self._prompt = prompt
        self._send = self._require("send")

    def send(self, *, timeout_ms=10000):
        if self._send_attempted or self._send is None:
            raise ChatGPTError("CHATGPT_SEND_ALREADY_ATTEMPTED")
        require_chatgpt_root(self.page.url)
        if self._composer_text() != self._prompt:
            raise ChatGPTError("CHATGPT_BOOTSTRAP_MISMATCH")
        self._send_attempted = True
        self._send.click(timeout=timeout_ms)
        # Deliberately no DOM access after this point, including success indicators.
