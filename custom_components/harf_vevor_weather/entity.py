"""Stable device and entity identities independent of a battery-generated ID."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WeatherCoordinator


class WeatherEntity(CoordinatorEntity[WeatherCoordinator]):
    """One entity belonging to a configured physical station."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WeatherCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer="Fujian Youtong",
            model="R56 / VEVOR 7-in-1 (unofficial)",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.weather_available
