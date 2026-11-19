# VEVOR 7-in-1 Weather Station

Unofficial, receive-only Home Assistant integration for the **Fujian Youtong
R56**, also sold as VEVOR and under other labels. Domain: `harf_vevor_weather`.
The sensor's US FCC ID is **2AQBD-R56**, distinct from console ID
2AQBD-60234 and related sensor R53.

## Architecture and support

```text
R56 → 915 MHz 2-FSK → CC1101 demodulator → GDO2 signed NRZ timings
    → generic ESPHome remote_receiver + radio_frequency → ESPHome native API
    → Home Assistant receiver adapter → Python decoder → station entities
```

OpenHARF needs only generic receiver configuration. All weather-specific
decoding, station filtering, deduplication and rain storage live in HA. There is
no transmitter, pairing command, MQTT, rtl_433 daemon, SDR, serial bridge,
cloud upload or VEVOR ESPHome component. Python cannot demodulate IQ or recover
data that the CC1101/RMT never delivered.

Setup discovers the station's regular broadcasts and records which station HA
should track. **No pairing or station button press is required.** Confirming a
discovered ID only selects a local filter; it sends nothing to the station.
See the [comparison with Dooya, Insoma and Rhine](docs/COMPARISON.md) for the
shared project conventions and the receive-only differences.

| Component | Support and evidence |
| --- | --- |
| US R56, nominal 915 MHz | Supplied real captured payload regression passes; live OpenHARF reception still requires the checklist below |
| Related 868.35 MHz models / R53 | Same referenced weather layout; subject to hardware testing, not a claim of verified model support |
| HA 2026.9.2, aioesphomeapi 46.2.0 | Minimum tested; ESPHome platform exposes transmitters only, so this integration subscribes directly to its runtime API client |
| Local HA 2026.12.0.dev0, aioesphomeapi 46.30.0 | Native public RX API and direct fallback tested; see [validation record](docs/VALIDATION.md) |
| Installed ESPHome 2026.10.2 | Generic schema/API source inspected; example configuration validated; RF hardware not exercised |

Native receiver entities are preferred when available. Otherwise each ESPHome
`RadioFrequencyInfo` with `RECEIVER` capability is listed separately, selected
by config entry + device ID + key. Channels are never mixed. Native selections
use registry UUIDs and survive entity renames. ESPHome key/name changes require
Reconfigure rather than silently selecting another radio. Known native 868/915
MHz receivers are listed first. Fallback channel labels show advertised ranges;
they may describe TX capability rather than actual RX tuning.

**The OOK label in HA is not an on-air modulation assertion.** Current native
received signals default to OOK metadata and ESPHome supplies only signed
timings. Both adapters accept them. Configure actual **2-FSK** on CC1101.

## Installation and configuration

For manual installation, copy **`custom_components/harf_vevor_weather`** into your HA
configuration's `custom_components` directory and restart HA. The directory
must match the domain: `harf_vevor_weather`.

For HACS, once this repository is published, add
`https://github.com/twasilczyk/harf-vevor-weather` as a custom repository with
category **Integration**, download it and restart HA. Metadata is included;
this work does not publish a release or claim HACS/Marketplace listing approval.

1. Add your generic OpenHARF node through HA's ESPHome integration. Confirm it
   exposes an RF **receive** channel and is connected.
2. Add **VEVOR 7-in-1 Weather Station** in Settings → Devices & services.
3. Choose the particular radio/channel already tuned for the station.
4. Listen for up to 75 seconds. A checksum-verified packet reveals `0xNNNN`
   and channel 0. **Explicitly confirm that this is your station.** The first
   received station may belong to a neighbor. Decline to retry or enter your ID.
5. Alternatively enter the sensor ID manually (`0x7203` or decimal `29187`).
   Manual setup still requires an RX source. Weather remains unavailable until
   the configured station transmits; setup never invents measurements.

