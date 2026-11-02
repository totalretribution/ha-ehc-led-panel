"""Hub: one per Home Assistant. Routes remote presses to the lights and sends presses."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DEDUP_SECONDS
from .protocol import Press, build, parse
from .proxies import Proxy, ProxyManager

if TYPE_CHECKING:
    from .device import PanelLight

_LOGGER = logging.getLogger(__name__)

type PressListener = Callable[[Press, str], None]


class Hub:
    """Shared by all lights."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.proxies = ProxyManager(hass, self._on_raw)
        self._lights: list[PanelLight] = []
        self._listeners: list[PressListener] = []
        self._recent: dict[tuple[int, int, int], float] = {}

    async def async_start(self) -> None:
        """Start following the boards."""
        await self.proxies.async_start()

    def stop(self) -> None:
        """Stop."""
        self.proxies.stop()

    def add_light(self, light: PanelLight) -> None:
        """Route presses to this light."""
        self._lights.append(light)

    def remove_light(self, light: PanelLight) -> None:
        """Stop routing presses to this light."""
        self._lights.remove(light)

    @callback
    def listen(self, listener: PressListener) -> CALLBACK_TYPE:
        """Call listener(press, board name) for every press heard, including repeats from several boards."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _seen(self, press: Press) -> bool:
        """Return True if the press was already seen recently, and remember it."""
        now = time.monotonic()
        self._recent = {k: v for k, v in self._recent.items() if v > now}
        seen = press.key in self._recent
        self._recent[press.key] = now + DEDUP_SECONDS
        return seen

    @callback
    def _on_raw(self, proxy_name: str, raw: bytes) -> None:
        if (press := parse(raw)) is None:
            return
        for listener in list(self._listeners):
            listener(press, proxy_name)
        if self._seen(press):
            return
        _LOGGER.debug(f"Heard {press} via '{proxy_name}'")
        for light in self._lights:
            if light.affected_by(press):
                light.on_remote_press(press)

    def sending_proxy(self, name: str) -> Proxy:
        """Get the board to send with, or raise."""
        proxy = self.proxies.get(name)
        if proxy is None:
            raise HomeAssistantError(f"Board '{name}' isn't available")
        return proxy

    async def async_send(self, proxy_name: str, press: Press, duration_ms: int) -> None:
        """Send a press from a board. It won't be taken as a remote press if another board hears it."""
        proxy = self.sending_proxy(proxy_name)
        self._seen(press)
        _LOGGER.debug(f"Sending {press} via '{proxy_name}' for {duration_ms}ms")
        await self.proxies.async_send(proxy, build(press), duration_ms)
