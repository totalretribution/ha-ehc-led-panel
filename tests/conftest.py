"""Fixtures: a mocked ESPHome ble_adv_proxy board, and a set up light."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
import voluptuous as vol
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ehc_led_panel.const import CONF_GROUP, CONF_LINKS, CONF_PROXY, CONF_REMOTE_ID, DOMAIN, RAW_ADV_EVENT
from custom_components.ehc_led_panel.protocol import Press, build


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""
    return


@pytest.fixture(autouse=True)
def no_sleep() -> Generator[None]:
    """No real waits between sends."""

    async def _no_sleep(_: float) -> None:
        return

    with (
        mock.patch("custom_components.ehc_led_panel.device._sleep", _no_sleep),
        mock.patch("custom_components.ehc_led_panel.config_flow._sleep", _no_sleep),
    ):
        yield


class MockBoard:
    """An ESPHome board running ble_adv_proxy, as Home Assistant sees it."""

    def __init__(self, hass: HomeAssistant, name: str = "light-tx-01", can_send: bool = True) -> None:
        self.hass = hass
        self.name = name
        self.node = name.replace("-", "_")
        self.can_send = can_send
        self.setup_calls: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.device_id = ""
        self.sensor_id = ""

    def register_services(self) -> None:
        """Register the ESPHome services, with the types ESPHome gives them."""
        setup_schema = {
            vol.Required("ignored_duration"): vol.Coerce(float),
            vol.Required("ignored_cids"): [vol.Coerce(float)],
            vol.Required("ignored_macs"): [cv.string],
        }
        self.hass.services.async_register("esphome", f"{self.node}_setup_svc_v0", self._setup, vol.Schema(setup_schema))
        if self.can_send:
            adv_schema = {
                vol.Required(name): kind
                for name, kind in [
                    ("raw", cv.string),
                    ("duration", vol.Coerce(float)),
                    ("repeat", vol.Coerce(float)),
                    ("ignored_advs", [cv.string]),
                    ("ignored_duration", vol.Coerce(float)),
                    ("own_address", cv.string),
                    ("channel_map", vol.Coerce(float)),
                    ("interval", vol.Coerce(float)),
                ]
            }
            self.hass.services.async_register("esphome", f"{self.node}_adv_svc_v2", self._adv, vol.Schema(adv_schema))

    async def async_setup(self, services: bool = True) -> None:
        """Add the board's ESPHome config entry, device and name sensor."""
        if services:
            self.register_services()
        entry = MockConfigEntry(domain="esphome", title=self.name)
        entry.add_to_hass(self.hass)
        entry.runtime_data = SimpleNamespace(device_info=SimpleNamespace(name=self.name))
        device = dr.async_get(self.hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("esphome", self.node)})
        self.device_id = device.id
        ent = er.async_get(self.hass).async_get_or_create(
            "sensor", "esphome", f"{self.node}_name", suggested_object_id=f"{self.node}_ble_adv_proxy_name", device_id=device.id, config_entry=entry
        )
        self.sensor_id = ent.entity_id
        await self.async_set_available(True)

    async def async_set_available(self, available: bool) -> None:
        """Name sensor state, as when the board connects / disconnects."""
        self.hass.states.async_set(self.sensor_id, self.name if available else STATE_UNAVAILABLE)
        await self.hass.async_block_till_done()

    def _setup(self, call: ServiceCall) -> None:
        self.setup_calls.append(dict(call.data))

    def _adv(self, call: ServiceCall) -> None:
        self.sent.append(dict(call.data))

    def sent_presses(self) -> list[Press]:
        """Presses sent since the last call."""
        from custom_components.ehc_led_panel.protocol import parse  # noqa: PLC0415

        presses = [parse(bytes.fromhex(x["raw"])) for x in self.sent]
        self.sent.clear()
        return [p for p in presses if p is not None]

    async def async_hear(self, press: Press | bytes) -> None:
        """The board hears an advert and forwards it."""
        raw = build(press) if isinstance(press, Press) else press
        self.hass.bus.async_fire(RAW_ADV_EVENT, {"device_id": self.device_id, "raw": raw.hex(), "orig": "66:55:44:33:22:11"})
        await self.hass.async_block_till_done()


@pytest.fixture
async def board(hass: HomeAssistant) -> MockBoard:
    """A board able to send."""
    board = MockBoard(hass)
    await board.async_setup()
    return board


@pytest.fixture
async def light_entry(hass: HomeAssistant, board: MockBoard) -> AsyncGenerator[MockConfigEntry]:
    """A light linked to remote 1A05 group 1 and remote D604 group 1, sent through the board."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Desk",
        unique_id="1A05_1",
        data={
            CONF_LINKS: [{CONF_REMOTE_ID: 0x1A05, CONF_GROUP: 1}, {CONF_REMOTE_ID: 0xD604, CONF_GROUP: 1}],
            CONF_PROXY: board.name,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    board.setup_calls.clear()
    yield entry
