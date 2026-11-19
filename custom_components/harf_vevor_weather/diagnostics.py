"""Local reception metadata for hardware validation; no network credentials."""


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    coordinator = entry.runtime_data
    return {
        "receiver": entry.data["receiver"],
        "sensor_id": f"0x{entry.data['sensor_id']:04X}",
        "channel": entry.data["channel"],
        "receiver_connected": coordinator.connected,
        "health": coordinator.data.health,
        "valid": coordinator.valid,
        "rejected_candidates": coordinator.decoder.rejected,
        "duplicates": coordinator.duplicates,
        "other_stations": coordinator.filtered,
        "last_timing_count": coordinator.last_timing_count,
        "last_duration_us": coordinator.last_duration_us,
        "last_seen": coordinator.data.last_seen,
        "rain_raw_tips": coordinator.rain.raw,
        "rain_total_tips": coordinator.rain.total,
    }
