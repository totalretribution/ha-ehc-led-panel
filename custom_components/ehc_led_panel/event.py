"""Remote presses affecting the light."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PanelConfigEntry
from .device import PanelLight
from .entity import PanelEntity
from .protocol import Command, Press, command_name

EVENT_OTHER = "other"
EVENT_TYPES = [command_name(c) for c in Command] + [EVENT_OTHER]


async def async_setup_entry(hass: HomeAssistant, entry: PanelConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:  # noqa: ARG001
    """Set up the remote event."""
    async_add_entities([PanelRemoteEvent(entry.runtime_data)])


class PanelRemoteEvent(PanelEntity, EventEntity):
    """Fires on each remote press affecting the light."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = EVENT_TYPES

    def __init__(self, light: PanelLight) -> None:
        super().__init__(light, "remote")

    async def async_added_to_hass(self) -> None:
        """Follow remote presses."""
        await super().async_added_to_hass()
        self.async_on_remove(self.light.subscribe(self._on_press))

    @callback
    def _on_press(self, press: Press | None) -> None:
        if press is None:
            return
        name = command_name(press.command)
        self._trigger_event(
            name if name in EVENT_TYPES else EVENT_OTHER,
            {"remote_id": f"{press.remote_id:04X}", "group": press.group, "command": press.command},
        )
        self.async_write_ha_state()
