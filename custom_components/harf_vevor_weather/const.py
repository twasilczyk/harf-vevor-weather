"""Integration configuration and timeouts."""

DOMAIN = "harf_vevor_weather"
NAME = "VEVOR 7-in-1 Weather Station"
CONF_RECEIVER = "receiver"
CONF_SENSOR_ID = "sensor_id"
CONF_CHANNEL = "channel"
CONF_SIGNAL_TIMEOUT = "signal_timeout"
DEFAULT_SIGNAL_TIMEOUT = 90
DISCOVERY_TIMEOUT = 75
FREQUENCIES = (915_000_000, 868_350_000)
