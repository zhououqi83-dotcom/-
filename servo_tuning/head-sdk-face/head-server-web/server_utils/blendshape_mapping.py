from __future__ import annotations


def _is_plain_object(value) -> bool:
    return isinstance(value, dict)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _normalize_number(value, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    return numeric


def _normalize_target_map(raw_targets, field_name: str) -> dict[str, float]:
    if raw_targets is None:
        return {}
    if not _is_plain_object(raw_targets):
        raise ValueError(f"{field_name} must be an object of numeric scales")

    targets: dict[str, float] = {}
    for target, value in raw_targets.items():
        try:
            scale = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field_name}.{target} must be a finite number") from None
        targets[str(target)] = scale
    return targets


def _normalize_coefficient_entry(name: str, raw_entry=None) -> dict:
    entry = raw_entry if _is_plain_object(raw_entry) else {}
    minimum = _normalize_number(entry.get("min"), 0.0)
    maximum = _normalize_number(entry.get("max"), 1.0)
    normalized_min = min(minimum, maximum)
    normalized_max = max(minimum, maximum)

    group = str(entry.get("group") or "").strip() or "face"
    step = max(0.001, _normalize_number(entry.get("step"), 0.01))

    return {
        "name": name,
        "group": group,
        "min": normalized_min,
        "max": normalized_max,
        "step": step,
        "forwardToRobot": entry.get("forwardToRobot") is not False,
        "targets": _normalize_target_map(entry.get("targets"), f"coefficients.{name}.targets"),
        "positiveTargets": _normalize_target_map(
            entry.get("positiveTargets"),
            f"coefficients.{name}.positiveTargets",
        ),
        "negativeTargets": _normalize_target_map(
            entry.get("negativeTargets"),
            f"coefficients.{name}.negativeTargets",
        ),
    }


def _normalize_reverse_entry(name: str, raw_entry=None) -> dict[str, float]:
    return _normalize_target_map(raw_entry, f"mappings.{name}")


def normalize_coefficient_catalog(raw_config=None) -> dict:
    raw = raw_config or {}
    raw_coefficients = raw.get("coefficients") if _is_plain_object(raw) else {}
    if not _is_plain_object(raw_coefficients):
        raw_coefficients = raw if _is_plain_object(raw) else {}

    coefficients = [
        _normalize_coefficient_entry(str(name), raw_entry)
        for name, raw_entry in raw_coefficients.items()
    ]

    by_name = {entry["name"]: entry for entry in coefficients}
    robot_names = {
        entry["name"]
        for entry in coefficients
        if entry["forwardToRobot"]
    }
    return {
        "coefficients": coefficients,
        "byName": by_name,
        "robotNames": robot_names,
    }


def normalize_reverse_mapping(raw_config=None) -> dict:
    raw = raw_config or {}
    raw_mappings = raw.get("mappings") if _is_plain_object(raw) else {}
    if not _is_plain_object(raw_mappings):
        raw_mappings = raw if _is_plain_object(raw) else {}

    mappings = {
        str(name): _normalize_reverse_entry(str(name), raw_entry)
        for name, raw_entry in raw_mappings.items()
    }
    return {
        "mappings": mappings,
        "bySource": dict(mappings),
    }


def _clamp_canonical_value(name: str, value: float, catalog: dict | None) -> float:
    entry = (catalog or {}).get("byName", {}).get(name)
    if not entry:
        return value
    return _clamp(value, float(entry["min"]), float(entry["max"]))


def _resolve_canonical_alias(name: str, catalog: dict | None) -> str | None:
    if not name:
        return None
    by_name = (catalog or {}).get("byName", {})
    if name in by_name:
        return name
    pascal_name = f"{name[:1].upper()}{name[1:]}"
    if pascal_name in by_name:
        return pascal_name
    return None


def coerce_canonical_values(values=None, catalog: dict | None = None) -> dict[str, float]:
    if values is None:
        return {}
    if not _is_plain_object(values):
        raise ValueError("values must be an object of numeric coefficients")

    normalized: dict[str, float] = {}
    for name, raw_value in values.items():
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            raise ValueError(f"values.{name} must be a finite number") from None
        normalized[str(name)] = _clamp_canonical_value(str(name), numeric, catalog)
    return normalized


def merge_canonical_value_maps(catalog: dict | None, *maps) -> dict[str, float]:
    merged: dict[str, float] = {}

    for value_map in maps:
        if not _is_plain_object(value_map):
            continue
        for name, raw_value in value_map.items():
            try:
                numeric = float(raw_value)
            except (TypeError, ValueError):
                continue
            merged[str(name)] = _clamp_canonical_value(str(name), numeric, catalog)

    return merged


def filter_robot_coefficients(values=None, catalog: dict | None = None, include_defaults: bool = False) -> dict[str, float]:
    normalized = coerce_canonical_values(values, catalog)
    filtered: dict[str, float] = {}

    if include_defaults:
        for name in (catalog or {}).get("robotNames", set()):
            filtered[name] = normalized.get(name, 0.0)
        return filtered

    for name, value in normalized.items():
        if not (catalog or {}).get("byName", {}).get(name, {}).get("forwardToRobot"):
            continue
        filtered[name] = value

    return filtered


def convert_avatar_values_to_canonical(values=None, reverse_mapping: dict | None = None, catalog: dict | None = None) -> dict[str, float]:
    normalized = coerce_canonical_values(values)
    converted: dict[str, float] = {}

    for source_name, value in normalized.items():
        targets = (reverse_mapping or {}).get("bySource", {}).get(source_name)
        if not targets:
            continue
        for target_name, scale in targets.items():
            next_value = converted.get(target_name, 0.0) + value * float(scale)
            converted[target_name] = _clamp_canonical_value(target_name, next_value, catalog)

    return converted


def normalize_incoming_values(values=None, reverse_mapping: dict | None = None, catalog: dict | None = None) -> dict[str, float]:
    raw_values = coerce_canonical_values(values)
    direct_values: dict[str, float] = {}
    legacy_values: dict[str, float] = {}

    for name, value in raw_values.items():
        canonical_name = _resolve_canonical_alias(name, catalog)
        if canonical_name:
            direct_values[canonical_name] = _clamp_canonical_value(canonical_name, value, catalog)
            continue
        legacy_values[name] = value

    converted_legacy = convert_avatar_values_to_canonical(legacy_values, reverse_mapping, catalog)
    return merge_canonical_value_maps(catalog, converted_legacy, direct_values)
