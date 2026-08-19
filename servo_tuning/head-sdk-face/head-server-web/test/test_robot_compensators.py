from __future__ import annotations

from pathlib import Path
import sys
import unittest

CORE_DIR = Path(__file__).resolve().parent.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from viewer.robot.robot_adapter import RobotBlendshapeAdapter


COEFFICIENT_CATALOG = {
    "byName": {
        "EyeBlinkLeft": {"min": 0.0, "max": 1.0},
        "EyeBlinkRight": {"min": 0.0, "max": 1.0},
        "EyeWideLeft": {"min": 0.0, "max": 1.0},
        "EyeWideRight": {"min": 0.0, "max": 1.0},
    },
}


class RobotCompensatorTest(unittest.TestCase):
    def setUp(self) -> None:
        config_path = CORE_DIR / "viewer" / "robot" / "robot-adapter.yaml"
        self.adapter = RobotBlendshapeAdapter(config_path)

    def test_existing_brow_rule_still_applies(self) -> None:
        result = self.adapter.apply(
            {
                "EyeBlinkLeft": 0.2,
                "BrowDownLeft": 0.5,
            },
            COEFFICIENT_CATALOG,
        )

        self.assertAlmostEqual(result["EyeBlinkLeft"], 0.5)

    def test_eye_look_up_and_down_drive_eyelids(self) -> None:
        result = self.adapter.apply(
            {
                "EyeLookUpLeft": 0.5,
                "EyeLookDownRight": 0.5,
            },
            COEFFICIENT_CATALOG,
        )

        self.assertAlmostEqual(result["EyeWideLeft"], 0.06)
        self.assertAlmostEqual(result["EyeBlinkRight"], 0.06)

    def test_status_reports_loaded_rule_sets(self) -> None:
        status = self.adapter.status()

        self.assertEqual(status["rule_count"], 2)
        self.assertEqual(
            [rule["name"] for rule in status["rules"]],
            ["eyelidCoupling", "eyeLookEyelidCoupling"],
        )


if __name__ == "__main__":
    unittest.main()
