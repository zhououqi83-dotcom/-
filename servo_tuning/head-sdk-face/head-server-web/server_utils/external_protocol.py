from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from server_utils.blendshape_mapping import normalize_incoming_values


RESERVED_EXTERNAL_FRAME_KEYS = {
    "avatar_id",
    "blendshapes",
    "bones",
    "detail",
    "duration_ms",
    "gesture",
    "interrupt",
    "is_first",
    "is_last",
    "library",
    "motion",
    "ok",
    "payload",
    "received_at",
    "request_id",
    "reset_missing",
    "sent_at",
    "status",
    "stream_id",
    "t_ms",
    "track",
    "type",
    "values",
    "version",
}


def _is_plain_object(value) -> bool:
    return isinstance(value, dict)


def _unwrap_external_payload(raw_payload=None) -> dict:
    payload = raw_payload or {}
    if not _is_plain_object(payload):
        raise ValueError("external frame must be a JSON object")
    nested_payload = payload.get("payload")
    if _is_plain_object(nested_payload):
        return _unwrap_external_payload(nested_payload)
    return payload


def _extract_top_level_values(frame: dict) -> dict:
    values: dict = {}
    for name, value in frame.items():
        if name in RESERVED_EXTERNAL_FRAME_KEYS:
            continue
        values[name] = value
    return values


def normalize_external_bones(bones) -> dict:
    if bones is None:
        return {}
    if not _is_plain_object(bones):
        raise ValueError("bones must be an object")
    return deepcopy(bones)


def normalize_external_gesture(gesture):
    if gesture is None:
        return None
    if not isinstance(gesture, list) or not gesture:
        raise ValueError("gesture must be an array like [name, duration, isRight]")

    name = gesture[0]
    if not isinstance(name, str) or not name.strip():
        raise ValueError("gesture[0] must be a non-empty string")

    normalized = [name.strip()]

    duration = gesture[1] if len(gesture) > 1 else None
    if duration in (None, ""):
        normalized.append(None)
    else:
        try:
            numeric_duration = round(float(duration))
        except (TypeError, ValueError):
            raise ValueError("gesture[1] must be a positive number or null") from None
        if numeric_duration <= 0:
            raise ValueError("gesture[1] must be a positive number or null")
        normalized.append(int(numeric_duration))

    if len(gesture) > 2:
        normalized.append(bool(gesture[2]))

    return normalized


def _normalize_optional_string(value, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalize_optional_integer(value, field_name: str, minimum: int = 0) -> int | None:
    if value in (None, ""):
        return None
    try:
        numeric = round(float(value))
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer >= {minimum}") from None
    if numeric < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")
    return int(numeric)


def _normalize_optional_boolean(value, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def build_external_frame(raw_payload=None, *, motion_mapping: dict | None = None, coefficient_catalog: dict | None = None) -> dict:
    frame = _unwrap_external_payload(raw_payload or {})
    raw_values = frame.get("values")
    if raw_values is None:
        raw_values = frame.get("blendshapes")
    if raw_values is None:
        raw_values = _extract_top_level_values(frame)

    normalized_frame = {
        "values": normalize_incoming_values(
            raw_values if raw_values else {},
            reverse_mapping=motion_mapping,
            catalog=coefficient_catalog,
        ) if raw_values else {},
        "bones": normalize_external_bones(frame.get("bones")),
        "reset_missing": frame.get("reset_missing") is not False,
    }

    if "gesture" in frame:
        normalized_frame["gesture"] = normalize_external_gesture(frame.get("gesture"))

    stream_id = _normalize_optional_string(frame.get("stream_id"), "stream_id")
    if stream_id is not None:
        normalized_frame["stream_id"] = stream_id

    motion_name = _normalize_optional_string(frame.get("motion"), "motion")
    if motion_name is not None:
        normalized_frame["motion"] = motion_name

    t_ms = _normalize_optional_integer(frame.get("t_ms"), "t_ms", minimum=0)
    if t_ms is not None:
        normalized_frame["t_ms"] = t_ms

    duration_ms = _normalize_optional_integer(frame.get("duration_ms"), "duration_ms", minimum=1)
    if duration_ms is not None:
        normalized_frame["duration_ms"] = duration_ms

    is_first = _normalize_optional_boolean(frame.get("is_first"), "is_first")
    if is_first is not None:
        normalized_frame["is_first"] = is_first

    is_last = _normalize_optional_boolean(frame.get("is_last"), "is_last")
    if is_last is not None:
        normalized_frame["is_last"] = is_last

    return normalized_frame
