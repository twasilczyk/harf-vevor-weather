"""End-to-end synthetic ESPHome event -> HA state, accounting and lifecycle."""

from unittest.mock import patch

from aioesphomeapi import InfraredRFReceiveEvent
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.harf_vevor_weather.const import DOMAIN
from custom_components.harf_vevor_weather.coordinator import WeatherCoordinator

from .compat import MockConfigEntry
from .helpers import payload, timings


def station(hass, source, sensor_id=0x7203):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"VEVOR 0x{sensor_id:04X}",
        unique_id=f"{sensor_id:04x}:0",
        data={
            "receiver": f"esphome:{source.entry_id}:2:7",
            "sensor_id": sensor_id,
            "channel": 0,
        },
    )
    entry.add_to_hass(hass)
    return entry


def state(hass, entry, key, domain="sensor"):
    entity_id = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id
    return hass.states.get(entity_id)


async def setup_station(hass, entry):
    if entry.state is ConfigEntryState.SETUP_RETRY:
        await hass.config_entries.async_unload(entry.entry_id)
    if entry.state is ConfigEntryState.NOT_LOADED:
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_receive_to_entities_timeout_reconnect_unload(hass, esphome_entry):
    entry = station(hass, esphome_entry)
    await setup_station(hass, entry)
    assert state(hass, entry, "temperature").state == "unavailable"
    assert state(hass, entry, "health").state == "waiting"
    data = esphome_entry.runtime_data
    receive = data.client.subscribe_infrared_rf_receive.call_args.args[0]
    with patch(
        "custom_components.harf_vevor_weather.coordinator.monotonic", return_value=1
    ):
        receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings(copies=2)))
    await hass.async_block_till_done()
    assert state(hass, entry, "temperature").state == "18.3"
    assert state(hass, entry, "humidity").state == "57"
    assert state(hass, entry, "wind_speed").state == "0.0"
    assert state(hass, entry, "wind_direction").state == "261"
    assert state(hass, entry, "illuminance").state == "107"
    assert state(hass, entry, "rain_mm").state == "0.233"
    assert state(hass, entry, "rain_mm").attributes["state_class"] == "total_increasing"
    assert state(hass, entry, "battery_low", "binary_sensor").state == "off"
    assert entry.runtime_data.valid == 1
    assert entry.runtime_data.duplicates == 1
    with patch(
        "custom_components.harf_vevor_weather.coordinator.monotonic", return_value=21
    ):
        receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings()))
    assert entry.runtime_data.valid == 2  # Identical data next burst is fresh.
    # Exercise the scheduled callback rather than invoking the implementation.
    expiry = entry.runtime_data._expiry
    expire_callback = expiry._callback
    expiry.cancel()
    expire_callback()
    assert state(hass, entry, "temperature").state == "unavailable"
    assert state(hass, entry, "health").state == "signal_lost"
    updated = data.async_subscribe_device_updated.call_args.args[0]
    data.available = False
    updated()
    assert state(hass, entry, "health").state == "receiver_disconnected"
    data.available = True
    updated()
    await hass.async_block_till_done()
    assert state(hass, entry, "temperature").state == "unavailable"
    receive = data.client.subscribe_infrared_rf_receive.call_args.args[0]
    receive(
        InfraredRFReceiveEvent(
            device_id=2, key=7, timings=timings(payload(b4=0x9C, b18=0x86))
        )
    )
    assert state(hass, entry, "battery_low", "binary_sensor").state == "on"
    coordinator = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings()))
    assert coordinator._expiry is None
    assert coordinator._unsubscribe is None


