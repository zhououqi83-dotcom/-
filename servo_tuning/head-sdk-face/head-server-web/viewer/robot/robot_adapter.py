from __future__ import annotations

from pathlib import Path
import sys

CORE_DIR = Path(__file__).resolve().parent.parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from server_utils.simple_yaml import parse_simple_yaml
from viewer.robot.compensators import build_compensators, default_compensation_config
from viewer.robot.compensators.base import merge_dict, to_float


DEFAULT_ADAPTER_CONFIG = default_compensation_config()


class RobotBlendshapeAdapter:
    def __init__(self, config_path: str | Path | None = None):
        self.config_path = str(config_path) if config_path else None
        self.config_error: str | None = None
        self.config = default_compensation_config()
        self._compensators = build_compensators(self.config)
        if self.config_path:
            self.reload(self.config_path)

    def reload(self, config_path: str | Path | None = None) -> dict:
        if config_path is not None:
            self.config_path = str(config_path)

        if not self.config_path:
            self.config = default_compensation_config()
            self.config_error = None
            self._compensators = build_compensators(self.config)
            return self.status()

        try:
            raw = parse_simple_yaml(Path(self.config_path).read_text(encoding="utf-8"))
            self.config = merge_dict(DEFAULT_ADAPTER_CONFIG, raw)
            self.config_error = None
        except Exception as error:
            self.config = default_compensation_config()
            self.config_error = str(error)

        self._compensators = build_compensators(self.config)
        return self.status()

    def status(self) -> dict:
        rules = [compensator.status() for compensator in self._compensators]
        return {
            "config_path": self.config_path,
            "enabled": any(rule["enabled"] for rule in rules),
            "error": self.config_error,
            "rule_count": len(rules),
            "rules": rules,
        }

    def apply(self, blendshapes: dict[str, float], coefficient_catalog: dict | None = None) -> dict[str, float]:
        result = {
            str(name): to_float(value)
            for name, value in (blendshapes or {}).items()
        }

        for compensator in self._compensators:
            result = compensator.apply(result, coefficient_catalog)

        return result
