"""Reset and byte-carry regressions for long-term statistics."""

from custom_components.harf_vevor_weather.rain import RainTracker


def test_transient_byte_carry_and_zero_confirmation():
    rain = RainTracker()
    assert rain.update(300, 0) == 69.9
    for t in (20, 40, 60):
        assert rain.update(44, t) == 69.9  # Even repeated -256 is impossible.
    assert rain.update(301, 80) == 70.133
    assert rain.update(0, 100) == 70.133
    assert rain.update(0, 100.03) == 70.133  # Same burst does not confirm.
    assert rain.update(0, 120) == 70.133
    assert rain.raw == 301
    rain.update(0, 140)
    assert rain.raw == 0
    assert rain.update(1, 160) == 70.366


def test_counter_rollover_and_separate_instances():
    rain, other = RainTracker(), RainTracker()
    rain.update(65535, 0)
    rain.update(0, 20)
    rain.update(1, 40)
    rain.update(2, 60)
    assert rain.total == 65538
    assert rain.raw == 2
    assert other.update(1, 0) == 0.233
    rain.replace_station()
    before = rain.total
    rain.update(500, 80)
    assert rain.total == before
    rain.update(501, 100)
    assert rain.total == before + 1


def test_pending_reset_cancelled_by_recovery():
    rain = RainTracker()
    for raw, now in ((256, 0), (0, 20), (256, 40), (0, 60), (0, 80)):
        rain.update(raw, now)
    assert rain.raw == 256
    assert rain.total == 256
