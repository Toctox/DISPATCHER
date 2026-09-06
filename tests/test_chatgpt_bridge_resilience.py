from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from factory_dispatcher.chatgpt_browser import ChatGPTError
from factory_dispatcher.chatgpt_extension_bridge import Bridge
from factory_dispatcher.chatgpt_extension_pair import create_pairing


@pytest.fixture
def bridge_settings(local_settings):
    return replace(
        local_settings,
        chatgpt_browser_mode="EXTENSION_BRIDGE",
        chatgpt_extension_bridge_enabled=True,
        chatgpt_extension_id="a" * 32,
        chatgpt_extension_bridge_host="127.0.0.1",
        chatgpt_extension_bridge_port=8765,
    )


def test_wait_for_extension_accepts_short_reconnect_before_new_rpc(bridge_settings):
    create_pairing(bridge_settings)
    bridge = Bridge(bridge_settings)

    async def scenario():
        async def reconnect():
            await asyncio.sleep(0.05)
            bridge.extension = object()

        task = asyncio.create_task(reconnect())
        await bridge.wait_for_extension(timeout=0.5)
        await task
        assert bridge.extension is not None

    asyncio.run(scenario())


def test_wait_for_extension_still_fails_closed_after_deadline(bridge_settings):
    create_pairing(bridge_settings)
    bridge = Bridge(bridge_settings)

    async def scenario():
        with pytest.raises(ChatGPTError, match="EXTENSION_NOT_PAIRED"):
            await bridge.wait_for_extension(timeout=0.05)

    asyncio.run(scenario())
