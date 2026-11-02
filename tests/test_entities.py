"""Light, buttons, event and send_command tests."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ASSUMED_STATE, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.ehc_led_panel.const import CONF_GROUP, CONF_LINKS, CONF_PROXY, CONF_REMOTE_ID, DOMAIN
from custom_components.ehc_led_panel.protocol import Command, Press

from .conftest import MockBoard

LIGHT = "light.desk"
EVENT = "event.desk_remote"


async def _light(hass: HomeAssistant, service: str, **data: object) -> None:
    await hass.services.async_call("light", service, {"entity_id": LIGHT, **data}, blocking=True)


async def test_light_on_off(hass: HomeAssistant, board: MockBoard, light_entry: MockConfigEntry) -> None:
    """On / off send a power toggle only when the assumed state differs."""
    state = hass.states.get(LIGHT)
    assert state.state == STATE_UNKNOWN
    assert state.attributes[ATTR_ASSUMED_STATE] is True
    await _light(hass, "turn_on")
    assert hass.states.get(LIGHT).state == STATE_ON
    assert [(p.remote_id, p.group, p.command) for p in board.sent_presses()] == [(0x1A05, 1, Command.POWER)]
    await _light(hass, "turn_on")
    assert board.sent_presses() == []
    await _light(hass, "turn_off")
    assert hass.states.get(LIGHT).state == STATE_OFF
    assert [p.command for p in board.sent_presses()] == [Command.POWER]


async def test_restore(hass: HomeAssistant, board: MockBoard) -> None:
    """The assumed state is restored after a restart."""
    mock_restore_cache(hass, [State(LIGHT, STATE_ON)])
    entry = MockConfigEntry(domain=DOMAIN, title="Desk", data={CONF_LINKS: [{CONF_REMOTE_ID: 0x1A05, CONF_GROUP: 1}], CONF_PROXY: board.name})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LIGHT).state == STATE_ON
    await _light(hass, "turn_on")
    assert board.sent_presses() == []
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_remote_presses(hass: HomeAssistant, board: MockBoard, light_entry: MockConfigEntry) -> None:
    """Remote presses that reach the light change its state and fire events; others are ignored."""
    await board.async_hear(Press(0x1A05, 0, Command.POWER, 1, 1))
    assert hass.states.get(LIGHT).state == STATE_ON
    event = hass.states.get(EVENT)
    assert event.attributes["event_type"] == "power"
    assert event.attributes["remote_id"] == "1A05"
    assert event.attributes["group"] == 0

    await board.async_hear(Press(0x1A05, 0, Command.POWER, 1, 1))  # same press again: ignored
    assert hass.states.get(LIGHT).state == STATE_ON
    await board.async_hear(Press(0x1A05, 2, Command.POWER, 2, 1))  # group 2: not this light
    await board.async_hear(Press(0xC802, 0, Command.POWER, 3, 1))  # other remote
    assert hass.states.get(LIGHT).state == STATE_ON

    await board.async_hear(Press(0xD604, 1, Command.GROUP1, 4, 1))  # second remote, group 1 button: toggles
    assert hass.states.get(LIGHT).state == STATE_OFF
    await board.async_hear(Press(0xD604, 1, Command.DIMMER, 5, 1))  # a step: event only
    assert hass.states.get(LIGHT).state == STATE_OFF
    assert hass.states.get(EVENT).attributes["event_type"] == "dimmer"
    await board.async_hear(Press(0x1A05, 1, 0x0B, 6, 1))
    assert hass.states.get(EVENT).attributes["event_type"] == "other"


async def test_own_send_heard_back(hass: HomeAssistant, board: MockBoard, light_entry: MockConfigEntry) -> None:
    """A send of ours heard by another board doesn't toggle the state again."""
    await _light(hass, "turn_on")
    [press] = board.sent_presses()
    await board.async_hear(press)
    assert hass.states.get(LIGHT).state == STATE_ON


@pytest.mark.parametrize(
    ("button", "command", "toggles"),
    [
        ("power", Command.POWER, True),
        ("night", Command.NIGHT, False),
        ("max", Command.MAX, False),
        ("brighter", Command.BRIGHTER, False),
        ("dimmer", Command.DIMMER, False),
        ("warmer", Command.WARMER, False),
        ("cooler", Command.COOLER, False),
    ],
)
async def test_buttons(hass: HomeAssistant, board: MockBoard, light_entry: MockConfigEntry, button: str, command: Command, toggles: bool) -> None:
    """Each button sends its command; power also flips the assumed state."""
    await _light(hass, "turn_off")  # unknown -> off
    board.sent_presses()
    await hass.services.async_call("button", "press", {"entity_id": f"button.desk_{button}"}, blocking=True)
    assert [p.command for p in board.sent_presses()] == [command]
    assert hass.states.get(LIGHT).state == (STATE_ON if toggles else STATE_OFF)


async def test_send_command(hass: HomeAssistant, board: MockBoard, light_entry: MockConfigEntry) -> None:
    """send_command by name or number; a count is sent like a held button."""
    await hass.services.async_call(DOMAIN, "send_command", {"entity_id": LIGHT, "command": "Dimmer", "count": 3}, blocking=True)
    assert [x["duration"] for x in board.sent] == [210, 210, 500]
    presses = board.sent_presses()
    assert [p.command for p in presses] == [Command.DIMMER] * 3
    assert len({p.key for p in presses}) == 3
    await _light(hass, "turn_off")
    board.sent_presses()
    await hass.services.async_call(DOMAIN, "send_command", {"entity_id": LIGHT, "command": 9}, blocking=True)
    assert [p.command for p in board.sent_presses()] == [Command.GROUP1]
    assert hass.states.get(LIGHT).state == STATE_ON  # group 1 toggles a group 1 light
    await hass.services.async_call(DOMAIN, "send_command", {"entity_id": LIGHT, "command": "power", "count": 2}, blocking=True)
    assert hass.states.get(LIGHT).state == STATE_ON


async def test_board_unavailable(hass: HomeAssistant, board: MockBoard, light_entry: MockConfigEntry) -> None:
    """Sending through an unavailable board fails, and the state is unchanged."""
    await board.async_set_available(False)
    with pytest.raises(HomeAssistantError, match="isn't available"):
        await _light(hass, "turn_on")
    assert hass.states.get(LIGHT).state == STATE_UNKNOWN
