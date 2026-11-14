# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - tests/test_sros2_requirement.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The switch that makes the service refuse to start without ROS 2 security. No ROS 2 is needed: the check reads the environment variables and a directory."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hydra_umc_bridge_ros2 import service
from hydra_umc_bridge_ros2.service import ConfigError, SecurityConfig, check_security, load_service_config, main, sros2_problems


def secure_env(root: Path) -> dict[str, str]:
    (root / "enclaves").mkdir()
    return {"ROS_SECURITY_ENABLE": "true", "ROS_SECURITY_STRATEGY": "Enforce", "ROS_SECURITY_KEYSTORE": str(root)}


class Sros2ProblemsTests(unittest.TestCase):
    def test_a_complete_environment_has_no_problem(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(sros2_problems(secure_env(Path(folder))), [])

    def test_case_and_spaces_of_the_values_do_not_matter(self):
        with tempfile.TemporaryDirectory() as folder:
            env = secure_env(Path(folder))
            env["ROS_SECURITY_ENABLE"] = " TRUE "
            env["ROS_SECURITY_STRATEGY"] = "enforce"
            self.assertEqual(sros2_problems(env), [])

    def test_each_missing_piece_is_named(self):
        with tempfile.TemporaryDirectory() as folder:
            base = secure_env(Path(folder))
            for key, text in (("ROS_SECURITY_ENABLE", "ROS_SECURITY_ENABLE"), ("ROS_SECURITY_STRATEGY", "ROS_SECURITY_STRATEGY"), ("ROS_SECURITY_KEYSTORE", "ROS_SECURITY_KEYSTORE is not set")):
                env = dict(base)
                del env[key]
                problems = sros2_problems(env)
                self.assertEqual(len(problems), 1, key)
                self.assertIn(text, problems[0])

    def test_permissive_strategy_or_false_enable_or_a_keystore_without_enclaves_are_problems(self):
        with tempfile.TemporaryDirectory() as folder:
            base = secure_env(Path(folder))
            for key, value in (("ROS_SECURITY_STRATEGY", "Permissive"), ("ROS_SECURITY_ENABLE", "false"), ("ROS_SECURITY_ENABLE", "1")):
                env = dict(base)
                env[key] = value
                self.assertEqual(len(sros2_problems(env)), 1, (key, value))
            empty = Path(folder) / "empty"
            empty.mkdir()
            env = dict(base)
            env["ROS_SECURITY_KEYSTORE"] = str(empty)
            self.assertIn("no 'enclaves' directory", sros2_problems(env)[0])

    def test_nothing_set_names_all_three(self):
        self.assertEqual(len(sros2_problems({})), 3)


class SwitchTests(unittest.TestCase):
    def test_off_by_default_checks_nothing(self):
        self.assertFalse(SecurityConfig().require_sros2)
        check_security(SecurityConfig(), {})

    def test_on_without_security_refuses_and_says_what_is_missing(self):
        with self.assertRaises(ConfigError) as caught:
            check_security(SecurityConfig(True), {})
        self.assertIn("ROS_SECURITY_ENABLE", str(caught.exception))

    def test_on_with_security_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            check_security(SecurityConfig(True), secure_env(Path(folder)))

    def test_the_section_is_read_from_the_file_and_a_wrong_type_or_key_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bridge.json"
            path.write_text(json.dumps({"security": {"require_sros2": True}}), encoding="utf-8")
            self.assertTrue(load_service_config(path).security.require_sros2)
            for bad in ({"require_sros2": "yes"}, {"require_sros2": 1}, {"other": True}):
                path.write_text(json.dumps({"security": bad}), encoding="utf-8")
                with self.assertRaises(ConfigError):
                    load_service_config(path)


class MainTests(unittest.TestCase):
    def run_main(self, config: dict[str, object], env: dict[str, str], extra: list[str] | None = None) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bridge.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(service, "build_bridge", lambda c: SimpleNamespace(node=None)), mock.patch.object(
                service, "run_forever", lambda *a, **k: None
            ), redirect_stdout(out), redirect_stderr(err):
                code = main(["--config", str(path), *(extra or [])])
            return code, out.getvalue(), err.getvalue()

    def test_the_service_exits_with_a_configuration_error_when_security_is_required_and_missing(self):
        code, _out, err = self.run_main({"security": {"require_sros2": True}}, {})
        self.assertEqual(code, 2)
        self.assertIn("ROS_SECURITY_ENABLE", err)

    def test_check_mode_reports_it_too(self):
        code, out, err = self.run_main({"security": {"require_sros2": True}}, {}, ["--check"])
        self.assertEqual(code, 2)
        self.assertNotIn("CONFIG=PASS", out)
        self.assertIn("require_sros2", err)

    def test_without_the_switch_the_service_starts_as_before(self):
        code, _out, _err = self.run_main({}, {})
        self.assertNotEqual(code, 2)

    def test_with_the_switch_and_a_full_environment_the_check_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            code, out, _err = self.run_main({"security": {"require_sros2": True}}, secure_env(Path(folder)), ["--check"])
        self.assertEqual(code, 0)
        self.assertIn("CONFIG=PASS", out)


if __name__ == "__main__":
    unittest.main()
