# Validation record

Completed in this workspace on 2026-11-18. These are software and simulated
event results, **not a claim of live R56 → CC1101 → OpenHARF → HA reception**.

Follow-up comparison with Dooya, Insoma and Rhine is recorded in
[COMPARISON.md](COMPARISON.md). After changing the flow and translation keys to
`discover`, both full test suites passed again (45 passed on newer HA;
43 passed / 2 native-API skips on HA 2026.9.2). Ruff and hassfest also passed.
The discovery test explicitly verifies that no RF transmit command is issued.

## Installed stack and compatibility decision

- Python 3.14.4.
- `/home/twasilczyk/projects/venv-hass`: HA 2026.12.0.dev0, installed from local
  `/home/twasilczyk/projects/hass-core`, checkout HEAD
  `fddf359f543346a9b7cc0068e8e4dc19944dea13`; aioesphomeapi 46.30.0,
  rf-protocols 4.3.0, pytest-homeassistant 0.0.0, pytest 9.2.0.
- `/home/twasilczyk/projects/harf-dooya/.venv`: HA 2026.9.2,
  aioesphomeapi 46.2.0, pytest-homeassistant-custom-component 0.13.365.
  These reproducible dependency pins are in `requirements-test.txt`.
- `/home/twasilczyk/projects/venv-esphome`: ESPHome 2026.10.2. Its installed
  generic receiver schema, CC1101 code, RF proxy, and API forwarding code were
  inspected. Its `IrRfEntity.on_receive` forwards the complete signed timing
  vector with device ID/key through `InfraredRFReceiveEvent`.

The installed HA 2026.9.2 ESPHome RF platform filters for `TRANSMITTER` only.
It has no public native receiver API. The newer local HA has
`async_get_receivers(hass, frequency, modulation)` and
`async_subscribe_receiver(hass, entity_id_or_uuid, callback)`, plus an ESPHome
receiver entity filtering events by `(device_id, key)`. It constructs
`RadioFrequencyReceivedSignal(timings=event.timings)` with default OOK metadata.

Consequently the integration prefers native RX entities in the newer stack,
and otherwise discovers individual `RadioFrequencyInfo` channels using
`list_entities_services()` on the connected ESPHome entry's runtime client.
It subscribes through `subscribe_infrared_rf_receive()`. No separate API
connection, credentials, tuning service or transmit capability is required.

## Executed commands and results

Working directory for all commands below:
`/home/twasilczyk/projects/harf-vevor-weather`.

```sh
/home/twasilczyk/projects/venv-hass/bin/python -m pytest -q --tb=short --show-capture=no --cov=custom_components.harf_vevor_weather --cov-report=term-missing
```

**45 passed**, 94% overall statement coverage (716 statements, 40 uncovered).
The 18 warnings originate in HA/aiohttp/backoff dependencies. No teardown
leaks or failed tests remain.

```sh
/home/twasilczyk/projects/harf-dooya/.venv/bin/python -m pytest -q --tb=short --show-capture=no
```

