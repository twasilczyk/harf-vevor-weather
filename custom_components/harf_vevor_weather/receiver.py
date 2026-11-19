"""Receive-only sources: public HA RF API, then individual ESPHome channels.

OOK metadata describes the current API's timing transport; it does not describe
the CC1101's on-air modulation or tune the radio.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .const import FREQUENCIES

_LOGGER = logging.getLogger(__name__)
type FrameHandler = Callable[[list[int]], None]
type AvailabilityHandler = Callable[[bool], None]


@dataclass
class FrameSource:
    """An externally managed receiver; empty timings invalidate partial data."""

    hass: HomeAssistant
    id: str
    name: str

    def async_subscribe(
        self, handler: FrameHandler, availability: AvailabilityHandler
    ) -> CALLBACK_TYPE:
        """Subscribe, reporting True only after successful attachment."""
        raise NotImplementedError


class NativeSource(FrameSource):
    """A registry-UUID-selected native receiver, surviving renames and reloads."""

    @callback
    def async_subscribe(
        self, handler: FrameHandler, availability: AvailabilityHandler
    ) -> CALLBACK_TYPE:
        from homeassistant.components.radio_frequency import async_subscribe_receiver

        registry_id = self.id.removeprefix("native:")
        registry = er.async_get(self.hass)
        receive_stop = None
        state_stop = None
        active = True
        generation = 0

        @callback
        def refresh(event=None) -> None:
            nonlocal receive_stop, generation
            if not active:
                return
            generation += 1
            if receive_stop is not None:
                receive_stop()
                receive_stop = None
            handler([])
            entry = registry.async_get(registry_id)
            state = self.hass.states.get(entry.entity_id) if entry else None
            if state is None or state.state == STATE_UNAVAILABLE:
                availability(False)
                return
            token = generation

            @callback
            def receive(signal) -> None:
                if active and token == generation:
                    handler(list(signal.timings))

            try:
                receive_stop = async_subscribe_receiver(self.hass, registry_id, receive)
            except HomeAssistantError:
                availability(False)
            else:
                availability(True)

        @callback
        def bind(event=None) -> None:
            nonlocal state_stop
            if (
                event is not None
                and event.data.get("entity_id") != tracked[0]
                and event.data.get("changes", {}).get("entity_id") != tracked[0]
            ):
                return
            if state_stop is not None:
                state_stop()
            entry = registry.async_get(registry_id)
            tracked[0] = entry.entity_id if entry else None
            if entry:
                state_stop = async_track_state_change_event(
                    self.hass, [entry.entity_id], state_changed
                )
            refresh()

        @callback
        def state_changed(event) -> None:
            old, new = event.data["old_state"], event.data["new_state"]
            # Receive timestamps change every frame. Rebind only on availability
            # transitions (including removal/recreation of the underlying entity).
            if (
                old is None
                or new is None
                or (
                    (old.state == STATE_UNAVAILABLE) != (new.state == STATE_UNAVAILABLE)
                )
            ):
                refresh()

        tracked: list[str | None] = [None]
        registry_stop = self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, bind
        )
        bind()

        @callback
        def stop() -> None:
            nonlocal active
            if not active:
                return
            active = False
            registry_stop()
            if state_stop:
                state_stop()
            if receive_stop:
                receive_stop()

        return stop


async def _receiver_infos(entry: ConfigEntry) -> list[Any]:
    """Query raw API metadata: older HA may cache only transmitters."""
    from aioesphomeapi import RadioFrequencyCapability, RadioFrequencyInfo

    data = getattr(entry, "runtime_data", None)
    if entry.state is not ConfigEntryState.LOADED or data is None or not data.available:
        return []
    async with asyncio.timeout(5):
        infos, _ = await data.client.list_entities_services()
    result = []
    for info in infos:
        if not isinstance(info, RadioFrequencyInfo):
            continue
        _LOGGER.debug(
            "ESPHome %s device_id=%s key=%s capabilities=%s range=%s..%s Hz "
            "modulations=%s (metadata does not tune RX)",
            entry.entry_id,
            info.device_id,
            info.key,
            info.capabilities,
            info.frequency_min,
            info.frequency_max,
            info.supported_modulations,
        )
        if info.capabilities & RadioFrequencyCapability.RECEIVER:
            result.append(info)
    return result


@dataclass
class EsphomeSource(FrameSource):
    """An exact (entry, device_id, key), with generation-guarded callbacks."""

    entry: ConfigEntry
    device_id: int
    key: int

    @callback
    def async_subscribe(
        self, handler: FrameHandler, availability: AvailabilityHandler
    ) -> CALLBACK_TYPE:
        active = True
        generation = 0
        receive_stop = None
        update_stop = None
        task = None
        retry_stop = None

        @callback
        def clear() -> None:
            nonlocal generation, receive_stop, task, retry_stop
            generation += 1
            if task:
                task.cancel()
                task = None
            if retry_stop:
                retry_stop.cancel()
                retry_stop = None
            if receive_stop:
                receive_stop()
                receive_stop = None

        async def attach(data, token) -> None:
            nonlocal receive_stop, retry_stop
            try:
                infos = await _receiver_infos(self.entry)
                if not active or token != generation or not data.available:
                    return
                if not any(
                    (info.device_id, info.key) == (self.device_id, self.key)
                    for info in infos
                ):
                    raise HomeAssistantError("Selected RF receive channel is missing")

                @callback
                def receive(event) -> None:
                    if (
                        active
                        and token == generation
                        and data.available
                        and (event.device_id, event.key) == (self.device_id, self.key)
                    ):
                        handler(list(event.timings))

                receive_stop = data.client.subscribe_infrared_rf_receive(receive)
                availability(True)
            except Exception:
                if active and token == generation:
                    availability(False)
                    _LOGGER.debug("Unable to attach %s", self.id, exc_info=True)
                    retry_stop = self.hass.loop.call_later(10, updated)

        @callback
        def updated() -> None:
            nonlocal task, retry_stop
            if not active:
                return
            if retry_stop:
                retry_stop.cancel()
                retry_stop = None
            data = self.entry.runtime_data
            if not data.available:
                clear()
                handler([])
                availability(False)
            elif receive_stop is None and (task is None or task.done()):
                task = self.hass.async_create_task(attach(data, generation))

        @callback
        def state_changed() -> None:
            nonlocal update_stop
            if not active:
                return
            if self.entry.state is not ConfigEntryState.LOADED:
                clear()
                if update_stop:
                    update_stop()
                    update_stop = None
                handler([])
                availability(False)
            elif update_stop is None:
                update_stop = self.entry.runtime_data.async_subscribe_device_updated(
                    updated
                )
                updated()

        state_stop = self.entry.async_on_state_change(state_changed)

        @callback
        def stop() -> None:
            nonlocal active
            if not active:
                return
            active = False
            clear()
            state_stop()
            if update_stop:
                update_stop()

        try:
            state_changed()
        except Exception:
            stop()
            raise
        return stop


async def async_list_sources(hass: HomeAssistant) -> list[FrameSource]:
    """Prefer native receivers; fallback channels retain explicit stable keys.

    Frequency ranges are useful labels but ESPHome sometimes advertises TX-only
    ranges. Unknown and out-of-band RX channels remain selectable with a warning
    in their label; RX tuning must be verified on the device.
    """
    registry = er.async_get(hass)
    sources: list[FrameSource] = []
    native_ids: set[str] = set()
    try:
        from homeassistant.components.radio_frequency import (
            ModulationType,
            async_get_receivers,
        )

        for frequency in FREQUENCIES:
            native_ids.update(async_get_receivers(hass, frequency, ModulationType.OOK))
    except ImportError, HomeAssistantError:
        pass
    for entity_id in sorted(native_ids):
        entry = registry.async_get(entity_id)
        state = hass.states.get(entity_id)
        if entry and state and state.state != STATE_UNAVAILABLE:
            sources.append(NativeSource(hass, f"native:{entry.id}", state.name))

    for entry in hass.config_entries.async_entries("esphome"):
        try:
            infos = await _receiver_infos(entry)
        except Exception:
            _LOGGER.debug("Unable to discover %s", entry.entry_id, exc_info=True)
            continue
        for info in infos:
            # Match the public ESPHome unique-ID builder, not private HA entity
            # attributes. Suppress only the same usable native channel.
            from aioesphomeapi import build_device_unique_id

            device_info = entry.runtime_data.device_info
            native_entity = (
                registry.async_get_entity_id(
                    "radio_frequency",
                    "esphome",
                    build_device_unique_id(device_info.mac_address, info),
                )
                if device_info
                else None
            )
            if native_entity in native_ids and any(
                source.id == f"native:{registry.async_get(native_entity).id}"
                for source in sources
            ):
                continue
            band = f"{info.frequency_min / 1e6:g}–{info.frequency_max / 1e6:g} MHz"
            label = (
                f"{entry.title}: {info.name or info.object_id or 'RF'} "
                f"(device {info.device_id}, key {info.key}; {band}; verify RX tuning)"
            )
            sources.append(
                EsphomeSource(
                    hass,
                    f"esphome:{entry.entry_id}:{info.device_id}:{info.key}",
                    label,
                    entry,
                    info.device_id,
                    info.key,
                )
            )
    return sources


@callback
def async_get_source(hass: HomeAssistant, source_id: str) -> FrameSource | None:
    """Resolve a configured source even while it is temporarily offline."""
    parts = source_id.split(":")
    if len(parts) == 2 and parts[0] == "native":
        if (entry := er.async_get(hass).async_get(parts[1])) is not None:
            return NativeSource(hass, source_id, entry.entity_id)
    if len(parts) == 4 and parts[0] == "esphome":
        entry = hass.config_entries.async_get_entry(parts[1])
        if entry is not None and entry.domain == "esphome":
            try:
                device_id, key = int(parts[2]), int(parts[3])
            except ValueError:
                return None
            return EsphomeSource(hass, source_id, entry.title, entry, device_id, key)
    return None