Multiple stations may share one receiver. Duplicate ID/channel entries are
rejected. Only byte-1 value zero (type 0 / channel 0) has the verified weather
layout supported by the authoritative reference. Other types/channels are
rejected, rather than interpreted using an unverified layout.

After battery replacement some sensors choose a new ID. Use **Reconfigure** to
replace the ID or receiver. Entity unique IDs and device identity are based on
the config entry, so this preserves history. To find a changed ID, start the
Add integration listening flow, note the candidate ID, then cancel and
reconfigure the existing entry. Do not confirm a neighbor's station.

Options allow a lost-signal timeout of 60–600 seconds (default 90). Receiver
disconnect and loss of valid station packets have separate diagnostic states.
Reconnection attaches automatically; weather becomes available on the next
valid station packet. Last successful packet time remains visible when stale.

## Measurements and rain semantics

One device contains temperature (°C), relative humidity (%), average wind and
gust (km/h), wind direction (°), cumulative rainfall (mm), UV index,
illuminance (lx), battery low, last packet timestamp and reception status.
All measurements share one immutable snapshot per distinct valid packet.
HA may convert native units to your chosen display units. There is no forecast,
RSSI, LQI or frequency-offset sensor: raw timings cannot supply those values.

Checksum and adjacent counters must agree, including `FF → 00` wrap. Repeated
copies/overlapping captures with identical payloads within one second are
discarded. Counter changes are accepted immediately; an identical measurement
20 seconds later still refreshes availability. Station ID and channel are
checked before updating any measurement or rain state.

Rain uses corrected 16-bit tip counts at **0.233 mm/tip**. The first reading
initializes the cumulative total from the station's absolute counter.
Subsequent increments accumulate into a persisted `total_increasing` value.
It is not rain rate or rainfall since the preceding packet. Use HA utility
meters for daily/monthly totals.

The station can transmit checksum-valid misreads around byte carry, typically
256 tips too low. Nonzero decreases are held indefinitely, never adopted just
because they repeat. Zero resets require three distinct observations at least
five seconds apart; recovery cancels pending confirmation. A near-full counter
(≥65280) rolling to ≤255 uses the same confirmation, then accounts for its
16-bit wrap. Near-wrap battery resets are inherently ambiguous without another
signal; a changed ID is rebaselined instead. Same-ID resets first seen at a
nonzero lower count remain held; confirm the hardware reset and reconfigure
the station's identity if it changed. This deliberately favors undercounting
over an artificial statistics spike.

On ID replacement the existing cumulative total is retained and the next
packet establishes a new raw baseline, without adding the replacement's
historical rain. Baselines/totals are saved after updates and on clean unload;
up to five seconds of changes can be lost in an abrupt power failure. Pending
reset evidence is not restored across restarts.

## Generic ESPHome receiver example

See [examples/cc1101-receiver.yaml](examples/cc1101-receiver.yaml), a standalone
diagnostic configuration with **example pins**, not an OpenHARF pin map. Adapt
the generic settings to your existing board; no OpenHARF files are modified by
this integration. It uses CC1101 asynchronous serial RX on **GDO2**, starts RX
with `cc1101.begin_rx`, and attaches `radio_frequency: platform: ir_rf_proxy`
to `remote_receiver_id`. Merely configuring `remote_receiver` does not ensure
an API RF channel exists.

Suggested starting settings:

```yaml
cc1101:
  # id / SPI / chip select as appropriate for your board
  frequency: 915MHz
  modulation_type: 2-FSK
  symbol_rate: 11260
  fsk_deviation: 38kHz
  filter_bandwidth: 203kHz
remote_receiver:
  # pin wired to CC1101 GDO2
  filter: 65us
  idle: 2000us
  receive_symbols: 512
```

These are diagnostic starting values, not a proven universal preset. The user
capture measured about 914.994 MHz (tones near 914.960/915.028); the linked US
report describes a different unit near 915.031 MHz. Nominal center is 915 MHz,
with roughly ±37–38 kHz deviation, 11,260 symbols/s (~88.8 µs/bit), an
85–86 ms burst about every 20 seconds and repeated packet copies. Related EU
hardware uses about 868.35 MHz. Oscillator calibration and antennas matter.

