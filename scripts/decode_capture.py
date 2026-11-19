"""Inspect a JSON signed-timing capture offline, using only Python's stdlib."""

import argparse
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    if args.capture.stat().st_size > 1_000_000:
        parser.error("capture exceeds 1 MB diagnostic limit")
    spec = importlib.util.spec_from_file_location(
        "vevor_protocol",
        Path(__file__).resolve().parents[1]
        / "custom_components/harf_vevor_weather/protocol.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    frames = module.decode_timings(json.loads(args.capture.read_text()))
    for frame in frames:
        result = asdict(frame)
        result["payload"] = frame.payload.hex(" ")
        result["sensor_id"] = f"0x{frame.sensor_id:04X}"
        result["rain_mm"] = frame.rain_mm
        print(json.dumps(result))
    if not frames:
        parser.exit(1, "No checksum-verified R56 frames found.\n")


if __name__ == "__main__":
    main()
