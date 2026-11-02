"""On / off light, with an assumed state."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import PanelConfigEntry
from .device import PanelLight
from .entity import PanelEntity
from .protocol import Command, Press

ATTR_COMMAND = "command"
ATTR_COUNT = "count"

COMMAND_SCHEMA = vol.Any(
    vol.All(vol.Lower, vol.In([c.name.lower() for c in Command])),
    vol.All(vol.Coerce(int), vol.Range(min=0, max=0xFF)),
)


async def async_setup_entry(hass: HomeAssistant, entry: PanelConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:  # noqa: ARG001
    """Set up the light and its send_command service."""
    async_add_entities([PanelLightEntity(entry.runtime_data)])
    entity_platform.async_get_current_platform().async_register_entity_service(
        "send_command",
        {vol.Required(ATTR_COMMAND): COMMAND_SCHEMA, vol.Optional(ATTR_COUNT, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=50))},
        "async_send_command",
    )


class PanelLightEntity(PanelEntity, LightEntity, RestoreEntity):
    """The light. Its state is assumed: the panel never reports it."""

    _attr_name = None
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}  # noqa: RUF012

    def __init__(self, light: PanelLight) -> None:
        super().__init__(light, "light")

    @property
    def is_on(self) -> bool | None:
        """Assumed state."""
        return self.light.is_on

    async def async_added_to_hass(self) -> None:
        """Restore the assumed state and follow changes."""
        await super().async_added_to_hass()
        if self.light.is_on is None and (last := await self.async_get_last_state()) is not None and last.state in (STATE_ON, STATE_OFF):
            self.light.is_on = last.state == STATE_ON
        self.async_on_remove(self.light.subscribe(self._on_change))

    @callback
    def _on_change(self, _: Press | None) -> None:
        self.async_write_ha_state()

    async def async_turn_on(self, **_: Any) -> None:  # noqa: ANN401
        """Turn on: power toggle if assumed off."""
        await self.light.async_set_on(True)

    async def async_turn_off(self, **_: Any) -> None:  # noqa: ANN401
        """Turn off: power toggle if assumed on."""
        await self.light.async_set_on(False)

    async def async_send_command(self, command: str | int, count: int = 1) -> None:
        """Send a remote command (name or number), count times like a held button."""
        await self.light.async_command(Command[command.upper()] if isinstance(command, str) else command, count)
