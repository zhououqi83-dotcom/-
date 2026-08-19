from __future__ import annotations

from copy import deepcopy


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def to_float(value, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def merge_dict(base: dict, override: dict | None) -> dict:
    merged = deepcopy(base)
    if not isinstance(override, dict):
        return merged

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dict(merged[key], value)
            continue
        merged[key] = value
    return merged


def clamp_target_value(target: str, value: float, coefficient_catalog: dict | None = None) -> float:
    if coefficient_catalog:
        entry = coefficient_catalog.get("byName", {}).get(target)
        if entry:
            return clamp(value, to_float(entry.get("min"), 0.0), to_float(entry.get("max"), 1.0))
    return clamp(value, 0.0, 1.0)


class LinearRuleCompensator:
    section_name = ""
    default_section: dict = {}

    def __init__(self, section_config: dict | None = None):
        self.config = merge_dict(self.default_section, section_config)

    def status(self) -> dict:
        rules = self.config.get("rules", {})
        enabled_rules = 0
        if isinstance(rules, dict):
            enabled_rules = sum(
                1
                for rule in rules.values()
                if isinstance(rule, dict) and rule.get("enabled") is not False
            )

        return {
            "name": self.section_name,
            "enabled": bool(self.config.get("enabled", True)),
            "rule_count": enabled_rules,
        }

    def apply(self, blendshapes: dict[str, float], coefficient_catalog: dict | None = None) -> dict[str, float]:
        result = {
            str(name): to_float(value)
            for name, value in (blendshapes or {}).items()
        }
        if not self.config.get("enabled", True):
            return result

        rules = self.config.get("rules", {})
        if not isinstance(rules, dict):
            return result

        source_values = dict(result)
        for rule in rules.values():
            if not isinstance(rule, dict) or rule.get("enabled") is False:
                continue

            target = str(rule.get("target") or "").strip()
            if not target:
                continue

            sources = rule.get("sources")
            if not isinstance(sources, dict):
                continue

            delta = to_float(rule.get("bias"), 0.0)
            for source_name, scale in sources.items():
                delta += to_float(source_values.get(str(source_name), 0.0)) * to_float(scale, 0.0)

            delta = clamp(
                delta,
                to_float(rule.get("minDelta"), 0.0),
                to_float(rule.get("maxDelta"), 1.0),
            )

            existing = to_float(result.get(target), 0.0)
            result[target] = clamp_target_value(
                target,
                existing + delta,
                coefficient_catalog,
            )

        return result
