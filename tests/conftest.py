"""First gate: real API event models -> source -> pure timing decoder."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aioesphomeapi import (
    RadioFrequencyCapability,
    RadioFrequencyInfo,
)
from homeassistant.components.radio_frequency import DATA_COMPONENT

try:
    from homeassistant.components.radio_frequency import RadioFrequencyReceiverEntity
except ImportError:
    RadioFrequencyReceiverEntity = object
from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component

from .compat import MockConfigEntry


class Radio(RadioFrequencyReceiverEntity):
    _attr_unique_id = "weather_receiver"
    _attr_name = "Weather receiver"

    @property
    def supported_frequency_ranges(self):
        return [(850_000_000, 930_000_000)]


@pytest.fixture
async def radio(hass):
    if RadioFrequencyReceiverEntity is object:
        pytest.skip(
            "Native RF RX API absent on this HA version; fallback tested separately"
        )
    assert await async_setup_component(hass, "radio_frequency", {})
    instance = Radio()
    await hass.data[DATA_COMPONENT].async_add_entities([instance])
    return instance


@pytest.fixture
def esphome_entry(hass):
    entry = MockConfigEntry(domain="esphome", title="OpenHARF")
    entry.add_to_hass(hass)
    info = RadioFrequencyInfo(
        key=7,
        device_id=2,
        capabilities=RadioFrequencyCapability.RECEIVER,
        frequency_min=850_000_000,
        frequency_max=930_000_000,
    )
    client = MagicMock()
    client.list_entities_services = AsyncMock(return_value=([info], []))
    entry.runtime_data = MagicMock(available=True, client=client, device_info=None)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    yield entry
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations on both test plugin generations."""
