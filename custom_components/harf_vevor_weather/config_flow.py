"""Select one receiver, discover and confirm a station, or enter its ID."""

import asyncio
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CHANNEL,
    CONF_RECEIVER,
    CONF_SENSOR_ID,
    CONF_SIGNAL_TIMEOUT,
    DEFAULT_SIGNAL_TIMEOUT,
    DISCOVERY_TIMEOUT,
    DOMAIN,
)
from .protocol import Packet, TimingDecoder
from .receiver import async_get_source, async_list_sources

_LOGGER = logging.getLogger(__name__)


def parse_sensor_id(value: str) -> int:
    """Accept explicit 0x hexadecimal or decimal without ambiguous base guessing."""
    text = str(value).strip()
    number = int(text, 16 if text.lower().startswith("0x") else 10)
    if not 0 <= number <= 0xFFFF:
        raise ValueError("Sensor ID outside 16-bit range")
    return number


def _source_selector(sources, configured=None):
    options = [
        selector.SelectOptionDict(value=source.id, label=source.name)
        for source in sources
    ]
    if configured and configured not in {source.id for source in sources}:
        options.append(
            selector.SelectOptionDict(
                value=configured, label="Configured receiver (offline or missing)"
            )
        )
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options, mode=selector.SelectSelectorMode.DROPDOWN
        )
    )


class WeatherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discover periodic broadcasts and confirm which station to track in HA."""

    VERSION = 1

    def __init__(self) -> None:
        self._receiver = ""
        self._sensor_id = None
        self._channel = 0
        self._discover_task = None

    async def async_step_user(self, user_input=None):
        sources = await async_list_sources(self.hass)
        errors = {}
        if user_input:
            if user_input[CONF_RECEIVER] not in {source.id for source in sources}:
                errors[CONF_RECEIVER] = "receiver_unusable"
            else:
                self._receiver = user_input[CONF_RECEIVER]
                return await self.async_step_method()
        if not sources:
            return await self.async_step_no_receiver()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_RECEIVER): _source_selector(sources)}
            ),
            errors=errors,
        )

    async def async_step_no_receiver(self, user_input=None):
        if user_input is not None:
            return await self.async_step_user()
        return self.async_show_form(step_id="no_receiver", data_schema=vol.Schema({}))

    async def async_step_method(self, user_input=None):
        return self.async_show_menu(
            step_id="method", menu_options=["discover", "manual"]
        )

    async def async_step_discover(self, user_input=None):
        starting = self._discover_task is None
        if starting:
            self._discover_task = self.hass.async_create_background_task(
                self._discover(), "R56 station discovery"
            )
        if starting or not self._discover_task.done():
            return self.async_show_progress(
                step_id="discover",
                progress_action="listening",
                progress_task=self._discover_task,
            )
        packet = self._discover_task.result()
        self._discover_task = None
        if packet:
            self._sensor_id, self._channel = packet.sensor_id, packet.channel
        return self.async_show_progress_done(
            next_step_id="confirm" if packet else "retry"
        )

    async def _discover(self) -> Packet | None:
        source = async_get_source(self.hass, self._receiver)
        if source is None:
            return None
        found = self.hass.loop.create_future()
        decoder = TimingDecoder()

        @callback
        def receive(timings):
            for packet in decoder.feed(timings):
                if not found.done():
                    found.set_result(packet)

        @callback
        def available(value):
            decoder.reset()
            if not value and not found.done():
                found.set_result(None)

        stop = None
        try:
            stop = source.async_subscribe(receive, available)
            async with asyncio.timeout(DISCOVERY_TIMEOUT):
                return await found
        except TimeoutError:
            return None
        except Exception:
            _LOGGER.debug("Station discovery failed", exc_info=True)
            return None
        finally:
            if stop:
                stop()

    @callback
    def async_remove(self) -> None:
        """Cancellation closes the discovery subscription in its finally block."""
        if self._discover_task and not self._discover_task.done():
            self._discover_task.cancel()
        super().async_remove()

    async def async_step_retry(self, user_input=None):
        return self.async_show_menu(
            step_id="retry", menu_options=["discover", "manual", "user"]
        )

    async def async_step_manual(self, user_input=None):
        errors = {}
        if user_input:
            try:
                self._sensor_id = parse_sensor_id(user_input[CONF_SENSOR_ID])
            except ValueError:
                errors[CONF_SENSOR_ID] = "invalid_sensor_id"
            else:
                self._channel = 0
                return await self.async_step_confirm()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {vol.Required(CONF_SENSOR_ID): selector.TextSelector()}
            ),
            errors=errors,
        )

    async def async_step_confirm(self, user_input=None):
        assert self._sensor_id is not None
        if user_input is not None:
            if not user_input["confirm"]:
                return await self.async_step_retry()
            await self.async_set_unique_id(f"{self._sensor_id:04x}:{self._channel}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"VEVOR 0x{self._sensor_id:04X}",
                data={
                    CONF_RECEIVER: self._receiver,
                    CONF_SENSOR_ID: self._sensor_id,
                    CONF_CHANNEL: self._channel,
                },
            )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {vol.Required("confirm", default=False): selector.BooleanSelector()}
            ),
            description_placeholders={
                "sensor_id": f"0x{self._sensor_id:04X}",
                "channel": str(self._channel),
            },
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        sources = await async_list_sources(self.hass)
        errors = {}
        if user_input:
            receiver = user_input[CONF_RECEIVER]
            if receiver != entry.data[CONF_RECEIVER] and receiver not in {
                source.id for source in sources
            }:
                errors[CONF_RECEIVER] = "receiver_unusable"
            try:
                sensor_id = parse_sensor_id(user_input[CONF_SENSOR_ID])
            except ValueError:
                errors[CONF_SENSOR_ID] = "invalid_sensor_id"
            else:
                if any(
                    other.entry_id != entry.entry_id
                    and other.data.get(CONF_SENSOR_ID) == sensor_id
                    and other.data.get(CONF_CHANNEL) == 0
                    for other in self._async_current_entries()
                ):
                    errors[CONF_SENSOR_ID] = "already_configured"
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_RECEIVER: receiver,
                        CONF_SENSOR_ID: sensor_id,
                        CONF_CHANNEL: 0,
                    },
                    unique_id=f"{sensor_id:04x}:0",
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_RECEIVER, default=entry.data[CONF_RECEIVER]
                    ): _source_selector(sources, entry.data[CONF_RECEIVER]),
                    vol.Required(
                        CONF_SENSOR_ID, default=f"0x{entry.data[CONF_SENSOR_ID]:04X}"
                    ): selector.TextSelector(),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return WeatherOptionsFlow()


class WeatherOptionsFlow(OptionsFlow):
    """Only software freshness policy is configurable here; no radio tuning."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SIGNAL_TIMEOUT,
                        default=self.config_entry.options.get(
                            CONF_SIGNAL_TIMEOUT, DEFAULT_SIGNAL_TIMEOUT
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=60, max=600))
                }
            ),
        )
