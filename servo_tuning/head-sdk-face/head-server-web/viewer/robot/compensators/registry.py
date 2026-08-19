from __future__ import annotations

from copy import deepcopy

from .brow_eyelid import BrowEyelidCompensator
from .eye_look_eyelid import EyeLookEyelidCompensator


COMPENSATOR_TYPES = (
    BrowEyelidCompensator,
    EyeLookEyelidCompensator,
)


def default_compensation_config() -> dict:
    return {
        compensator.section_name: deepcopy(compensator.default_section)
        for compensator in COMPENSATOR_TYPES
    }


def build_compensators(config: dict | None) -> list:
    source = config if isinstance(config, dict) else {}
    return [
        compensator(source.get(compensator.section_name))
        for compensator in COMPENSATOR_TYPES
    ]
