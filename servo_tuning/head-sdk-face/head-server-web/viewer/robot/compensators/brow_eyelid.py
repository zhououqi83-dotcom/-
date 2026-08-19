from __future__ import annotations

from .base import LinearRuleCompensator


DEFAULT_BROW_EYELID_SECTION = {
    "enabled": True,
    "rules": {
        "leftEyeOpenFromBrow": {
            "enabled": True,
            "target": "EyeWideLeft",
            "minDelta": 0.0,
            "maxDelta": 0.25,
            "sources": {
                "BrowInnerUp": 0.08,
                "BrowOuterUpLeft": 0.18,
            },
        },
        "rightEyeOpenFromBrow": {
            "enabled": True,
            "target": "EyeWideRight",
            "minDelta": 0.0,
            "maxDelta": 0.25,
            "sources": {
                "BrowInnerUp": 0.08,
                "BrowOuterUpRight": 0.18,
            },
        },
        "leftEyeCloseFromBrow": {
            "enabled": True,
            "target": "EyeBlinkLeft",
            "minDelta": 0.0,
            "maxDelta": 0.18,
            "sources": {
                "BrowDownLeft": 0.16,
            },
        },
        "rightEyeCloseFromBrow": {
            "enabled": True,
            "target": "EyeBlinkRight",
            "minDelta": 0.0,
            "maxDelta": 0.18,
            "sources": {
                "BrowDownRight": 0.16,
            },
        },
    },
}


class BrowEyelidCompensator(LinearRuleCompensator):
    section_name = "eyelidCoupling"
    default_section = DEFAULT_BROW_EYELID_SECTION
