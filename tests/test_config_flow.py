"""UI selection, explicit confirmation, manual setup and reconfiguration."""

from unittest.mock import patch

from aioesphomeapi import InfraredRFReceiveEvent

from custom_components.harf_vevor_weather.config_flow import WeatherConfigFlow
from custom_components.harf_vevor_weather.const import DOMAIN

from .compat import MockConfigEntry
from .helpers import timings


async def select_receiver(hass, esphome_entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"receiver": f"esphome:{esphome_entry.entry_id}:2:7"}
    )
    assert result["step_id"] == "method"
    return result


async def test_manual_invalid_confirm_and_duplicate(hass, esphome_entry):
    with patch(
        "custom_components.harf_vevor_weather.async_setup_entry", return_value=True
    ):
        for duplicate in (False, True):
            result = await select_receiver(hass, esphome_entry)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": "manual"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"sensor_id": "0x10000"}
            )
            assert result["errors"] == {"sensor_id": "invalid_sensor_id"}
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"sensor_id": "0x7203"}
            )
            assert result["step_id"] == "confirm"
            assert result["description_placeholders"]["sensor_id"] == "0x7203"
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"confirm": True}
            )
            if duplicate:
                assert result["reason"] == "already_configured"
            else:
                assert result["data"]["sensor_id"] == 0x7203
                assert result["data"]["channel"] == 0


async def test_discovery_requires_confirmation_and_cleans_subscription(
    hass, esphome_entry
):
    result = await select_receiver(hass, esphome_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "discover"}
    )
    assert result["type"] == "progress"
    await hass.async_block_till_done()
    client = esphome_entry.runtime_data.client
    receive = client.subscribe_infrared_rf_receive.call_args.args[0]
    receive(InfraredRFReceiveEvent(device_id=2, key=7, timings=timings()))
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["step_id"] == "confirm"
    assert not hass.config_entries.async_entries(DOMAIN)
    client.subscribe_infrared_rf_receive.return_value.assert_called_once()
    client.radio_frequency_transmit_raw_timings.assert_not_called()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert result["step_id"] == "retry"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_discovery_abort_and_timeout(hass, esphome_entry):
    result = await select_receiver(hass, esphome_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "discover"}
    )
    await hass.async_block_till_done()
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()
    esphome_entry.runtime_data.client.subscribe_infrared_rf_receive.return_value.assert_called_once()
    # Exercise the same await/finally timeout path without a 75-second test wait.
    flow = WeatherConfigFlow()
    flow.hass = hass
    flow._receiver = f"esphome:{esphome_entry.entry_id}:2:7"
    with patch(
        "custom_components.harf_vevor_weather.config_flow.DISCOVERY_TIMEOUT", 0.01
    ):
        assert await flow._discover() is None
    assert (
        esphome_entry.runtime_data.client.subscribe_infrared_rf_receive.return_value.call_count
        == 2
    )


async def test_no_receiver_retry(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["step_id"] == "no_receiver"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "no_receiver"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_reconfigure_stable_entry_and_options(hass, esphome_entry):
    receiver = f"esphome:{esphome_entry.entry_id}:2:7"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="7203:0",
        data={"receiver": receiver, "sensor_id": 0x7203, "channel": 0},
    )
    entry.add_to_hass(hass)
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="7204:0",
        data={"receiver": receiver, "sensor_id": 0x7204, "channel": 0},
    )
    other.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"receiver": receiver, "sensor_id": "0x7204"}
    )
    assert result["errors"] == {"sensor_id": "already_configured"}
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"receiver": receiver, "sensor_id": "0x7205"}
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["sensor_id"] == 0x7205
    assert entry.unique_id == "7205:0"
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal_timeout": 120}
    )
    assert result["data"] == {"signal_timeout": 120}