**2000 µs idle may split valid long NRZ runs.** Try a longer idle (for example
9000 µs) if diagnostics show truncated frames, respecting the target's RMT
clock/max-idle limits. Increase `receive_symbols` where supported if capture
memory overflows. Check chip-specific RMT RAM/DMA and other RMT consumers;
512 receive symbols is not a guarantee that every board captures the full
burst. A larger idle with insufficient memory can also overflow. Adjust filter
if 89 µs single bits are lost. These failures cannot be fixed by HA tuning.

The decoder searches sync `CA 54` anywhere, both polarities, and preserves long
valid runs. Current HA/ESPHome APIs emit whole capture events without chunk
sequence or continuity metadata. They are deliberately not stitched together:
missing idle samples cannot be reconstructed safely. The pure streaming API
supports explicitly contiguous chunks for future transports and tests, bounded
to 250 ms; reconnects always discard partial data. Do not use the reference
project's packet-mode decoder or a VEVOR-specific ESPHome external component.

## Live validation and troubleshooting

Simulated tests **do not certify live RX**. Run this checklist with your node:

1. Enable debug logging for `custom_components.harf_vevor_weather` (example below).
   Open receiver selection. Record native entity identity or ESPHome entry,
   `device_id`, `key`, capability flags, advertised frequency range and
   modulation. Ensure the selected individual channel is RX-capable. No source
   means the generic RF proxy/API exposure is missing or disconnected.
2. Check CC1101 SPI communication, GDO2 wiring, antenna/band, actual 2-FSK tuning
   and `begin_rx`. Keep the station nearby for this diagnostic.
3. Complete manual setup with the known sensor ID, or leave discovery active.
   On a configured entry, debug `RX` lines report timing count and total
   microseconds; Download diagnostics also exposes the latest counts. No
   events despite advertised RX means the generic firmware/API forwarding
   path has not been verified. A successful subscription alone proves no RF.
4. Observe several bursts over at least two minutes. Expect approximately
   20-second spacing; check actual durations and counts, rather than assuming
   every API event is one complete 85 ms RF burst. Repeated short captures or
   undecodable captures suggest idle/filter/RMT overflow or RF tuning issues.
   Test a larger idle and sufficient receive symbols. Inversion is handled
   automatically; zero checksum-valid packets with both polarities suggests
   lost bits/timing distortion, not an HA OOK-label problem.
5. Confirm `Verified R56` debug lines contain your ID/channel, checksum, adjacent
   counter and payload. Check duplicates do not count as separate updates, and
   counters/last packet time advance on later bursts. Compare temperature,
   humidity and rain with the station console.
6. Verify all entities update, then disconnect the receiver: measurements become
   unavailable and status says receiver disconnected. Reconnect and wait for
   the next packet. Leave the receiver online but remove RF reception: after
   90 seconds status becomes no recent valid packet. Restore RF and verify
   recovery. Reload the integration and ensure exactly one subscription per
   configured station remains.

```yaml
logger:
  default: warning
  logs:
    custom_components.harf_vevor_weather: debug
```

Do not post API encryption keys or Wi-Fi credentials in reports. Capture
metadata, sanitized debug lines and checksum-valid packet bytes are sufficient
for most decoder troubleshooting. Raw timing captures, if available separately,
can be checked offline with `python scripts/decode_capture.py capture.json`.
It expects a JSON list of signed microsecond durations; it is a diagnostic tool,
not a runtime bridge or IQ input.

## Captured regression fixture

```text
AA 00 72 03 1C 02 AB 39 01 01 00 02 06 01 02 01 01 6C 85 21 86
```

