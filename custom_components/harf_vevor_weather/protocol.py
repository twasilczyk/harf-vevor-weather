"""Original, dependency-free R56 payload and demodulated NRZ timing decoder.

Durations are GPIO levels in microseconds, never RF samples or OOK symbols.
The transport must explicitly mark continuation chunks; ordinary receive events
are separate captures. No partial capture survives a reset or a 250 ms gap.
"""

from dataclasses import dataclass
from time import monotonic
import logging


_LOGGER = logging.getLogger(__name__)
BIT_PERIOD_US = 1_000_000 / 11260
SYNC = "1100101001010100"
MAX_TIMINGS = 8192
MAX_BITS = 4096
MAX_CAPTURE_US = 250_000


@dataclass(frozen=True, slots=True)
class Packet:
    """One checksum-verified measurement, in native units."""

    payload: bytes
    sensor_id: int
    channel: int
    counter: int
    battery_low: bool
    temperature: float
    humidity: int
    wind_speed: float
    wind_gust: float
    wind_direction: int
    rain_tips: int
    uv_index: int
    illuminance: int

    @property
    def rain_mm(self) -> float:
        """Unfiltered absolute counter, not interval rainfall."""
        return self.rain_tips * 0.233


def decode_payload(payload: bytes) -> Packet | None:
    """Validate and decode 21 bytes; unsupported type/channel pairs are rejected.

    The reference's supported R56/R53 weather format has byte 1 == 0. Other
    channel/type combinations have no verified field layout and are not guessed.
    Offsets apply independently to each byte, including 0 -> 255 wrapping.
    """
    if not isinstance(payload, bytes) or len(payload) != 21:
        return None
    if payload[:2] != b"\xaa\x00":
        return None
    if sum(payload[:19]) & 255 != payload[19]:
        return None
    if payload[20] != (payload[18] + 1) & 255:
        return None

    def corrected_word(index: int) -> int:
        return (((payload[index] - 1) & 255) << 8) | ((payload[index + 1] - 1) & 255)

    light = corrected_word(16)
    return Packet(
        payload=payload,
        sensor_id=int.from_bytes(payload[2:4], "big"),
        channel=payload[1] & 15,
        counter=payload[18],
        battery_low=bool(payload[4] & 128),
        temperature=(int.from_bytes(payload[5:7], "big") - 500) / 10,
        humidity=payload[7],
        wind_speed=corrected_word(8) / 8.333,
        wind_gust=payload[10] / 1.25,
        wind_direction=corrected_word(11) & 4095,
        rain_tips=corrected_word(13),
        uv_index=(payload[15] & 31) - 1,
        illuminance=(light & 32767) * (10 if light & 32768 else 1),
    )


class TimingDecoder:
    """Bounded capture decoder, with optional explicitly contiguous chunks.

    Both current HA and ESPHome events contain a whole capture and have no chunk
    sequence identifier. Feed those with the default continuation=False. A future
    transport with a contiguous-chunk contract may opt in. A missing RMT idle gap
    cannot be recovered by concatenating independent events.
    """

    def __init__(self) -> None:
        self.rejected = 0
        self.reset()

    def reset(self) -> None:
        """Discard partial runs and already emitted capture offsets."""
        self._raw: list[int] = []
        self._emitted: set[tuple[int, bytes]] = set()
        self._last_time: float | None = None

    def feed(
        self,
        timings: list[int],
        *,
        continuation: bool = False,
        now: float | None = None,
    ) -> list[Packet]:
        """Return all verified copies in this capture, in receive order."""
        now = monotonic() if now is None else now
        if (
            not continuation
            or self._last_time is None
            or not 0 <= now - self._last_time <= 0.25
        ):
            self.reset()
        if (
            not isinstance(timings, (list, tuple))
            or not timings
            or len(timings) + len(self._raw) > MAX_TIMINGS
            or any(type(value) is not int or value == 0 for value in timings)
        ):
            self.reset()
            _LOGGER.debug(
                "R56 reject: container=%s len=%d raw_len=%d "
                "first_type=%s zero_count=%d non_int_count=%d",
                type(timings).__name__,
                len(timings),
                len(self._raw),
                type(timings[0]).__name__ if timings else "empty",
                sum(v == 0 for v in timings),
                sum(type(v) is not int for v in timings),
            )
            return []
        raw = self._raw.copy()
        for value in timings:
            if raw and (raw[-1] > 0) == (value > 0):
                raw[-1] += value
            else:
                raw.append(value)
        if sum(abs(value) for value in raw) > MAX_CAPTURE_US:
            self.reset()
            _LOGGER.debug("R56 2 protocol loaded ret2")
            return []
        self._raw = raw
        self._last_time = now
        bits = []
        size = 0
        for value in raw:
            count = max(1, int(abs(value) / BIT_PERIOD_US + 0.5))
            # A run longer than an entire payload is a boundary, never something
            # to remove and then join the surrounding partial frames across.
            if count > 256:
                bits.append("|")
                continue
            size += count
            if size > MAX_BITS:
                self.reset()
                _LOGGER.debug("R56 2 protocol loaded ret3")
                return []
            bits.append(("1" if value > 0 else "0") * count)
        stream = "".join(bits)

        _LOGGER.debug(
            "R56 bits=%d sync=%d inverted_sync=%d",
            len(stream),
            stream.count(SYNC),
            stream.translate(str.maketrans("01", "10")).count(SYNC),
        )

        found: list[tuple[int, Packet]] = []
        for inverted in (False, True):
            view = stream.translate(str.maketrans("01", "10")) if inverted else stream
            offset = 0
            while (offset := view.find(SYNC, offset)) != -1:
                start = offset + 16
                body = view[start : start + 168]
                if len(body) == 168 and "|" not in body:
                    payload = int(body, 2).to_bytes(21, "big")
                    identity = (offset, payload)
                    if identity not in self._emitted:
                        self._emitted.add(identity)
                        if (packet := decode_payload(payload)) is not None:
                            found.append((offset, packet))
                        else:
                            self.rejected += 1
                offset += 1
        return [packet for _, packet in sorted(found, key=lambda item: item[0])]


def decode_timings(timings: list[int]) -> list[Packet]:
    """Decode one complete capture without maintaining state."""
    return TimingDecoder().feed(timings)