async def test_two_stations_filter_bad_checksum_and_persistence(hass, esphome_entry):
    entries = [
        station(hass, esphome_entry, sensor_id) for sensor_id in (0x7203, 0x7204)
    ]
    for entry in entries:
        await setup_station(hass, entry)
    receivers = [
        call.args[0]
        for call in esphome_entry.runtime_data.client.subscribe_infrared_rf_receive.call_args_list
    ]
    bad = bytearray(payload())
    bad[19] ^= 1
    for receive in receivers:
        receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings(bytes(bad))))
        receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings()))
    assert entries[0].runtime_data.valid == 1
    assert entries[1].runtime_data.valid == 0
    for receive in receivers:
        receive(
            InfraredRFReceiveEvent(
                device_id=2, key=7, timings=timings(payload(b3=4, b6=0xAC))
            )
        )
    assert state(hass, entries[0], "temperature").state == "18.3"
    assert state(hass, entries[1], "temperature").state == "18.4"
    original_ids = {
        row.unique_id
        for row in er.async_entries_for_config_entry(
            er.async_get(hass), entries[0].entry_id
        )
    }
    for entry in entries:
        await hass.config_entries.async_unload(entry.entry_id)
    restored = WeatherCoordinator(hass, entries[0])
    await restored.async_load()
    assert restored.rain.raw == 1 and restored.rain.total == 1
    hass.config_entries.async_update_entry(
        entries[0], data={**entries[0].data, "sensor_id": 0x7205}
    )
    changed = WeatherCoordinator(hass, entries[0])
    await changed.async_load()
    assert changed.rain.raw is None
    assert changed.rain.update(500, 0) == 0.233
    assert original_ids == {
        row.unique_id
        for row in er.async_entries_for_config_entry(
            er.async_get(hass), entries[0].entry_id
        )
    }


async def test_missing_receiver_retries(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"receiver": "esphome:missing:0:7", "sensor_id": 0x7203, "channel": 0},
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_failed_subscription_and_failed_platform_unload(hass, esphome_entry):
    entry = station(hass, esphome_entry)
    esphome_entry.runtime_data.client.subscribe_infrared_rf_receive.side_effect = (
        RuntimeError("No RX")
    )
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    esphome_entry.runtime_data.client.subscribe_infrared_rf_receive.side_effect = None
    await setup_station(hass, entry)
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        return_value=False,
    ):
        assert not await hass.config_entries.async_unload(entry.entry_id)
    assert entry.runtime_data._unsubscribe is not None
    entry.mock_state(hass, ConfigEntryState.LOADED)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_live_reconfigure_preserves_history_and_rain_baseline(
    hass, esphome_entry
):
    entry = station(hass, esphome_entry)
    await setup_station(hass, entry)
    client = esphome_entry.runtime_data.client
    receive = client.subscribe_infrared_rf_receive.call_args.args[0]
    receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings()))
    registry = er.async_get(hass)
    before = {
        row.unique_id: row.entity_id
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    old_coordinator = entry.runtime_data
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"receiver": entry.data["receiver"], "sensor_id": "0x7205"}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.runtime_data is not old_coordinator
    assert entry.runtime_data.rain.raw is None
    assert entry.runtime_data.rain.total == 1
    receive = client.subscribe_infrared_rf_receive.call_args.args[0]
    receive(
        InfraredRFReceiveEvent(
            device_id=2, key=7, timings=timings(payload(b3=5, b13=2, b14=245))
        )
    )  # new station at 500 tips
    assert state(hass, entry, "rain_mm").state == "0.233"
    after = {
        row.unique_id: row.entity_id
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert before == after
    assert old_coordinator._unsubscribe is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_initial_silence_and_diagnostics(hass, esphome_entry):
    from custom_components.harf_vevor_weather.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = station(hass, esphome_entry)
    await setup_station(hass, entry)
    expiry = entry.runtime_data._expiry
    expire_callback = expiry._callback
    expiry.cancel()
    expire_callback()
    assert state(hass, entry, "health").state == "signal_lost"
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["receiver_connected"]
    assert diagnostics["valid"] == 0
    assert diagnostics["last_seen"] is None
    await hass.config_entries.async_unload(entry.entry_id)