This user-supplied US R56 capture decodes to ID `0x7203`, channel 0,
18.3 °C, 57%, 0 km/h average/gust, 261°, 0.233 mm, UV 0, 107 lx,
battery normal, counter `85`, checksum `21`, next counter `86`.
The waveform tests synthesize signed timings from this packet; they are not
claimed to be a recorded OpenHARF timing capture. No local IQ recording was
used or required.

## Development and validation

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

The minimum-version test environment skips native-RX-only tests because that
API does not exist there. The installed newer HA environment runs them. To test
the pure decoder without Home Assistant, install only pytest and run:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --noconftest -o addopts='' tests/test_protocol.py
```

Local development config and integration symlink are prepared under ignored
`config/`. In this workspace launch your existing HA development environment:

```sh
cd /home/twasilczyk/projects/harf-vevor-weather
/home/twasilczyk/projects/venv-hass/bin/hass -c config
```

Then open `http://localhost:8123`, finish local onboarding, add ESPHome, and
follow the live checklist. If another HA is already using port 8123, set a
different `http.server_port` in this isolated config before launching.
For a fresh clone, create `config/custom_components`, symlink
`../../custom_components/harf_vevor_weather` there and add `default_config:` to
`config/configuration.yaml`. Keep credentials and `.storage` out of Git.

See [docs/VALIDATION.md](docs/VALIDATION.md) for exact tested versions,
commands/results, remaining hardware gate and release checks. CI runs pytest,
Ruff, hassfest and HACS metadata validation. No vendor logo or unlicensed brand
artwork is included. Releases use `v<manifest version>`; nothing is pushed or
published by the implementation task.

## References and licensing

This is an original MIT-licensed Python implementation. Protocol behavior was
cross-checked against the references; no GPL source is copied or distributed.
The per-byte wrapping rule in rtl_433 is used instead of the MIT reference's
combined-word subtraction, which differs at encoded byte wrap boundaries.

- [rtl_433 vevor_7in1.c](https://github.com/merbanan/rtl_433/blob/master/src/devices/vevor_7in1.c), Bruno OCTAU / ProfBoc75 and contributors: authoritative field layout/checksum (GPL behavioral reference only).
- [US reception report #3591](https://github.com/merbanan/rtl_433/issues/3591): related US YT60234/R53 RF measurements; not evidence that all R56 units have the same frequency offset.
- [parrel/esphome-vevor-7in1](https://github.com/parrel/esphome-vevor-7in1) and [decoder](https://github.com/parrel/esphome-vevor-7in1/blob/main/components/vevor_decoder/vevor_decoder.cpp): MIT timing/polarity and rain-hold behavioral reference, not a runtime dependency.
- [FCC sensor record 2AQBD-R56](https://fccid.io/2AQBD-R56).
- Tomasz Wasilczyk's [harf-dooya](https://github.com/twasilczyk/harf-dooya), [latest receiver](https://github.com/twasilczyk/harf-dooya/blob/main/custom_components/harf_dooya/receiver.py), [config flow](https://github.com/twasilczyk/harf-dooya/blob/main/custom_components/harf_dooya/config_flow.py), [harf-insoma](https://github.com/twasilczyk/harf-insoma), [harf-rhine](https://github.com/twasilczyk/harf-rhine), [hass-proflame](https://github.com/twasilczyk/hass-proflame), and [Aetf/hass-proflame](https://github.com/Aetf/hass-proflame): receiver lifecycle and project conventions.
- HA [RF entity API](https://contributors.home-assistant.io/core/entities/radio-frequency/), [RF integration](https://www.home-assistant.io/integrations/radio_frequency/), [helpers](https://github.com/home-assistant/core/blob/dev/homeassistant/components/radio_frequency/helpers.py), [ESPHome adapter](https://github.com/home-assistant/core/blob/dev/homeassistant/components/esphome/radio_frequency.py).
- ESPHome [remote_receiver](https://esphome.io/components/remote_receiver/) and [radio_frequency](https://next.esphome.io/components/radio_frequency/). The local installed code, rather than documentation alone, determined API compatibility.
