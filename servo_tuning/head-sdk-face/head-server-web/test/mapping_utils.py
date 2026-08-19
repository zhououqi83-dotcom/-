from __future__ import annotations

from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MAPPING_PATH = BASE_DIR / "motion-mapping.yaml"
CORE_DIR = BASE_DIR.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from server_utils.simple_yaml import parse_simple_yaml


def load_motion_mapping(path: str | Path = DEFAULT_MAPPING_PATH) -> dict[str, dict[str, float]]:
    raw = parse_simple_yaml(Path(path).read_text(encoding="utf-8"))
    mappings = raw.get("mappings")
    if not isinstance(mappings, dict):
        raise ValueError("motion-mapping.yaml must contain a top-level 'mappings' object")

    normalized: dict[str, dict[str, float]] = {}
    for source_name, raw_targets in mappings.items():
        if not isinstance(raw_targets, dict):
            continue
        normalized[str(source_name)] = {
            str(target_name): float(scale)
            for target_name, scale in raw_targets.items()
        }
    return normalized


def convert_values_to_canonical(
    values: dict[str, float],
    mapping: dict[str, dict[str, float]],
) -> dict[str, float]:
    converted: dict[str, float] = {}

    for source_name, raw_value in (values or {}).items():
        value = float(raw_value)
        if source_name in {"HeadPitch", "HeadYaw", "HeadRoll"}:
            converted[source_name] = value
            continue

        targets = mapping.get(source_name)
        if not targets:
            pascal_name = f"{source_name[:1].upper()}{source_name[1:]}" if source_name else source_name
            converted[pascal_name] = value
            continue

        for target_name, scale in targets.items():
            converted[target_name] = converted.get(target_name, 0.0) + value * float(scale)

    return converted


def build_stream_payload(
    values: dict[str, float],
    *,
    bones: dict | None = None,
    mapping: dict[str, dict[str, float]] | None = None,
) -> dict:
    resolved_mapping = mapping or load_motion_mapping()
    payload = {
        "values": convert_values_to_canonical(values, resolved_mapping),
    }
    if bones:
        payload["bones"] = bones
    else:
        payload["bones"] = {}
    return payload
