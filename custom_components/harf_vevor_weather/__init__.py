"""VEVOR R56 weather station integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store

from .const import CONF_RECEIVER, DOMAIN
from .coordinator import WeatherCoordinator
from .receiver import async_get_source

type WeatherConfigEntry = ConfigEntry[WeatherCoordinator]
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: WeatherConfigEntry) -> bool:
    """Attach the selected RX channel and expose the station."""
    source = async_get_source(hass, entry.data[CONF_RECEIVER])
    if source is None:
        raise ConfigEntryNotReady(
            "Selected receiver is missing; reconfigure the station"
        )
    coordinator = WeatherCoordinator(hass, entry)
    await coordinator.async_load()
    try:
        await coordinator.async_start(source)
    except TimeoutError as err:
        raise ConfigEntryNotReady("Receiver subscription timed out") from err
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_stop)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_close()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: WeatherConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WeatherConfigEntry) -> bool:
    """Retain listeners if platform unload fails."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_close()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: WeatherConfigEntry) -> None:
    """Remove accounting only on deletion, never on reload."""
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()
