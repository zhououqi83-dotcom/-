from __future__ import annotations

from .base import LinearRuleCompensator


DEFAULT_EYE_LOOK_EYELID_SECTION = {
    "enabled": True,
    "rules": {
        "leftEyeOpenFromLookUp": {
            "enabled": True,
            "target": "EyeWideLeft",
            "minDelta": 0.0,
            "maxDelta": 0.12,
            "sources": {
                "EyeLookUpLeft": 0.12,
            },
        },
        "rightEyeOpenFromLookUp": {
            "enabled": True,
            "target": "EyeWideRight",
            "minDelta": 0.0,
            "maxDelta": 0.12,
            "sources": {
                "EyeLookUpRight": 0.12,
            },
        },
        "leftEyeCloseFromLookDown": {
            "enabled": True,
            "target": "EyeBlinkLeft",
            "minDelta": 0.0,
            "maxDelta": 0.12,
            "sources": {
                "EyeLookDownLeft": 0.12,
            },
        },
        "rightEyeCloseFromLookDown": {
            "enabled": True,
            "target": "EyeBlinkRight",
            "minDelta": 0.0,
            "maxDelta": 0.12,
            "sources": {
                "EyeLookDownRight": 0.12,
            },
        },
    },
}


class EyeLookEyelidCompensator(LinearRuleCompensator):
    section_name = "eyeLookEyelidCoupling"
    default_section = DEFAULT_EYE_LOOK_EYELID_SECTION
