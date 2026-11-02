"""Boards and hub tests."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.ehc_led_panel import async_get_hub
from custom_components.ehc_led_panel.const import PROXY_IGNORED_DURATION_MS
from custom_components.ehc_led_panel.protocol import Command, Press, build

from .conftest import MockBoard


async def test_board_discovery(hass: HomeAssistant) -> None:
    """Boards are set up when available, dropped when not, set up again when back."""
    sender = MockBoard(hass)
    await sender.async_setup()
    listener = MockBoard(hass, "old-proxy", can_send=False)
    await listener.async_setup()
    hub = await async_get_hub(hass)
    assert {(p.name, p.can_send) for p in hub.proxies.proxies} == {("light-tx-01", True), ("old-proxy", False)}
    assert sender.setup_calls == [{"ignored_duration": PROXY_IGNORED_DURATION_MS, "ignored_cids": [], "ignored_macs": []}]

    await sender.async_set_available(False)
    assert hub.proxies.get("light-tx-01") is None
    await sender.async_set_available(True)
    assert hub.proxies.get("light-tx-01") is not None
    assert len(sender.setup_calls) == 2
    # Attribute-only change: no new setup
    hass.states.async_set(sender.sensor_id, sender.name, {"x": 1})
    await hass.async_block_till_done()
    assert len(sender.setup_calls) == 2

    with pytest.raises(HomeAssistantError, match="can't send"):
        await hub.async_send("old-proxy", Press(0x1A05, 1, Command.POWER, 1, 2), 500)
    with pytest.raises(HomeAssistantError, match="isn't available"):
        await hub.async_send("nope", Press(0x1A05, 1, Command.POWER, 1, 2), 500)


async def test_board_services_late(hass: HomeAssistant) -> None:
    """A board whose ESPHome services appear after its name sensor."""
    board = MockBoard(hass)
    hub = await async_get_hub(hass)
    await board.async_setup(services=False)
    assert hub.proxies.proxies == []
    board.register_services()
    await hass.async_block_till_done()
    assert [p.name for p in hub.proxies.proxies] == ["light-tx-01"]


async def test_send(hass: HomeAssistant, board: MockBoard) -> None:
    """A send uses adv_svc_v2 with the remotes' address, channel 37, 20ms."""
    hub = await async_get_hub(hass)
    press = Press(0x1A05, 1, Command.DIMMER, 0x10, 0x20)
    await hub.async_send(board.name, press, 500)
    raw = build(press).hex()
    assert board.sent == [
        {
            "raw": raw,
            "duration": 500,
            "repeat": 1,
            "ignored_advs": [raw],
            "ignored_duration": PROXY_IGNORED_DURATION_MS,
            "own_address": "66:55:44:33:22:11",
            "channel_map": 1,
            "interval": 20,
        }
    ]


async def test_listen_and_dedup(hass: HomeAssistant, board: MockBoard) -> None:
    """Listeners hear every copy of a press; anything else is ignored."""
    other = MockBoard(hass, "light-tx-02")
    await other.async_setup()
    hub = await async_get_hub(hass)
    heard: list[tuple[Press, str]] = []
    unsub = hub.listen(lambda press, board: heard.append((press, board)))
    press = Press(0x1A05, 0, Command.POWER, 0x30, 0x40)
    await board.async_hear(press)
    await other.async_hear(press)
    await board.async_hear(bytes.fromhex("1EFFD60502000600229FDA08C1627202645A00270102000000000000000005"))
    hass.bus.async_fire("esphome.ble_adv.raw_adv", {"device_id": "unknown", "raw": "zz"})
    hass.bus.async_fire("esphome.ble_adv.raw_adv", {"device_id": "unknown", "raw": build(press).hex()})
    await hass.async_block_till_done()
    unsub()
    await board.async_hear(press)
    assert heard == [(press, "light-tx-01"), (press, "light-tx-02"), (press, "unknown")]
