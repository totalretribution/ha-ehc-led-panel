"""A button per remote key."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PanelConfigEntry
from .device import PanelLight
from .entity import PanelEntity
from .protocol import Command

# Group 1 / 2 aren't buttons: sent from Home Assistant they only toggle the light, like power.
BUTTONS = [Command.POWER, Command.NIGHT, Command.MAX, Command.BRIGHTER, Command.DIMMER, Command.WARMER, Command.COOLER]


async def async_setup_entry(hass: HomeAssistant, entry: PanelConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:  # noqa: ARG001
    """Set up the buttons."""
    async_add_entities([PanelButton(entry.runtime_data, command) for command in BUTTONS])


class PanelButton(PanelEntity, ButtonEntity):
    """Sends one remote key press."""

    def __init__(self, light: PanelLight, command: Command) -> None:
        super().__init__(light, command.name.lower())
        self._command = command

    async def async_press(self) -> None:
        """Send the key press."""
        await self.light.async_command(self._command)
