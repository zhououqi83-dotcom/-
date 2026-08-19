from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any


SKIP_KEYS = {"gesture", "headMove"}
FADE_RAMP_MS = 300


def load_motion_catalog(root_dir: str | Path) -> dict[str, dict[str, Any]]:
    root_path = Path(root_dir)
    sources = [
        ("motions", root_path / "src" / "motions.json"),
        ("motions_th", root_path / "src" / "motions_th.json"),
    ]
    catalog: dict[str, dict[str, Any]] = {}

    for source_name, path in sources:
        motions = json.loads(path.read_text(encoding="utf-8"))
        for name, motion in motions.items():
            catalog[str(name)] = {
                "name": str(name),
                "source": source_name,
                "motion": motion,
            }

    return catalog


def estimate_motion_duration_ms(motion: dict[str, Any] | None) -> int:
    if not motion or "dt" not in motion:
        return 0

    frames = motion["dt"] if isinstance(motion["dt"], list) else [motion["dt"]]
    total = 0.0
    for frame in frames:
        if isinstance(frame, list):
            total += (float(frame[0]) + float(frame[1])) / 2.0
        else:
            total += float(frame)
    return int(total)


def scale_motion_timings(dt: Any, target_duration_ms: int) -> list[Any]:
    normalized_target = int(round(float(target_duration_ms)))
    if normalized_target <= 0:
        raise ValueError("target_duration_ms must be greater than 0")
    if not dt:
        return [normalized_target]

    source_frames = dt if isinstance(dt, list) else [dt]
    source_total = estimate_motion_duration_ms({"dt": source_frames}) or normalized_target
    ratio = normalized_target / source_total
    scaled = []

    for frame in source_frames:
        if isinstance(frame, list):
            scaled.append([max(1, int(round(float(value) * ratio))) for value in frame])
        else:
            scaled.append(max(1, int(round(float(frame) * ratio))))

    return scaled


def _coerce_float_list(values: Any) -> list[float]:
    if isinstance(values, (int, float)) and math.isfinite(values):
        return [float(values)]
    if not isinstance(values, list):
        return []

    result: list[float] = []
    for item in values:
        if isinstance(item, (int, float)) and math.isfinite(item):
            result.append(float(item))
    return result


def _expand_curve_points(values: list[float], segment_count: int) -> list[float]:
    if segment_count <= 0:
        return [float(values[-1])] if values else [0.0]
    if not values:
        return [0.0] * (segment_count + 1)
    if len(values) >= segment_count + 1:
        return [float(value) for value in values[: segment_count + 1]]
    if len(values) == segment_count:
        return [0.0, *[float(value) for value in values]]
    if len(values) == segment_count - 1:
        tail = [float(value) for value in values]
        return [0.0, *tail, tail[-1]]
    if len(values) == 1:
        value = float(values[0])
        if segment_count == 1:
            return [value, value]
        if segment_count == 2:
            return [0.0, value, value]
        return [0.0, *[value for _ in range(segment_count - 1)], 0.0]

    expanded = [0.0, *[float(value) for value in values[:segment_count]]]
    while len(expanded) < segment_count + 1:
        expanded.append(expanded[-1])
    return expanded[: segment_count + 1]


def _expand_step_points(values: Any, segment_count: int) -> list[Any]:
    if not isinstance(values, list):
        return [values for _ in range(segment_count + 1)]
    if len(values) >= segment_count + 1:
        return values[: segment_count + 1]
    if len(values) == segment_count:
        return [None, *values]
    if len(values) == segment_count - 1:
        return [None, *values, values[-1]]
    if len(values) == 1:
        return [None, *[values[0] for _ in range(segment_count)]]

    expanded = [None, *values[:segment_count]]
    while len(expanded) < segment_count + 1:
        expanded.append(expanded[-1] if expanded[-1] is not None else None)
    return expanded[: segment_count + 1]


def build_timeline_points(motion: dict[str, Any] | None = None) -> list[int]:
    if not motion or "dt" not in motion:
        return [0]

    frames = motion["dt"] if isinstance(motion["dt"], list) else [motion["dt"]]
    points = [0]
    total = 0

    for frame in frames:
        if isinstance(frame, list):
            total += int(round((float(frame[0]) + float(frame[1])) / 2.0))
        else:
            total += int(round(float(frame)))
        points.append(total)

    return points


def sample_numeric_values(motion: dict[str, Any] | None = None, t_ms: int = 0) -> dict[str, float]:
    points = build_timeline_points(motion)
    vs = motion.get("vs") if motion else None
    if len(points) <= 1 or not isinstance(vs, dict):
        return {}

    segment_count = len(points) - 1
    clamped_t = max(0, min(int(round(t_ms)), points[-1]))
    frame_values: dict[str, float] = {}

    for key, raw in vs.items():
        if key in SKIP_KEYS:
            continue

        curve = _coerce_float_list(raw)
        if not curve:
            continue

        values = _expand_curve_points(curve, segment_count)
        if clamped_t <= points[0]:
            frame_values[str(key)] = values[0]
            continue
        if clamped_t >= points[-1]:
            frame_values[str(key)] = values[-1]
            continue

        left_index = 0
        for index in range(segment_count):
            if points[index] <= clamped_t <= points[index + 1]:
                left_index = index
                break

        left_t = points[left_index]
        right_t = points[left_index + 1]
        span = max(1, right_t - left_t)
        ratio = (clamped_t - left_t) / span
        left_value = values[left_index]
        right_value = values[left_index + 1]
        frame_values[str(key)] = left_value + (right_value - left_value) * ratio

    return frame_values


