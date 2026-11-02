"""EHC LED panels (LED Driver 50W-2.4G-CCT, EHC_050S105DN_TP02), controlled through ESPHome ble_adv_proxy boards."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant

from .const import CONF_LINKS, CONF_PROXY, DOMAIN
from .device import Link, PanelLight
from .hub import Hub

PLATFORMS = [Platform.BUTTON, Platform.EVENT, Platform.LIGHT]

type PanelConfigEntry = ConfigEntry[PanelLight]


async def async_get_hub(hass: HomeAssistant) -> Hub:
    """Get the hub, creating and starting it on first use."""
    if (hub := hass.data.get(DOMAIN)) is None:
        hub = hass.data[DOMAIN] = Hub(hass)
        await hub.async_start()

        def _stop(_: Event) -> None:
            hub.stop()

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
    return hub


async def async_setup_entry(hass: HomeAssistant, entry: PanelConfigEntry) -> bool:
    """Set up a light."""
    hub = await async_get_hub(hass)
    links = [Link.from_dict(link) for link in entry.data[CONF_LINKS]]
    light = PanelLight(hub, entry.entry_id, entry.title, links, entry.data[CONF_PROXY])
    hub.add_light(light)
    entry.runtime_data = light
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: PanelConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PanelConfigEntry) -> bool:
    """Unload a light."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].remove_light(entry.runtime_data)
    return unloaded
