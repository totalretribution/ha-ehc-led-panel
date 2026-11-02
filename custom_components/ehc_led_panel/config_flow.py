"""Add a light by pressing its remote; options to change the sending board and the linked remotes."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import SelectOptionDict, SelectSelector, SelectSelectorConfig, SelectSelectorMode

from . import async_get_hub
from .const import CONF_GROUP, CONF_LINKS, CONF_PROXY, CONF_REMOTE_ID, DOMAIN
from .device import Link
from .hub import Hub
from .protocol import TAP_DURATION_MS, Command, Press, command_name, new_press

_LOGGER = logging.getLogger(__name__)

LISTEN_SECONDS = 20
AFTER_FIRST_PRESS_SECONDS = 3  # keep listening a little, to hear other boards and a second press
TEST_GAP_SECONDS = 1.5
CONF_NAME = "name"
CONF_REMOVE = "remove"

_sleep = asyncio.sleep  # replaced in tests


@dataclass
class _Heard:
    """What was heard from one remote while listening."""

    remote_id: int
    groups: set[int] = field(default_factory=set)
    commands: list[str] = field(default_factory=list)
    boards: Counter[str] = field(default_factory=Counter)

    def label(self) -> str:
        groups = ", ".join(str(g) for g in sorted(self.groups))
        boards = ", ".join(f"{b} ({n}x)" for b, n in self.boards.most_common())
        return f"{self.remote_id:04X}: {', '.join(dict.fromkeys(self.commands))} - group {groups} - heard by {boards}"


async def _async_listen(hub: Hub, heard: dict[int, _Heard]) -> None:
    """Collect remote presses until shortly after the first one, or LISTEN_SECONDS."""
    first = asyncio.Event()

    @callback
    def _on_press(press: Press, board: str) -> None:
        item = heard.setdefault(press.remote_id, _Heard(press.remote_id))
        item.groups.add(press.group)
        item.commands.append(command_name(press.command))
        item.boards[board] += 1
        first.set()

    unsub = hub.listen(_on_press)
    try:
        try:
            await asyncio.wait_for(first.wait(), LISTEN_SECONDS)
        except TimeoutError:
            return
        await _sleep(AFTER_FIRST_PRESS_SECONDS)
    finally:
        unsub()


def _pick_schema(heard: dict[int, _Heard]) -> vol.Schema:
    """Choose a remote heard, and the group the light is paired to on it."""
    first = next(iter(heard.values()))
    group = next((g for g in sorted(first.groups) if g in (1, 2)), 1)
    return vol.Schema(
        {
            vol.Required(CONF_REMOTE_ID, default=f"{first.remote_id:04X}"): SelectSelector(
                SelectSelectorConfig(options=[SelectOptionDict(value=f"{h.remote_id:04X}", label=h.label()) for h in heard.values()])
            ),
            vol.Required(CONF_GROUP, default=str(group)): SelectSelector(
                SelectSelectorConfig(options=["1", "2"], mode=SelectSelectorMode.LIST, translation_key="group")
            ),
        }
    )


def _sending_boards(hub: Hub) -> list[str]:
    return [p.name for p in hub.proxies.proxies if p.can_send]


def _best_board(hub: Hub, heard: _Heard | None) -> str | None:
    """Pick the sending board that heard the remote most often, else the first sending board."""
    boards = _sending_boards(hub)
    if heard is not None:
        for board, _ in heard.boards.most_common():
            if board in boards:
                return board
    return boards[0] if boards else None


def _board_schema(boards: list[str], default: str | None) -> dict[Any, Any]:
    return {vol.Required(CONF_PROXY, default=default): SelectSelector(SelectSelectorConfig(options=boards))}


class _ListenFlowMixin:
    """Listening steps shared by the config and options flows."""

    hass: HomeAssistant
    _hub: Hub
    _heard: dict[int, _Heard]
    _listen_task: asyncio.Task | None

    def _listen_step(self, step_id: str, next_step_id: str) -> ConfigFlowResult:
        if self._listen_task is None:
            self._heard = {}
            self._listen_task = self.hass.async_create_task(_async_listen(self._hub, self._heard))
        if not self._listen_task.done():
            return self.async_show_progress(  # type: ignore[attr-defined]
                step_id=step_id, progress_action="listen", progress_task=self._listen_task, description_placeholders={"seconds": str(LISTEN_SECONDS)}
            )
        self._listen_task = None
        return self.async_show_progress_done(next_step_id=next_step_id if self._heard else "nothing_heard")  # type: ignore[attr-defined]


class PanelConfigFlow(_ListenFlowMixin, ConfigFlow, domain=DOMAIN):
    """Add a light."""

    VERSION = 1

    def __init__(self) -> None:
        self._heard = {}
        self._listen_task = None
        self._link: Link | None = None
        self._board: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PanelOptionsFlow:  # noqa: ARG004
        """Options."""
        return PanelOptionsFlow()

    async def async_step_user(self, _: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start: needs a board able to send."""
        self._hub = await async_get_hub(self.hass)
        if not _sending_boards(self._hub):
            return self.async_abort(reason="no_boards")
        return await self.async_step_listen()

    async def async_step_listen(self, _: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Listen for the remote."""
        return self._listen_step("listen", "pick")

    async def async_step_nothing_heard(self, _: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Nothing heard: retry."""
        return self.async_show_menu(step_id="nothing_heard", menu_options=["listen"])

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the remote and group."""
        if user_input is None:
            return self.async_show_form(step_id="pick", data_schema=_pick_schema(self._heard))
        self._link = Link(int(user_input[CONF_REMOTE_ID], 16), int(user_input[CONF_GROUP]))
        await self.async_set_unique_id(f"{self._link.remote_id:04X}_{self._link.group}")
        self._abort_if_unique_id_configured()
        self._board = _best_board(self._hub, self._heard.get(self._link.remote_id))
        if self._board is None:
            return self.async_abort(reason="no_boards")
        return await self.async_step_test()

    async def _async_test(self, link: Link, board: str) -> None:
        try:
            for i in range(2):
                if i:
                    await _sleep(TEST_GAP_SECONDS)
                await self._hub.async_send(board, new_press(link.remote_id, link.group, Command.POWER), TAP_DURATION_MS)
        except HomeAssistantError as err:
            _LOGGER.warning(f"Test send failed: {err}")

    async def async_step_test(self, _: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Toggle the light twice, and ask if it did."""
        assert self._link is not None  # noqa: S101
        assert self._board is not None  # noqa: S101
        self.hass.async_create_task(self._async_test(self._link, self._board))
        return self.async_show_menu(
            step_id="test",
            menu_options=["name", "test", "other_group", "listen"],
            description_placeholders={"remote": f"{self._link.remote_id:04X}", "group": str(self._link.group), "board": self._board},
        )

    async def async_step_other_group(self, _: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Try the other group."""
        assert self._link is not None  # noqa: S101
        self._link = Link(self._link.remote_id, 3 - self._link.group)
        await self.async_set_unique_id(f"{self._link.remote_id:04X}_{self._link.group}")
        self._abort_if_unique_id_configured()
        return await self.async_step_test()

    async def async_step_name(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Name the light and choose the board that sends to it."""
        assert self._link is not None  # noqa: S101
        if user_input is None:
            schema = {
                vol.Required(CONF_NAME, default=f"LED panel {self._link.remote_id:04X}"): str,
                **_board_schema(_sending_boards(self._hub), self._board),
            }
            return self.async_show_form(step_id="name", data_schema=vol.Schema(schema))
        return self.async_create_entry(title=user_input[CONF_NAME], data={CONF_LINKS: [self._link.as_dict()], CONF_PROXY: user_input[CONF_PROXY]})


class PanelOptionsFlow(_ListenFlowMixin, OptionsFlow):
    """Change the sending board, add or remove linked remotes."""

    def __init__(self) -> None:
        self._heard = {}
        self._listen_task = None

    async def async_step_init(self, _: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Menu."""
        self._hub = await async_get_hub(self.hass)
        return self.async_show_menu(step_id="init", menu_options=["board", "listen", "remove"])

    def _save(self, **changes: Any) -> ConfigFlowResult:  # noqa: ANN401
        self.hass.config_entries.async_update_entry(self.config_entry, data={**self.config_entry.data, **changes})
        return self.async_create_entry(data={})

    async def async_step_board(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the sending board."""
        if user_input is None:
            boards = _sending_boards(self._hub)
            current = self.config_entry.data[CONF_PROXY]
            schema = _board_schema(boards if current in boards else [current, *boards], current)
            return self.async_show_form(step_id="board", data_schema=vol.Schema(schema))
        return self._save(**{CONF_PROXY: user_input[CONF_PROXY]})

    async def async_step_listen(self, _: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Listen for another remote linked to the light."""
        return self._listen_step("listen", "pick")

    async def async_step_nothing_heard(self, _: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Nothing heard: retry."""
        return self.async_show_menu(step_id="nothing_heard", menu_options=["listen"])

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the remote and group to add."""
        if user_input is None:
            return self.async_show_form(step_id="pick", data_schema=_pick_schema(self._heard))
        link = Link(int(user_input[CONF_REMOTE_ID], 16), int(user_input[CONF_GROUP]))
        links = [Link.from_dict(x) for x in self.config_entry.data[CONF_LINKS]]
        if link in links:
            return self.async_abort(reason="already_linked")
        return self._save(**{CONF_LINKS: [x.as_dict() for x in [*links, link]]})

    async def async_step_remove(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Remove linked remotes, all but the first (used to send)."""
        links = [Link.from_dict(x) for x in self.config_entry.data[CONF_LINKS]]
        if len(links) < 2:
            return self.async_abort(reason="nothing_to_remove")
        if user_input is None:
            options = [SelectOptionDict(value=str(i), label=str(link)) for i, link in enumerate(links) if i > 0]
            schema = {vol.Required(CONF_REMOVE): SelectSelector(SelectSelectorConfig(options=options, multiple=True))}
            return self.async_show_form(step_id="remove", data_schema=vol.Schema(schema))
        removed = {int(i) for i in user_input[CONF_REMOVE]}
        return self._save(**{CONF_LINKS: [link.as_dict() for i, link in enumerate(links) if i not in removed]})
