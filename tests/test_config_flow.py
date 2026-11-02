"""Config and options flow tests."""

from unittest import mock

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ehc_led_panel.const import CONF_GROUP, CONF_LINKS, CONF_PROXY, CONF_REMOTE_ID, DOMAIN
from custom_components.ehc_led_panel.hub import Hub
from custom_components.ehc_led_panel.protocol import Command, Press

from .conftest import MockBoard


async def _listen(hass: HomeAssistant, result: FlowResult, board: MockBoard | None, *presses: Press) -> FlowResult:
    """In a listening step: the board hears presses, then the flow moves on."""
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["progress_action"] == "listen"
    for press in presses:
        assert board is not None
        await board.async_hear(press)
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(result["flow_id"]) if result.get("handler") == DOMAIN else result


async def test_no_boards(hass: HomeAssistant) -> None:
    """No board able to send."""
    listener = MockBoard(hass, can_send=False)
    await listener.async_setup()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_boards"


async def test_add_light(hass: HomeAssistant, board: MockBoard) -> None:
    """Listen, pick, test, other group, name."""
    other = MockBoard(hass, "light-tx-02")
    await other.async_setup()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _listen(hass, result, other, Press(0x1A05, 0, Command.POWER, 1, 1), Press(0x1A05, 1, Command.GROUP1, 2, 1))
    await board.async_hear(Press(0x1A05, 1, Command.GROUP1, 2, 1))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick"
    schema = {str(k): k.default() for k in result["data_schema"].schema}
    assert schema == {CONF_REMOTE_ID: "1A05", CONF_GROUP: "1"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REMOTE_ID: "1A05", CONF_GROUP: "1"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "test"
    assert result["description_placeholders"] == {"remote": "1A05", "group": "1", "board": "light-tx-02"}  # heard it first
    assert [(p.remote_id, p.group, p.command) for p in other.sent_presses()] == [(0x1A05, 1, Command.POWER)] * 2

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "other_group"})
    await hass.async_block_till_done()
    assert result["description_placeholders"]["group"] == "2"
    assert [p.group for p in other.sent_presses()] == [2, 2]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "other_group"})
    await hass.async_block_till_done()
    other.sent_presses()

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "name"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Desk", CONF_PROXY: board.name})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Desk"
    assert result["data"] == {CONF_LINKS: [{CONF_REMOTE_ID: 0x1A05, CONF_GROUP: 1}], CONF_PROXY: board.name}
    await hass.async_block_till_done()
    assert hass.states.get("light.desk") is not None

    # Same remote and group again
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _listen(hass, result, board, Press(0x1A05, 0, Command.POWER, 3, 1))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REMOTE_ID: "1A05", CONF_GROUP: "1"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_nothing_heard(hass: HomeAssistant, board: MockBoard) -> None:
    """Nothing heard, then listen again."""
    with mock.patch("custom_components.ehc_led_panel.config_flow.LISTEN_SECONDS", 0.01):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await _listen(hass, result, None)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "nothing_heard"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "listen"})
    result = await _listen(hass, result, board, Press(0xC802, 2, Command.DIMMER, 1, 1))
    assert {str(k): k.default() for k in result["data_schema"].schema}[CONF_GROUP] == "2"


async def test_test_send_fails(hass: HomeAssistant, board: MockBoard) -> None:
    """The test send fails if the board went away: the flow carries on."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _listen(hass, result, board, Press(0x1A05, 0, Command.POWER, 1, 1))
    with mock.patch.object(Hub, "async_send", side_effect=HomeAssistantError("gone")):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REMOTE_ID: "1A05", CONF_GROUP: "1"})
        await hass.async_block_till_done()
    assert result["step_id"] == "test"
    assert board.sent == []
    await board.async_set_available(False)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "listen"})
    result = await _listen(hass, result, board, Press(0x1A05, 0, Command.POWER, 2, 1))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_REMOTE_ID: "1A05", CONF_GROUP: "1"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_boards"


async def _options(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> FlowResult:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})


async def test_options_board(hass: HomeAssistant, board: MockBoard, light_entry: MockConfigEntry) -> None:
    """Change the sending board."""
    other = MockBoard(hass, "light-tx-02")
    await other.async_setup()
    result = await _options(hass, light_entry, "board")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_PROXY: "light-tx-02"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert light_entry.data[CONF_PROXY] == "light-tx-02"
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.desk"}, blocking=True)
    assert len(other.sent) == 1
    assert board.sent == []

    await board.async_set_available(False)
    await other.async_set_available(False)
    result = await _options(hass, light_entry, "board")
    assert result["type"] is FlowResultType.FORM  # the current board is still offered


async def test_options_remotes(hass: HomeAssistant, board: MockBoard, light_entry: MockConfigEntry) -> None:
    """Link another remote, link it again, remove remotes."""
    result = await _options(hass, light_entry, "listen")
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await board.async_hear(Press(0xC802, 1, Command.WARMER, 1, 1))
    result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["step_id"] == "pick"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_REMOTE_ID: "C802", CONF_GROUP: "1"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [x[CONF_REMOTE_ID] for x in light_entry.data[CONF_LINKS]] == [0x1A05, 0xD604, 0xC802]
    await board.async_hear(Press(0xC802, 0, Command.POWER, 2, 1))
    assert hass.states.get("light.desk").state == "on"

    result = await _options(hass, light_entry, "listen")
    await board.async_hear(Press(0xC802, 1, Command.WARMER, 3, 1))
    result = await hass.config_entries.options.async_configure(result["flow_id"])
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_REMOTE_ID: "C802", CONF_GROUP: "1"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_linked"

    result = await _options(hass, light_entry, "remove")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"remove": ["1", "2"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert light_entry.data[CONF_LINKS] == [{CONF_REMOTE_ID: 0x1A05, CONF_GROUP: 1}]
    result = await _options(hass, light_entry, "remove")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "nothing_to_remove"

    with mock.patch("custom_components.ehc_led_panel.config_flow.LISTEN_SECONDS", 0.01):
        result = await _options(hass, light_entry, "listen")
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_configure(result["flow_id"])
    assert result["step_id"] == "nothing_heard"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "listen"})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
