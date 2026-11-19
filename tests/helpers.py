"""Synthetic waveform generator; no recorded live timings are implied."""

import random
from itertools import groupby

FIXTURE = bytes.fromhex(
    "AA 00 72 03 1C 02 AB 39 01 01 00 02 06 01 02 01 01 6C 85 21 86"
)


def payload(**changes):
    """Modify fields by byte index and regenerate integrity bytes."""
    data = bytearray(FIXTURE)
    for index, value in changes.items():
        data[int(index.removeprefix("b"))] = value
    data[19] = sum(data[:19]) & 255
    data[20] = (data[18] + 1) & 255
    return bytes(data)


def timings(data=FIXTURE, *, inverse=False, jitter=0, copies=1):
    """Encode full preamble + sync + payload to signed high/low runs."""
    bits = "".join(f"{byte:08b}" for byte in b"\xaa\xaa\xaa\xca\xca\x54" + data)
    bits = (bits + "01010101") * copies
    randomizer = random.Random(42)
    result = []
    for bit, run in groupby(bits):
        duration = round(len(list(run)) * (1_000_000 / 11260))
        duration += randomizer.randint(-jitter, jitter)
        result.append(duration * (1 if (bit == "1") != inverse else -1))
    return result
