"""Atomic station snapshots, availability, deduplication and persisted rain."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CHANNEL,
    CONF_SENSOR_ID,
    CONF_SIGNAL_TIMEOUT,
    DEFAULT_SIGNAL_TIMEOUT,
    DOMAIN,
)
from .protocol import Packet, TimingDecoder
from .rain import RainTracker
from .receiver import FrameSource

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StationState:
    """One consistent view shared by every entity."""

    packet: Packet | None = None
    rain_mm: float | None = None
    last_seen: datetime | None = None
    health: str = "waiting"


class WeatherCoordinator(DataUpdateCoordinator[StationState]):
    """A push coordinator; weather remains unavailable until a fresh packet."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry)
        self.entry = entry
        # Config-entry data changes before HA unloads the old coordinator during
        # reconfigure. Persist the old baseline with its ORIGINAL identity.
        self._station = [entry.data[CONF_SENSOR_ID], entry.data[CONF_CHANNEL]]
        self.data = StationState()
        self.decoder = TimingDecoder()
        self.rain = RainTracker()
        self.connected = False
        self.valid = 0
        self.duplicates = 0
        self.filtered = 0
        self.last_timing_count = 0
        self.last_duration_us = 0
        self._seen: dict[bytes, float] = {}
        self._unsubscribe = None
        self._expiry = None
        self._closed = False
        self._store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.timeout = entry.options.get(CONF_SIGNAL_TIMEOUT, DEFAULT_SIGNAL_TIMEOUT)

    @property
    def weather_available(self) -> bool:
        """Receiver connectivity alone is not a fresh weather observation."""
        return self.data.health == "receiving"

    async def async_load(self) -> None:
        """Restore only rain accounting, never stale weather availability."""
        if (saved := await self._store.async_load()) is None:
            return
        raw, total = saved.get("raw"), saved.get("total", 0)
        if (
            type(total) is not int
            or total < 0
            or (raw is not None and (type(raw) is not int or not 0 <= raw <= 65535))
        ):
            _LOGGER.warning(
                "Ignoring invalid stored rain accounting for %s", self.entry.entry_id
            )
            return
        self.rain = RainTracker(
            raw=raw, total=total, initialized=saved.get("initialized", True)
        )
        if saved.get("station") != self.station:
            self.rain.replace_station()

    @property
    def station(self) -> list[int]:
        """Persist the on-air identity alongside its raw rain baseline."""
        return self._station

    @callback
    def _save_data(self) -> dict:
        return {
            "station": self.station,
            "raw": self.rain.raw,
            "total": self.rain.total,
            "initialized": self.rain.initialized,
        }

    async def async_start(self, source: FrameSource) -> None:
        """Require a real source subscription before setup can succeed."""
        ready = self.hass.loop.create_future()

        @callback
        def availability(connected: bool) -> None:
            if self._closed:
                return
            if not ready.done():
                ready.set_result(connected)
            self.connected = connected
            self.decoder.reset()
            if self._expiry:
                self._expiry.cancel()
                self._expiry = None
            if not connected:
                self._seen.clear()
                self._set_health("receiver_disconnected")
            else:
                self._set_health("waiting")
                self._expiry = self.hass.loop.call_later(self.timeout, self._expired)

        try:
            self._unsubscribe = source.async_subscribe(self.receive, availability)
            async with asyncio.timeout(7):
                if not await ready:
                    raise ConfigEntryNotReady(
                        "Selected receiver is offline or cannot subscribe"
                    )
        except BaseException:
            self.async_stop()
            raise

    @callback
    def _set_health(self, health: str) -> None:
        old = self.data
        self.async_set_updated_data(
            StationState(old.packet, old.rain_mm, old.last_seen, health)
        )

    @callback
    def receive(self, timings: list[int]) -> None:
        """Filter and commit a snapshot for each distinct verified measurement."""
        if self._closed:
            return
        if not timings:
            self.decoder.reset()
            return
        self.last_timing_count = len(timings)
        self.last_duration_us = sum(
            abs(value) for value in timings if type(value) is int
        )
        _LOGGER.debug(
            "RX %s: %s timings, %s us",
            self.entry.data["receiver"],
            self.last_timing_count,
            self.last_duration_us,
        )

        if len(timings) >= 400 and not getattr(self, "_raw_dumped", False):
            self._raw_dumped = True
            _LOGGER.debug("R56 RAW TIMINGS: %s", timings)

        packets = self.decoder.feed(timings)
        _LOGGER.debug(
            "R56 decoder: packets=%d station=%s",
            len(packets),
            self.station,
        )
        for packet in packets:
            if [packet.sensor_id, packet.channel] != self.station:
                self.filtered += 1
                continue
            now = monotonic()
            self._seen = {
                data: stamp for data, stamp in self._seen.items() if now - stamp < 1
            }
            if packet.payload in self._seen:
                self.duplicates += 1
                continue
            self._seen[packet.payload] = now
            self.valid += 1
            rain_mm = self.rain.update(packet.rain_tips, now)
            self.async_set_updated_data(
                StationState(packet, rain_mm, dt_util.utcnow(), "receiving")
            )
            if self._expiry:
                self._expiry.cancel()
            self._expiry = self.hass.loop.call_later(self.timeout, self._expired)
            self._store.async_delay_save(self._save_data, 5)
            _LOGGER.debug(
                "Verified R56 0x%04X channel=%s counter=%02X checksum=%02X payload=%s",
                packet.sensor_id,
                packet.channel,
                packet.counter,
                packet.payload[19],
                packet.payload.hex(" "),
            )

    @callback
    def _expired(self) -> None:
        self._expiry = None
        if not self._closed:
            self._set_health(
                "signal_lost" if self.connected else "receiver_disconnected"
            )

    @callback
    def async_stop(self) -> None:
        """Idempotently detach tasks and callbacks, including failed setup."""
        self._closed = True
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._expiry:
            self._expiry.cancel()
            self._expiry = None
        self.decoder.reset()

    async def async_close(self) -> None:
        """Stop receiving before flushing the final rain baseline."""
        self.async_stop()
        await self._store.async_save(self._save_data())
