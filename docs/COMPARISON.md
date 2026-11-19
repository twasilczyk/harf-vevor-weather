# Comparison with the other HARF integrations

Compared the current default-branch `config_flow.py`, `receiver.py` and
`__init__.py` in all three repositories on 2026-11-18.

| Area | Dooya / Insoma / Rhine | VEVOR |
| --- | --- | --- |
| Role | RF appliance control, with an RF transmitter selected during setup | Receive-only weather measurements |
| Identification | Learn a remote/address from received commands or enter its code/address manually | Discover a checksum-verified periodic broadcast or enter a station ID |
| Receiver requirement | Optional, supporting observation/learning alongside transmission | Required, with setup retry if a subscription cannot be established |
| Receiver abstraction | `FrameSource`, separate from the device protocol | Same separation, adding native HA RX preference and explicit ESPHome device-ID/key selection |
| Lifecycle | Config-entry and ESPHome device-update listeners; detach on unload | Same pattern, with cancellation and stale-callback guards, plus receiver and signal-loss status |
| Reconfigure | Replace settings while retaining existing entity identities | Replace receiver or station ID while retaining entities, history and cumulative rain accounting |
| Persistent state | Rhine persists appliance state and flushes only after successful platform unload | Rain accounting follows that storage/unload pattern; weather freshness is never restored as live data |
| Entities | Covers/buttons/events/numbers, switches/selects, or fans/lights | Weather sensors, battery-low binary sensor and reception diagnostics |

The closest receiver lifecycle reference is
[Dooya's receiver](https://github.com/twasilczyk/harf-dooya/blob/main/custom_components/harf_dooya/receiver.py),
which discovers raw ESPHome RF capability metadata and guards callbacks across
reconnects. VEVOR retains those ideas while selecting one specific RF channel
and supporting the public HA receiver API. It requires a successful receiver
subscription because reception is its entire purpose.

The setup conventions come from the
[Dooya](https://github.com/twasilczyk/harf-dooya/blob/main/custom_components/harf_dooya/config_flow.py),
[Insoma](https://github.com/twasilczyk/harf-insoma/blob/main/custom_components/harf_insoma/config_flow.py)
and [Rhine](https://github.com/twasilczyk/harf-rhine/blob/main/custom_components/harf_rhine/config_flow.py)
flows: receiver selection, asynchronous listening with a timeout, manual identity
entry, duplicate handling and reconfiguration. VEVOR uses discovery terminology
throughout its flow, translation keys and tests.

**Discovery is entirely passive.** The station broadcasts without pairing.
Selecting “Discover station” listens for its normal packet; confirmation only
stores the station ID/channel filter in HA. No transmitter selection, pairing
command, programming sequence or button press is involved. Explicit confirmation
remains useful because a neighbor's station may be heard first.

The state persistence and successful-unload-before-cleanup convention matches
[Rhine's entry lifecycle](https://github.com/twasilczyk/harf-rhine/blob/main/custom_components/harf_rhine/__init__.py).
VEVOR's station measurements and rain rules remain specific to this protocol;
appliance command/state-estimation behavior is not needed.