def sample_gesture(motion: dict[str, Any] | None = None, t_ms: int = 0) -> Any:
    gesture_values = motion.get("vs", {}).get("gesture") if motion else None
    if gesture_values is None:
        return None

    points = build_timeline_points(motion)
    segment_count = max(1, len(points) - 1)
    expanded = _expand_step_points(gesture_values, segment_count)
    clamped_t = max(0, min(int(round(t_ms)), points[-1] if points else 0))
    current = expanded[0]

    for index in range(1, len(points)):
        if clamped_t < points[index]:
            break
        current = expanded[index]

    return current


def sample_overlay_bones(motion: dict[str, Any] | None = None, t_ms: int = 0) -> dict[str, dict[str, list[float]]]:
    overlay = motion.get("_overlay") if motion else None
    if not isinstance(overlay, dict):
        return {}

    bones = overlay.get("bones") or {}
    delay_ms = max(0, int(round(float(overlay.get("delay", 0) or 0))))
    duration_ms = max(
        1,
        int(round(float(overlay.get("duration") or estimate_motion_duration_ms(motion) or 1))),
    )
    elapsed_ms = int(round(t_ms)) - delay_ms
    active = 0 <= elapsed_ms <= duration_ms
    sampled: dict[str, dict[str, list[float]]] = {}

    for bone_name, config in bones.items():
        quaternion = [0.0, 0.0, 0.0]
        position = [0.0, 0.0, 0.0]

        if active and isinstance(config, dict):
            elapsed = float(elapsed_ms)
            fade_in = min(elapsed / FADE_RAMP_MS, 1.0)
            fade_out = min((duration_ms - elapsed) / FADE_RAMP_MS, 1.0)
            envelope = max(0.0, fade_in * fade_out)
            progress = min(max(elapsed / duration_ms, 0.0), 1.0)

            if config.get("custom") == "jump":
                position[1] = math.sin(progress * math.pi) * 0.12
            else:
                time_s = elapsed / 1000.0
                freq = float(config.get("freq", 0) or 0)
                amp = config.get("amp") if isinstance(config.get("amp"), list) else [0, 0, 0]
                phase = float(config.get("phase", 0) or 0)
                base = math.sin(time_s * freq) * envelope
                quaternion[0] = base * float(amp[0] if len(amp) > 0 else 0)
                quaternion[1] = base * float(amp[1] if len(amp) > 1 else 0)
                quaternion[2] = math.sin(time_s * freq + phase) * float(amp[2] if len(amp) > 2 else 0) * envelope

        sampled[str(bone_name)] = {
            "quaternion": quaternion,
            "position": position,
        }

    return sampled


def clone_motion_for_stream(
    motion: dict[str, Any] | None,
    *,
    duration_ms: int | None = None,
    legacy_values: dict[str, float] | None = None,
) -> dict[str, Any]:
    cloned = deepcopy(motion or {})

    if duration_ms:
        cloned["dt"] = scale_motion_timings(cloned.get("dt"), int(duration_ms))

    if duration_ms and isinstance(cloned.get("_overlay"), dict):
        source_total = estimate_motion_duration_ms(motion or {}) or int(duration_ms)
        ratio = float(duration_ms) / float(source_total)
        if cloned["_overlay"].get("delay") is not None:
            cloned["_overlay"]["delay"] = max(0, int(round(float(cloned["_overlay"]["delay"]) * ratio)))
        if cloned["_overlay"].get("duration") is not None:
            cloned["_overlay"]["duration"] = max(1, int(round(float(cloned["_overlay"]["duration"]) * ratio)))

    if legacy_values:
        vs = cloned.get("vs")
        if not isinstance(vs, dict):
            vs = {}
            cloned["vs"] = vs
        for key, value in legacy_values.items():
            vs[str(key)] = [float(value)]

    return cloned


def iter_stream_points(total_ms: int, step_ms: int) -> list[int]:
    if total_ms <= 0:
        return [0]

    points = list(range(0, total_ms + 1, step_ms))
    if points[-1] != total_ms:
        points.append(total_ms)
    return points


def plan_motion_frames(
    motion_name: str,
    motion: dict[str, Any],
    *,
    duration_ms: int | None = None,
    legacy_values: dict[str, float] | None = None,
    step_ms: int = 33,
) -> dict[str, Any]:
    stream_motion = clone_motion_for_stream(
        motion,
        duration_ms=duration_ms,
        legacy_values=legacy_values,
    )
    total_ms = estimate_motion_duration_ms(stream_motion)
    normalized_step_ms = max(1, int(round(float(step_ms or 33))))
    points = iter_stream_points(total_ms, normalized_step_ms)

    frames: list[dict[str, Any]] = []
    for index, t_ms in enumerate(points):
        frame = {
            "t_ms": t_ms,
            "duration_ms": normalized_step_ms,
            "is_first": index == 0,
            "is_last": index == len(points) - 1,
            "values": sample_numeric_values(stream_motion, t_ms),
            "bones": sample_overlay_bones(stream_motion, t_ms),
            "reset_missing": True,
        }
        gesture = sample_gesture(stream_motion, t_ms)
        if gesture is not None:
            frame["gesture"] = gesture
        frames.append(frame)

    return {
        "motion": motion_name,
        "duration_ms": total_ms,
        "step_ms": normalized_step_ms,
        "frames": frames,
    }
