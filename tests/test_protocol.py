"""Protocol regressions, independent of Home Assistant imports."""

import importlib.util
import sys
from pathlib import Path

import pytest

from .helpers import FIXTURE, payload, timings

# Loading the file directly also works with plain pytest, without HA installed.
spec = importlib.util.spec_from_file_location(
    "vevor_protocol",
    Path(__file__).parents[1] / "custom_components/harf_vevor_weather/protocol.py",
)
protocol = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = protocol
spec.loader.exec_module(protocol)
decode_payload = protocol.decode_payload
decode_timings = protocol.decode_timings
TimingDecoder = protocol.TimingDecoder


def test_captured_us_r56():
    frame = decode_payload(FIXTURE)
    assert frame is not None
    assert (frame.sensor_id, frame.channel, frame.counter) == (0x7203, 0, 0x85)
    assert (frame.temperature, frame.humidity) == (18.3, 57)
    assert (frame.wind_speed, frame.wind_gust, frame.wind_direction) == (0, 0, 261)
    assert frame.rain_mm == 0.233
    assert (frame.uv_index, frame.illuminance, frame.battery_low) == (0, 107, False)
    assert FIXTURE[19] == sum(FIXTURE[:19]) & 255 == 0x21


@pytest.mark.parametrize("inverse", [False, True])
@pytest.mark.parametrize("jitter", [0, 20, 30])
def test_nrz_noise_copies_polarities(inverse, jitter):
    raw = (
        [17, -41, 230, -100_000]
        + timings(inverse=inverse, jitter=jitter, copies=2)
        + [11, -19]
    )
    frames = decode_timings(raw)
    assert [frame.payload for frame in frames] == [FIXTURE, FIXTURE]


@pytest.mark.parametrize("cut", [1, 41, 70, 100])
def test_explicit_chunks(cut):
    raw = timings()
    decoder = TimingDecoder()
    assert decoder.feed(raw[:cut], now=1) == []
    assert decoder.feed(raw[cut:], continuation=True, now=1.01)[0].payload == FIXTURE
    assert decoder.feed([89, -89], continuation=True, now=1.02) == []


def test_chunk_mid_run_and_boundaries():
    raw = timings()
    index = 70
    first = int(raw[index] * 0.4)
    second = raw[index] - first
    decoder = TimingDecoder()
    assert decoder.feed(raw[:index] + [first], now=1) == []
    assert (
        decoder.feed([second] + raw[index + 1 :], continuation=True, now=1.01)[
            0
        ].payload
        == FIXTURE
    )
    for mode in ("new_event", "reset", "time_gap"):
        decoder.feed(raw[:70], now=1)
        if mode == "reset":
            decoder.reset()
        assert (
            decoder.feed(
                raw[70:],
                continuation=mode != "new_event",
                now=2 if mode == "time_gap" else 1.01,
            )
            == []
        )


@pytest.mark.parametrize(
    "raw", [[], [0], [True], [1.2], ["89"], None, [10**20], [89, -89] * 5000]
)
def test_malformed_and_bounded(raw):
    assert decode_timings(raw) == []


def test_integrity_and_supported_type():
    assert decode_payload(FIXTURE[:-1]) is None
    assert decode_payload(FIXTURE + b"\x00") is None
    bad = bytearray(FIXTURE)
    bad[7] ^= 1
    assert decode_payload(bytes(bad)) is None
    bad = bytearray(FIXTURE)
    bad[20] ^= 1
    assert decode_payload(bytes(bad)) is None
    assert decode_payload(payload(b0=0)) is None
    assert decode_payload(payload(b1=1)) is None
    assert decode_payload(payload(b1=16)) is None
    assert decode_payload(payload(b18=255)).counter == 255


def test_byte_offsets_flags_and_zero_runs():
    frame = decode_payload(
        payload(b8=0, b9=0, b11=0xF2, b12=0, b13=0, b14=0, b16=0x81, b17=0, b4=0x9C)
    )
    assert frame.wind_speed == 65535 / 8.333
    assert frame.wind_direction == 511
    assert frame.rain_tips == 65535
    assert frame.illuminance == 2550
    assert frame.battery_low
    # Encoded zeros spanning fields must not be mistaken for an idle gap.
    data = payload(b8=0, b9=0, b10=0, b11=0, b12=0, b13=0, b14=0)
    raw = timings(data)
    assert max(abs(x) for x in raw) > 4000
    assert decode_timings(raw)[0].payload == data


def test_bad_copy_then_good_copy_and_gap_not_joined():
    bad = bytearray(FIXTURE)
    bad[19] ^= 1
    assert [p.payload for p in decode_timings(timings(bytes(bad)) + timings())] == [
        FIXTURE
    ]
    raw = timings()
    assert decode_timings(raw[:70] + [-30_000] + raw[70:]) == []
