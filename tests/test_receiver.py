"""API event adapter and lifecycle tests."""

import pytest
from aioesphomeapi import InfraredRFReceiveEvent
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.harf_vevor_weather.protocol import TimingDecoder
from custom_components.harf_vevor_weather.receiver import EsphomeSource, NativeSource

from .helpers import timings


async def test_native_timings_reconnect_unsubscribe(hass, radio):
    registry_entry = er.async_get(hass).async_get(radio.entity_id)
    source = NativeSource(hass, f"native:{registry_entry.id}", "Receiver")
    decoder = TimingDecoder()
    frames, available = [], []
    stop = source.async_subscribe(
        lambda raw: frames.extend(decoder.feed(raw)), available.append
    )
    from homeassistant.components.radio_frequency import RadioFrequencyReceivedSignal

    signal = RadioFrequencyReceivedSignal(timings=timings())
    radio._handle_received_signal(signal)
    assert frames[-1].sensor_id == 0x7203
    radio._attr_available = False
    radio.async_write_ha_state()
    await hass.async_block_till_done()
    assert available[-1] is False
    radio._attr_available = True
    radio.async_write_ha_state()
    await hass.async_block_till_done()
    assert available[-1] is True
    radio._handle_received_signal(signal)
    assert len(frames) == 2
    stop()
    radio._handle_received_signal(signal)
    assert len(frames) == 2


async def test_esphome_timings_channel_reconnect_unsubscribe(hass, esphome_entry):
    entry = esphome_entry
    source = EsphomeSource(
        hass, f"esphome:{entry.entry_id}:2:7", "Receiver", entry, 2, 7
    )
    frames, available = [], []
    decoder = TimingDecoder()
    stop = source.async_subscribe(
        lambda raw: frames.extend(decoder.feed(raw)), available.append
    )
    await hass.async_block_till_done()
    data = entry.runtime_data
    receive = data.client.subscribe_infrared_rf_receive.call_args.args[0]
    receive(InfraredRFReceiveEvent(device_id=0, key=7, timings=timings()))
    assert not frames
    receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings()))
    assert frames[-1].temperature == 18.3
    updated = data.async_subscribe_device_updated.call_args.args[0]
    data.available = False
    updated()
    assert available[-1] is False
    data.available = True
    updated()
    await hass.async_block_till_done()
    receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings()))
    assert len(frames) == 1  # Old generation cannot deliver.
    receive = data.client.subscribe_infrared_rf_receive.call_args.args[0]
    receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings()))
    assert len(frames) == 2
    stop()
    receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings()))
    assert len(frames) == 2
    assert data.client.subscribe_infrared_rf_receive.return_value.call_count == 2


async def test_discovery_individual_channels_and_offline(hass, esphome_entry):
    from aioesphomeapi import InfraredInfo, RadioFrequencyCapability, RadioFrequencyInfo

    from custom_components.harf_vevor_weather.receiver import (
        async_get_source,
        async_list_sources,
    )

    data = esphome_entry.runtime_data
    receiver = data.client.list_entities_services.return_value[0][0]
    data.client.list_entities_services.return_value = (
        [
            receiver,
            RadioFrequencyInfo(
                device_id=3,
                key=8,
                name="Other radio",
                capabilities=RadioFrequencyCapability.RECEIVER,
                frequency_min=433000000,
                frequency_max=434000000,
            ),
            RadioFrequencyInfo(
                key=9, capabilities=RadioFrequencyCapability.TRANSMITTER
            ),
            InfraredInfo(key=10),
        ],
        [],
    )
    sources = await async_list_sources(hass)
    assert [s.id for s in sources] == [
        f"esphome:{esphome_entry.entry_id}:2:7",
        f"esphome:{esphome_entry.entry_id}:3:8",
    ]
    assert "verify RX tuning" in sources[1].name
    assert async_get_source(hass, sources[0].id).key == 7
    for bad in (
        "esphome:missing:2:7",
        "esphome:bad",
        f"esphome:{esphome_entry.entry_id}:x:7",
        "unknown:stuff",
    ):
        assert async_get_source(hass, bad) is None
    data.available = False
    assert await async_list_sources(hass) == []
    assert async_get_source(hass, sources[0].id) is not None


