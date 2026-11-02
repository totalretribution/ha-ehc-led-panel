"""Base entity."""

from __future__ import annotations

from homeassistant.helpers.entity import Entity

from .device import PanelLight


class PanelEntity(Entity):
    """An entity of a light panel."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, light: PanelLight, key: str) -> None:
        self.light = light
        self._attr_unique_id = f"{light.unique_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = light.device_info
