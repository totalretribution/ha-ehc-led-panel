"""A light panel: its links to remotes, assumed on / off state and send queue."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_GROUP, CONF_REMOTE_ID, DOMAIN, SEND_GAP_SECONDS
from .hub import Hub
from .protocol import HOLD_STEP_MS, TAP_DURATION_MS, Command, Press, new_press

type PressCallback = Callable[[Press | None], None]

_sleep = asyncio.sleep  # replaced in tests


@dataclass(frozen=True)
class Link:
    """A remote the light is paired to, and the group it is paired to on that remote (1 or 2)."""

    remote_id: int
    group: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Link:
        """From config entry data."""
        return cls(int(data[CONF_REMOTE_ID]), int(data[CONF_GROUP]))

    def as_dict(self) -> dict[str, int]:
        """To config entry data."""
        return {CONF_REMOTE_ID: self.remote_id, CONF_GROUP: self.group}

    def __str__(self) -> str:
        return f"{self.remote_id:04X} group {self.group}"


class PanelLight:
    """One light panel."""

    def __init__(self, hub: Hub, unique_id: str, name: str, links: list[Link], proxy_name: str) -> None:
        self.hub = hub
        self.unique_id = unique_id
        self.name = name
        self.links = links
        self.proxy_name = proxy_name
        self.is_on: bool | None = None
        self._lock = asyncio.Lock()
        self._callbacks: list[PressCallback] = []

    @property
    def device_info(self) -> DeviceInfo:
        """Device shared by the light's entities."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            manufacturer="EHC",
            model="LED Driver 50W-2.4G-CCT",
            model_id="EHC_050S105DN_TP02",
            serial_number=", ".join(str(link) for link in self.links),
        )

    @callback
    def subscribe(self, cb: PressCallback) -> CALLBACK_TYPE:
        """Call cb(press) on a remote press affecting the light, cb(None) when the state changed otherwise."""
        self._callbacks.append(cb)
        return lambda: self._callbacks.remove(cb)

    def _notify(self, press: Press | None) -> None:
        for cb in list(self._callbacks):
            cb(press)

    def affected_by(self, press: Press) -> bool:
        """Lights react to presses with group 0 (all) or their own group."""
        return any(link.remote_id == press.remote_id and press.group in (0, link.group) for link in self.links)

    @staticmethod
    def _toggles(command: int, group: int) -> bool:
        """Power toggles; group 1 / 2 toggle the lights of that group."""
        return command == Command.POWER or (command == Command.GROUP1 and group == 1) or (command == Command.GROUP2 and group == 2)

    @callback
    def on_remote_press(self, press: Press) -> None:
        """Update the assumed state from a remote press affecting the light."""
        if self._toggles(press.command, press.group):
            self.is_on = not self.is_on
        self._notify(press)

    @property
    def _send_link(self) -> Link:
        return self.links[0]

    async def _async_send(self, command: int, count: int = 1) -> None:
        """Send presses; a count > 1 is sent like a held button. Lock held by the caller."""
        link = self._send_link
        for i in range(count):
            duration = TAP_DURATION_MS if i == count - 1 else HOLD_STEP_MS
            await self.hub.async_send(self.proxy_name, new_press(link.remote_id, link.group, command), duration)
            await _sleep(duration / 1000 + (SEND_GAP_SECONDS if i == count - 1 else 0))

    async def async_set_on(self, on: bool) -> None:
        """Turn on or off: a power toggle if the assumed state differs."""
        async with self._lock:
            if self.is_on is on:
                return
            await self._async_send(Command.POWER)
            self.is_on = on
            self._notify(None)

    async def async_toggle(self) -> None:
        """Power toggle, whatever the assumed state."""
        await self.async_command(Command.POWER)

    async def async_command(self, command: int, count: int = 1) -> None:
        """Send any command, count times like a held button."""
        async with self._lock:
            await self._async_send(command, count)
            if self._toggles(command, self._send_link.group) and count % 2 == 1:
                self.is_on = not self.is_on
                self._notify(None)