async def test_native_preferred_and_rename(hass, radio, esphome_entry):
    from unittest.mock import patch

    from custom_components.harf_vevor_weather.receiver import async_list_sources

    registry = er.async_get(hass)
    row = registry.async_get(radio.entity_id)
    esphome_entry.runtime_data.device_info = type(
        "Device", (), {"mac_address": "aabbccddeeff"}
    )()
    with patch.object(
        type(registry), "async_get_entity_id", return_value=radio.entity_id
    ):
        sources = await async_list_sources(hass)
    assert [s.id for s in sources] == [f"native:{row.id}"]
    statuses = []
    stop = sources[0].async_subscribe(lambda raw: None, statuses.append)
    registry.async_update_entity(
        radio.entity_id, new_entity_id="radio_frequency.renamed_receiver"
    )
    await hass.async_block_till_done()
    assert statuses[-1] is True
    radio._attr_available = False
    radio.async_write_ha_state()
    await hass.async_block_till_done()
    assert statuses[-1] is False
    stop()


async def test_reload_removal_and_late_callbacks(hass, esphome_entry):
    entry = esphome_entry
    seen, statuses = [], []
    source = EsphomeSource(hass, f"esphome:{entry.entry_id}:2:7", "RX", entry, 2, 7)
    stop = source.async_subscribe(seen.append, statuses.append)
    await hass.async_block_till_done()
    data = entry.runtime_data
    old = data.client.subscribe_infrared_rf_receive.call_args.args[0]
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
    await hass.async_block_till_done()
    assert statuses[-1] is False
    old(InfraredRFReceiveEvent(device_id=2, key=7, timings=[89, -89]))
    assert seen == [[]]
    entry.mock_state(hass, ConfigEntryState.LOADED)
    await hass.async_block_till_done()
    assert statuses[-1] is True
    assert data.client.subscribe_infrared_rf_receive.call_count == 2
    stop()
    stop()
    assert data.client.subscribe_infrared_rf_receive.return_value.call_count == 2


@pytest.mark.parametrize("disconnect", [False, True])
async def test_cancel_pending_channel_discovery(hass, esphome_entry, disconnect):
    import asyncio

    pending = hass.loop.create_future()
    data = esphome_entry.runtime_data

    async def listing():
        return await pending

    data.client.list_entities_services = listing
    stop = EsphomeSource(hass, "unused", "RX", esphome_entry, 2, 7).async_subscribe(
        lambda raw: None, lambda value: None
    )
    await asyncio.sleep(0)
    if disconnect:
        data.available = False
        data.async_subscribe_device_updated.call_args.args[0]()
    else:
        stop()
    await hass.async_block_till_done()
    assert pending.cancelled()
    data.client.subscribe_infrared_rf_receive.assert_not_called()
    stop()


async def test_channel_disappears_and_api_error_retry(hass, esphome_entry):
    data = esphome_entry.runtime_data
    original = data.client.list_entities_services.return_value
    data.client.list_entities_services.return_value = ([], [])
    statuses = []
    stop = EsphomeSource(hass, "unused", "RX", esphome_entry, 2, 7).async_subscribe(
        lambda raw: None, statuses.append
    )
    await hass.async_block_till_done()
    assert statuses == [False]
    data.client.subscribe_infrared_rf_receive.assert_not_called()
    data.client.list_entities_services.side_effect = TimeoutError
    data.async_subscribe_device_updated.call_args.args[0]()
    await hass.async_block_till_done()
    assert statuses[-1] is False
    data.client.list_entities_services.side_effect = None
    data.client.list_entities_services.return_value = original
    data.async_subscribe_device_updated.call_args.args[0]()
    await hass.async_block_till_done()
    assert statuses[-1] is True
    stop()
