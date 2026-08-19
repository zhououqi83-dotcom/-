#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import uuid
from typing import Any
from urllib import request

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from motion_stream import estimate_motion_duration_ms, load_motion_catalog, plan_motion_frames
from server_utils.simple_yaml import parse_simple_yaml


DEFAULT_HUB_URL = "http://127.0.0.1:8765"
DEFAULT_STREAM_STEP_MS = 33
DEFAULT_MOTION_MAPPING_PATH = Path(__file__).with_name("motion-mapping.yaml")
DEFAULT_ROOT_DIR = Path(__file__).resolve().parent.parent / "viewer" / "digital"


def parse_name_values(items: list[str], label: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Invalid {label} '{item}', expected name=value")
        key, raw_value = item.split("=", 1)
        values[key.strip()] = float(raw_value)
    return values


def load_motion_mapping(path: str | Path) -> dict[str, dict[str, float]]:
    raw = parse_simple_yaml(Path(path).read_text(encoding="utf-8"))
    mappings = raw.get("mappings")
    if not isinstance(mappings, dict):
        raise ValueError("motion-mapping.yaml must contain a top-level 'mappings' object")

    normalized: dict[str, dict[str, float]] = {}
    for source_name, raw_targets in mappings.items():
        if not isinstance(raw_targets, dict):
            raise ValueError(f"mappings.{source_name} must be an object")
        normalized[source_name] = {
            str(target_name): float(scale)
            for target_name, scale in raw_targets.items()
        }
    return normalized


def convert_legacy_values_to_canonical(
    values: dict[str, float],
    mapping: dict[str, dict[str, float]],
) -> dict[str, float]:
    converted: dict[str, float] = {}
    for source_name, value in values.items():
        targets = mapping.get(source_name)
        if not targets:
            continue

        for target_name, scale in targets.items():
            converted[target_name] = converted.get(target_name, 0.0) + float(value) * float(scale)
    return converted


def merge_values(*maps: dict[str, float] | None) -> dict[str, float] | None:
    merged: dict[str, float] = {}
    for value_map in maps:
        if not value_map:
            continue
        for key, value in value_map.items():
            merged[str(key)] = float(value)
    return merged or None


def post_json(base_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def send_stream_frames(
    base_url: str,
    motion_name: str,
    plan: dict[str, Any],
    step_ms: int,
) -> None:
    total_ms = int(plan.get("duration_ms") or 0)
    frames = plan.get("frames") or []
    if not isinstance(frames, list) or not frames:
        raise RuntimeError("Stream plan does not contain frames")
    stream_id = str(uuid.uuid4())
    started = time.perf_counter()

    print(f"[stream] motion={motion_name} duration={total_ms}ms frames={len(frames)} step={step_ms}ms")

    for idx, frame in enumerate(frames):
        payload: dict[str, Any] = {
            "stream_id": stream_id,
            "motion": motion_name,
            **frame,
            "values": frame.get("values") or {},
            "bones": frame.get("bones") or {},
            "reset_missing": frame.get("reset_missing", True),
        }
        post_json(base_url, "/frame", payload)

        if idx == len(frames) - 1:
            continue
        next_frame = frames[idx + 1] or {}
        target_elapsed = float(next_frame.get("t_ms", 0)) / 1000.0
        sleep_s = target_elapsed - (time.perf_counter() - started)
        if sleep_s > 0:
            time.sleep(sleep_s)

    print(json.dumps({
        "stream": {
            "motion": motion_name,
            "frames": len(frames),
            "duration_ms": total_ms,
        }
    }, ensure_ascii=False, indent=2))


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hub-url", default=DEFAULT_HUB_URL, help="Control hub base URL")


def resolve_stream_duration_ms(
    motion: dict[str, Any],
    duration_ms: int | None,
    step_ms: int,
) -> int | None:
    base_duration_ms = int(duration_ms) if duration_ms is not None else estimate_motion_duration_ms(motion)
    if base_duration_ms <= 0:
        return None
    return max(1, int(round(float(base_duration_ms) * float(step_ms) / float(DEFAULT_STREAM_STEP_MS))))


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream sampled motion-library frames to the platform backend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stream_parser = subparsers.add_parser("stream", help="Parse one motion locally and stream sampled frames to the viewer")
    stream_parser.add_argument("motion", help="Motion name from motions.json or motions_th.json")
    stream_parser.add_argument("--duration-ms", type=int, help="Optional runtime override in milliseconds")
    stream_parser.add_argument(
        "--value",
        action="append",
        default=[],
        help="Extra canonical 61-coefficient override, e.g. MouthSmileLeft=0.6",
    )
    stream_parser.add_argument(
        "--blendshape",
        action="append",
        default=[],
        help="Legacy avatar coefficient override, auto-mapped with test/motion-mapping.yaml",
    )
    stream_parser.add_argument(
        "--mapping",
        default=str(DEFAULT_MOTION_MAPPING_PATH),
        help="Legacy avatar->canonical YAML mapping file",
    )
    stream_parser.add_argument(
        "--root-dir",
        default=str(DEFAULT_ROOT_DIR),
        help="Digital root containing src/motions.json and src/motions_th.json",
    )
    stream_parser.add_argument(
        "--step-ms",
        type=int,
        default=DEFAULT_STREAM_STEP_MS,
        help="Playback step in milliseconds. 33 keeps authored speed; smaller is faster, larger is slower",
    )
    add_common_flags(stream_parser)

    args = parser.parse_args()

    if args.command == "stream":
        normalized_step_ms = max(1, int(args.step_ms))
        canonical_values = parse_name_values(args.value, "value") if args.value else None
        legacy_values = parse_name_values(args.blendshape, "blendshape") if args.blendshape else None
        mapping = load_motion_mapping(args.mapping)
        catalog = load_motion_catalog(args.root_dir)
        entry = catalog.get(args.motion)
        if not entry:
            raise SystemExit(f"Unknown motion: {args.motion}")

        plan = plan_motion_frames(
            args.motion,
            entry["motion"],
            duration_ms=resolve_stream_duration_ms(entry["motion"], args.duration_ms, normalized_step_ms),
            legacy_values=legacy_values,
            step_ms=normalized_step_ms,
        )
        for frame in plan.get("frames", []):
            frame["values"] = merge_values(
                convert_legacy_values_to_canonical(frame.get("values", {}), mapping),
                canonical_values,
            ) or {}
        send_stream_frames(
            args.hub_url,
            args.motion,
            plan,
            normalized_step_ms,
        )
        return


if __name__ == "__main__":
    main()
