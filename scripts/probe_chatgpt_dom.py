from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, build_opener


def safe_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("unsafe endpoint")
    return f"http://127.0.0.1:{parsed.port}"


def normalize_ws(value: str, port: int) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "ws"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port != port
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/devtools/page/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("unsafe websocket")
    return urlunsplit(("ws", f"127.0.0.1:{port}", parsed.path, "", ""))


def read_targets(endpoint: str):
    opener = build_opener(ProxyHandler({}))
    with opener.open(endpoint + "/json", timeout=3) as response:
        raw = response.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise ValueError("target list too large")
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("invalid target list")
    return value


EXPRESSION = r"""
(() => {
  const usable = element => {
    if (!element || !element.isConnected || element.hidden || element.disabled ||
        element.getAttribute('aria-disabled') === 'true') return false;
    if (element.closest('[hidden],[aria-hidden="true"],[inert]')) return false;
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden' ||
        style.visibility === 'collapse') return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const selectors = [
    'textarea', '[contenteditable="true"]', 'button', '[role="button"]',
    '[data-testid]', 'a[aria-label]'
  ];
  const seen = new Set();
  const items = [];
  for (const selector of selectors) {
    for (const el of document.querySelectorAll(selector)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const tag = el.tagName.toLowerCase();
      const aria = el.getAttribute('aria-label');
      const testid = el.getAttribute('data-testid');
      const editable = el.getAttribute('contenteditable');
      const id = el.id || null;
      const role = el.getAttribute('role');
      const type = el.getAttribute('type');
      const name = el.getAttribute('name');
      const placeholder = (tag === 'textarea' || editable === 'true') ? el.getAttribute('placeholder') : null;
      const rect = el.getBoundingClientRect();
      items.push({
        tag, id, role, ariaLabel: aria, dataTestId: testid,
        contentEditable: editable, type, name, placeholder,
        usable: usable(el),
        rect: {x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height)}
      });
      if (items.length >= 120) break;
    }
    if (items.length >= 120) break;
  }
  return {
    origin: location.origin,
    pathname: location.pathname,
    readyState: document.readyState,
    count: items.length,
    controls: items
  };
})()
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only structural ChatGPT DOM probe via loopback CDP.")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        endpoint = safe_endpoint(str(config.get("chatGptCdpEndpoint") or "http://127.0.0.1:9222"))
        targets = read_targets(endpoint)
        pages = []
        for item in targets:
            if not isinstance(item, dict) or item.get("type") != "page":
                continue
            try:
                parsed = urlsplit(str(item.get("url", "")))
            except ValueError:
                continue
            if parsed.scheme == "https" and parsed.hostname == "chatgpt.com":
                pages.append(item)
        if len(pages) != 1:
            print(json.dumps({"kind": "CHATGPT_DOM_DIAGNOSTIC", "outcome": "CDP_TAB_COUNT", "count": len(pages)}))
            return 2
        port = urlsplit(endpoint).port
        websocket = normalize_ws(str(pages[0].get("webSocketDebuggerUrl", "")), port)
        from websockets.sync.client import connect
        with connect(websocket, origin=None, proxy=None, open_timeout=3, close_timeout=1, max_size=512 * 1024) as socket:
            request = {"id": 1, "method": "Runtime.evaluate", "params": {"expression": EXPRESSION, "returnByValue": True}}
            socket.send(json.dumps(request))
            while True:
                response = json.loads(socket.recv(timeout=5))
                if response.get("id") == 1:
                    break
        value = response.get("result", {}).get("result", {}).get("value")
        if not isinstance(value, dict):
            raise ValueError("missing evaluation value")
        print(json.dumps({"kind": "CHATGPT_DOM_DIAGNOSTIC", "outcome": "OK", **value}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"kind": "CHATGPT_DOM_DIAGNOSTIC", "outcome": "CDP_UNAVAILABLE", "errorType": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
