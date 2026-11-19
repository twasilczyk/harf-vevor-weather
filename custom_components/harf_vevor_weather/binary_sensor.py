"""Actual battery-low bit reported by the station."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from .entity import WeatherEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([BatteryLow(entry.runtime_data, "battery_low")])


class BatteryLow(WeatherEntity, BinarySensorEntity):
    """Low battery indication; normal is False."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        packet = self.coordinator.data.packet
        return packet.battery_low if packet else None
