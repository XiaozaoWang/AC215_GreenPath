import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock

spec = importlib.util.spec_from_file_location("run_pipeline", Path(__file__).resolve().parents[1] / "run_pipeline.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LauncherChecks(unittest.TestCase):
    def test_safe_run_id(self):
        self.assertEqual(module.validate_run_id("smoke-20261018-01"), "smoke-20261018-01")
        for value in ["../outside", "UPPER", "", "a/b", "a" * 57, "has space"]:
            with self.assertRaises(ValueError):
                module.validate_run_id(value)

    def test_compose_status_array_and_json_lines(self):
        array = '[{"Service":"data-processor","ExitCode":0}]'
        lines = '{"Service":"data-processor"}\n{"Service":"feature-encoder"}\n'
        self.assertEqual(module.parse_status(array)[0]["ExitCode"], 0)
        self.assertEqual(len(module.parse_status(lines)), 2)

    def test_ports_owned_by_the_pipeline_app_can_be_reused(self):
        with patch.object(module.subprocess, "check_output", return_value="127.0.0.1:9030->9000/tcp"), patch.object(module.socket, "socket") as probe:
            module.check_ports([9030], {})
            probe.assert_not_called()

    def test_other_process_port_conflict_fails_before_jobs(self):
        with patch.object(module.subprocess, "check_output", return_value=""), patch.object(module.socket, "socket") as probe:
            probe.return_value.__enter__.return_value.bind.side_effect = OSError("occupied")
            with self.assertRaisesRegex(ValueError, "occupied"):
                module.check_ports([9002], {})


if __name__ == "__main__":
    unittest.main()
