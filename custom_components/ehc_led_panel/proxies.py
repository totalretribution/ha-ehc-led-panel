"""ESPHome boards running ble_adv_proxy: discovery, receiving and sending adverts.

Each board has a text sensor 'sensor.<name>_ble_adv_proxy_name' and ESPHome services
'esphome.<node>_setup_svc_v0' and 'esphome.<node>_adv_svc_v2'. Once set up, it fires
'esphome.ble_adv.raw_adv' for each new advert it hears.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.const import EVENT_SERVICE_REGISTERED, EVENT_STATE_CHANGED, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import ADV_SVC, ESPHOME_DOMAIN, PROXY_IGNORED_DURATION_MS, PROXY_NAME_SENSOR_SUFFIX, RAW_ADV_EVENT, SETUP_SVC
from .protocol import ADV_INTERVAL_MS, CHANNEL_MAP, SENDER_ADDRESS

_LOGGER = logging.getLogger(__name__)

type RawCallback = Callable[[str, bytes], None]


@dataclass
class Proxy:
    """A ble_adv_proxy board."""

    name: str  # proxy name, as shown by its name sensor (e.g. 'light-tx-01')
    device_id: str | None  # Home Assistant device of the ESPHome board
    service_base: str  # ESPHome service prefix (e.g. 'light_tx_01')
    can_send: bool  # has adv_svc_v2, able to send from the remotes' address


def _is_name_sensor(entity_id: str) -> bool:
    return entity_id.startswith("sensor.") and entity_id.endswith(PROXY_NAME_SENSOR_SUFFIX)


class ProxyManager:
    """Track the available boards."""

    def __init__(self, hass: HomeAssistant, on_raw: RawCallback) -> None:
        self.hass = hass
        self._on_raw = on_raw
        self._proxies: dict[str, Proxy] = {}  # by name sensor entity_id
        self._unsubs: list[CALLBACK_TYPE] = []

    @property
    def proxies(self) -> list[Proxy]:
        """Available boards."""
        return list(self._proxies.values())

    def get(self, name: str) -> Proxy | None:
        """Available board by name."""
        return next((p for p in self._proxies.values() if p.name == name), None)

    async def async_start(self) -> None:
        """Find the boards and follow their availability."""
        self._unsubs.append(self.hass.bus.async_listen(RAW_ADV_EVENT, self._async_raw_event))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._async_state_changed, event_filter=self._name_sensor_filter))
        # ESPHome may register a board's services after its sensors are available
        self._unsubs.append(self.hass.bus.async_listen(EVENT_SERVICE_REGISTERED, self._async_service_registered, event_filter=self._service_filter))
        await self._async_rescan()

    async def _async_rescan(self) -> None:
        """Check the name sensors of boards not yet available, or not yet able to send."""
        for entity_id in [ent.entity_id for ent in er.async_get(self.hass).entities.values() if _is_name_sensor(ent.entity_id)]:
            if (proxy := self._proxies.get(entity_id)) is None or not proxy.can_send:
                await self._async_update(entity_id)

    def stop(self) -> None:
        """Stop listening."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._proxies.clear()

    @callback
    def _name_sensor_filter(self, event_data: EventStateChangedData) -> bool:
        return _is_name_sensor(event_data["entity_id"])

    @callback
    def _service_filter(self, event_data: dict[str, Any]) -> bool:
        return event_data.get("domain") == ESPHOME_DOMAIN and str(event_data.get("service", "")).endswith((SETUP_SVC, ADV_SVC))

    async def _async_service_registered(self, _: Event[dict[str, Any]]) -> None:
        await self._async_rescan()

    async def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is not None and new is not None and old.state == new.state and event.data["entity_id"] in self._proxies:
            return  # attributes only
        await self._async_update(event.data["entity_id"])

    def _service_base(self, ent: er.RegistryEntry | None, name: str) -> str:
        """ESPHome service prefix: the node name, as ESPHome builds service names."""
        if ent is not None and ent.config_entry_id is not None:
            entry = self.hass.config_entries.async_get_entry(ent.config_entry_id)
            device_info = getattr(getattr(entry, "runtime_data", None), "device_info", None)
            if device_info is not None and getattr(device_info, "name", None):
                name = device_info.name
        return name.replace("-", "_")

    async def _async_update(self, entity_id: str) -> None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            if (proxy := self._proxies.pop(entity_id, None)) is not None:
                _LOGGER.info(f"Board '{proxy.name}' unavailable")
            return
        ent = er.async_get(self.hass).async_get(entity_id)
        base = self._service_base(ent, state.state)
        services = self.hass.services.async_services_for_domain(ESPHOME_DOMAIN)
        if f"{base}_{SETUP_SVC}" not in services:
            _LOGGER.warning(f"Board '{state.state}': no service esphome.{base}_{SETUP_SVC}, ignored")
            return
        proxy = Proxy(state.state, ent.device_id if ent is not None else None, base, f"{base}_{ADV_SVC}" in services)
        if not proxy.can_send:
            _LOGGER.warning(f"Board '{proxy.name}' has no {ADV_SVC} service: it can listen but not send. Update its firmware.")
        try:
            await self.hass.services.async_call(
                ESPHOME_DOMAIN,
                f"{base}_{SETUP_SVC}",
                {"ignored_duration": PROXY_IGNORED_DURATION_MS, "ignored_cids": [], "ignored_macs": []},
                blocking=True,
            )
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.warning(f"Board '{proxy.name}' setup failed: {err}")
            return
        self._proxies[entity_id] = proxy
        _LOGGER.info(f"Board '{proxy.name}' available, {'sending and ' if proxy.can_send else ''}listening")

    async def _async_raw_event(self, event: Event[dict[str, Any]]) -> None:
        device_id = event.data.get("device_id")
        proxy = next((p for p in self._proxies.values() if p.device_id == device_id), None)
        try:
            raw = bytes.fromhex(event.data.get("raw", ""))
        except ValueError:
            return
        self._on_raw(proxy.name if proxy is not None else str(device_id), raw)

    async def async_send(self, proxy: Proxy, raw: bytes, duration_ms: int) -> None:
        """Advertise raw for duration_ms from the remotes' address, on channel 37."""
        if not proxy.can_send:
            raise HomeAssistantError(f"Board '{proxy.name}' can't send: its firmware has no {ADV_SVC} service")
        await self.hass.services.async_call(
            ESPHOME_DOMAIN,
            f"{proxy.service_base}_{ADV_SVC}",
            {
                "raw": raw.hex(),
                "duration": duration_ms,
                "repeat": 1,
                "ignored_advs": [raw.hex()],
                "ignored_duration": PROXY_IGNORED_DURATION_MS,
                "own_address": SENDER_ADDRESS,
                "channel_map": CHANNEL_MAP,
                "interval": ADV_INTERVAL_MS,
            },
            blocking=True,
        )