**43 passed, 2 skipped**. Both skips are native receiver tests on the older HA
version where that API does not exist; ESPHome fallback, flows, entities,
decoder, persistence and lifecycle tests all run there.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/twasilczyk/projects/harf-dooya/.venv/bin/python -m pytest --noconftest -o addopts='' -q tests/test_protocol.py
```

**23 passed** with HA fixtures/plugins disabled; the decoder is imported
directly from its file without importing HA. Two harmless pytest warnings
report asyncio settings because that plugin is intentionally disabled.

```sh
/home/twasilczyk/projects/venv-hass/bin/ruff check .
/home/twasilczyk/projects/venv-hass/bin/ruff format --check .
```

**All checks passed; 22 Python files already formatted.**

```sh
PYTHONPATH=/home/twasilczyk/projects/hass-core /home/twasilczyk/projects/venv-hass/bin/python -m script.hassfest --action validate --integration-path /home/twasilczyk/projects/harf-vevor-weather/custom_components/harf_vevor_weather
```

**1 integration, 0 invalid integrations.** Includes manifest, dependencies,
config flow, translations and generic entity icons.

```sh
/home/twasilczyk/projects/venv-esphome/bin/esphome config examples/cc1101-receiver.yaml
/home/twasilczyk/projects/venv-hass/bin/hass --script check_config -c config
python3 scripts/decode_capture.py /tmp/vevor-fixture-timings.json
git diff --check --cached
```

**All passed.** The ESPHome check validates schema, not compiled firmware or
live RF. The offline JSON fixture was synthesized using `tests.helpers.timings`;
its decoded fields match every supplied payload expectation. HA's checker
installed its missing `colorlog==6.12.0` prerequisite before successfully
checking the local development configuration. No HA server was left running.

The managed sandbox initially blocked asyncio self-pipe sockets and hassfest
multiprocessing sockets. Those commands were rerun with approval outside the
sandbox. The initial blocked processes were stopped. No workaround disabled
the underlying HA lifecycle behavior or replaced it with a fake HA runtime.

## Test coverage that matters

- Native `RadioFrequencyReceivedSignal` and ESPHome `InfraredRFReceiveEvent`
  models deliver signed timing data to the pure decoder.
- Full ESPHome-event → adapter → decoder → coordinator → actual HA entity state
  updates, including the supplied captured payload and battery-low bit.
- Native preference, registry rename, individual ESPHome channel selection,
  wrong-key/device isolation, missing channels, discovery failures, retry,
  connection drop/recovery, entry unload/reload, canceled discovery and stale
  callbacks. Unsubscribe is idempotent.
- UI manual validation, duplicate identities, verified learning with explicit
  confirmation, declined candidates, timeout/abort cleanup, no-receiver retry,
  options, and reconfigure.
- Loaded reconfiguration actually unloads/reloads the integration while keeping
  entity IDs and rain totals; old baselines retain their old station identity.
- Multiple stations on one receiver, invalid checksums, burst duplicates,
  identical measurements 20 seconds later, initial silence and stale timeout,
  receiver disconnect distinction, failed setup and failed platform unload.
- Both polarities, timing jitter, long zero runs, noise, any-offset sync,
  explicit continuation chunks including a split run, truncation/reset/time
  boundaries, malformed input and bounded capture sizes.
- Per-byte wrapping, lux multiplier, 12-bit direction, adjacent-counter wrap,
  type/channel validation, rain carry glitches, zero reset confirmation,
  16-bit rain rollover, replacement baseline and independent station accounting.

## Remaining hardware/API gate

No live node or raw OpenHARF capture was available to this task. No original IQ
recording was used. The mandatory supplied real payload was tested, and all
waveforms/API events used in tests were synthesized from it.

The minimal hardware prerequisite is a CC1101 in 2-FSK asynchronous RX mode,
GDO2 connected to a generic `remote_receiver`, sufficient RMT capture capacity,
and an RX-capable `radio_frequency` proxy that actually forwards signed timings
through ESPHome's API. Merely advertising RX or subscribing successfully is
not evidence that the waveform reaches HA intact.

`idle: 2000us` and `receive_symbols: 512` are starting points only. A legitimate
long zero run can exceed 2 ms; a longer idle can demand more buffer memory.
Neither current event model provides reliable chunk-continuity metadata. This
integration therefore does not concatenate separate capture events to hide
truncation. Hardware/configuration must preserve the frame. The full
[live validation checklist](../README.md#live-validation-and-troubleshooting)
records channel metadata, raw duration/count, valid checksums, packet cadence,
entity updates and disconnect/timeout recovery.

No fundamental limitation was found in the inspected Python APIs' ability to
carry demodulated FSK timings. Their actual delivery from the configured
hardware remains unverified. RF frequency offsets, RMT allocation, filtering
and idle settings must be checked on that hardware. No firmware-specific
weather decoder has been introduced.

## Packaging and release readiness

- Integration directory and manifest both use `harf_vevor_weather`; repository is
  `harf-vevor-weather`. The domain was updated to match the user's corrected
  integration identifier.
- Initial version is `0.1.0`; CI checks version tags against the manifest.
- MIT license, English strings/translation, HACS metadata, Ruff/pytest CI,
  hassfest CI and HACS validation workflow are present.
- HACS's GitHub action was **not executed locally**. Its repository/release
  checks need the hosted repository context. It is configured in CI with only
  the brand-artwork check ignored; no vendor artwork/listing approval is claimed.
- Local install archive `dist/harf_vevor_weather-0.1.0.zip` contains the integration,
  README, license, validation record, generic YAML example and offline capture
  tool. Archive integrity and manifest domain were checked. It excludes
  `__pycache__`, test environments and configuration/credentials.
- The ignored local `config/custom_components/harf_vevor_weather` symlink points at the
  implementation. `config/configuration.yaml` enables default HA features and
  debug logging for this integration. Run
  `/home/twasilczyk/projects/venv-hass/bin/hass -c config`, then add ESPHome and
  the weather integration at `http://localhost:8123`.
- Nothing was committed, pushed, published or released by this task.
