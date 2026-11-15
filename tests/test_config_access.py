# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - tests/test_config_access.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The configuration and the parameter file live in a directory of the service, and the account of the service is proved able to read them.

The service account cannot enter the shared /etc/hydra-umc, so the unit names its own configuration directory. These tests run with temporary folders
and no root: the helpers, the flags of the service, the refusal of a file hidden by a permission and the text of the unit.
"""

import contextlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path

from hydra_umc_bridge_ros2 import config_access
from hydra_umc_bridge_ros2.config_access import (
    check_access,
    check_access_all,
    diagnose_access,
    ensure_directory,
    migrate_legacy_config,
    permission_problem,
)
from hydra_umc_bridge_ros2.service import ConfigError, load_service_config, main

ROOT = Path(__file__).resolve().parent.parent
UNIT = ROOT / "docs" / "deploy" / "hydra-umc-bridge-ros2.service"
POSIX_USER = hasattr(os, "geteuid") and os.geteuid() != 0 and os.name != "nt"


class PathsTests(unittest.TestCase):
    def test_the_files_are_in_the_directory_of_the_service_and_the_earlier_places_are_kept_for_the_migration(self):
        self.assertEqual(config_access.DEFAULT_CONFIG_PATH, "/etc/hydra-umc-bridge-ros2/bridge-ros2.json")
        self.assertEqual(config_access.DEFAULT_PARAMS_PATH, "/etc/hydra-umc-bridge-ros2/bridge-ros2-params.yaml")
        self.assertEqual(config_access.LEGACY_CONFIG_PATH, "/etc/hydra-umc/bridge-ros2.json")
        self.assertEqual(config_access.LEGACY_PARAMS_PATH, "/etc/hydra-umc/bridge-ros2-params.yaml")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.legacy = self.base / "old" / "bridge-ros2.json"
        self.legacy.parent.mkdir()
        self.target = self.base / "new" / "bridge-ros2.json"

    def test_a_file_of_the_earlier_place_is_copied_once_and_the_old_one_stays(self):
        self.legacy.write_text("{}", encoding="utf-8")
        self.assertEqual(migrate_legacy_config(str(self.legacy), str(self.target)), "migrated")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "{}")
        self.assertTrue(self.legacy.is_file())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)

    def test_a_file_that_is_already_there_is_never_overwritten(self):
        self.legacy.write_text('{"old": 1}', encoding="utf-8")
        self.target.parent.mkdir()
        self.target.write_text('{"new": 1}', encoding="utf-8")
        self.assertEqual(migrate_legacy_config(str(self.legacy), str(self.target)), "kept")
        self.assertEqual(self.target.read_text(encoding="utf-8"), '{"new": 1}')

    def test_nothing_to_migrate_creates_nothing(self):
        self.assertEqual(migrate_legacy_config(str(self.legacy), str(self.target)), "none")
        self.assertFalse(self.target.parent.exists())

    def test_a_group_that_does_not_exist_is_not_a_group(self):
        self.assertFalse(config_access.group_exists("hydra-no-such-group-xyz"))


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_the_directory_of_the_service_is_made_and_set_to_0750(self):
        directory = self.base / "hydra-umc-bridge-ros2"
        self.assertEqual(ensure_directory(str(directory)), "ready")
        self.assertTrue(directory.is_dir())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o750)

    def test_the_shared_directory_is_never_changed(self):
        shared = self.base / "hydra-umc"
        shared.mkdir()
        before = shared.stat().st_mode
        with self.assertRaises(ValueError):
            ensure_directory(str(shared))
        self.assertEqual(shared.stat().st_mode, before)


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_a_readable_file_passes_and_a_missing_one_is_refused_with_the_reason(self):
        path = self.base / "bridge-ros2.json"
        path.write_text("{}", encoding="utf-8")
        self.assertEqual(check_access(str(path))[0], 0)
        code, message = check_access(str(self.base / "no" / "bridge-ros2.json"))
        self.assertEqual(code, 2)
        self.assertIn("does not exist", message)

    def test_a_missing_file_is_not_a_permission_problem(self):
        self.assertEqual(permission_problem(str(self.base / "nothing.json")), "")
        self.assertIn("does not exist", diagnose_access(str(self.base / "nothing.json")))

    def test_both_files_must_be_readable(self):
        config = self.base / "bridge-ros2.json"
        config.write_text("{}", encoding="utf-8")
        params = self.base / "params.yaml"
        self.assertEqual(check_access_all([str(config), str(params)])[0], 2)
        params.write_text("a: 1\n", encoding="utf-8")
        self.assertEqual(check_access_all([str(config), str(params)])[0], 0)

    @unittest.skipUnless(POSIX_USER, "needs a POSIX account that is not root")
    def test_a_directory_the_account_cannot_enter_is_named_and_is_not_taken_for_a_missing_file(self):
        locked = self.base / "locked"
        locked.mkdir()
        (locked / "bridge-ros2.json").write_text("{}", encoding="utf-8")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        code, message = check_access(str(locked / "bridge-ros2.json"))
        self.assertEqual(code, 2)
        self.assertIn("cannot be entered", message)
        with self.assertRaises(ConfigError) as caught:
            load_service_config(str(locked / "bridge-ros2.json"))
        self.assertIn(str(locked), str(caught.exception))

    def test_a_missing_file_still_gives_the_defaults(self):
        self.assertEqual(load_service_config(str(self.base / "nothing.json")).mqtt.host, "127.0.0.1")


class FlagsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.config = self.base / "hydra-umc-bridge-ros2" / "bridge-ros2.json"
        self.params = self.base / "hydra-umc-bridge-ros2" / "bridge-ros2-params.yaml"
        self.old = self.base / "old"
        self.old.mkdir()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--config", str(self.config), "--params-path", str(self.params), *args])
        return code, out.getvalue(), err.getvalue()

    def test_prepare_config_copies_both_files_once_and_exits_zero(self):
        (self.old / "bridge-ros2.json").write_text("{}", encoding="utf-8")
        (self.old / "params.yaml").write_text("a: 1\n", encoding="utf-8")
        arguments = ("--prepare-config", "--legacy-config", str(self.old / "bridge-ros2.json"), "--legacy-params", str(self.old / "params.yaml"))
        code, out, _ = self.run_main(*arguments)
        self.assertEqual(code, 0)
        self.assertIn("configuration migrated", out)
        self.assertIn("parameters migrated", out)
        self.assertEqual(self.params.read_text(encoding="utf-8"), "a: 1\n")
        code, out, _ = self.run_main(*arguments)
        self.assertEqual(code, 0)
        self.assertIn("configuration kept", out)

    def test_prepare_config_without_any_file_exits_zero_and_check_access_then_refuses(self):
        arguments = ("--prepare-config", "--legacy-config", str(self.old / "a.json"), "--legacy-params", str(self.old / "a.yaml"))
        code, out, _ = self.run_main(*arguments)
        self.assertEqual((code, "none" in out), (0, True))
        code, _, err = self.run_main("--check-access")
        self.assertEqual(code, 2)
        self.assertIn("cannot read the configuration file", err)

    def test_prepare_config_sets_the_directory_of_the_service_to_0750(self):
        self.run_main("--prepare-config", "--legacy-config", str(self.old / "a.json"), "--legacy-params", str(self.old / "a.yaml"))
        self.assertTrue(self.config.parent.is_dir())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o750)

    def test_prepare_config_does_not_touch_a_directory_the_operator_named(self):
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        if os.name != "nt":
            elsewhere.chmod(0o755)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--config", str(elsewhere / "c.json"), "--params-path", str(elsewhere / "p.yaml"), "--prepare-config", "--legacy-config", str(self.old / "a.json"), "--legacy-params", str(self.old / "a.yaml")])
        self.assertEqual(code, 0)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(elsewhere.stat().st_mode), 0o755)

    def test_check_access_exits_zero_when_both_files_can_be_read(self):
        self.config.parent.mkdir()
        self.config.write_text("{}", encoding="utf-8")
        self.params.write_text("a: 1\n", encoding="utf-8")
        code, out, _ = self.run_main("--check-access")
        self.assertEqual((code, "readable" in out), (0, True))

    def test_the_ros_arguments_still_pass_through_to_rclpy(self):
        self.config.parent.mkdir()
        self.config.write_text("{}", encoding="utf-8")
        code, out, _ = self.run_main("--check", "--ros-args", "--params-file", str(self.params))
        self.assertEqual(code, 0)
        self.assertIn("CONFIG=PASS", out)


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.text = UNIT.read_text(encoding="utf-8")

    def test_the_unit_asks_systemd_for_a_directory_of_its_own_with_mode_0750(self):
        self.assertIn("ConfigurationDirectory=hydra-umc-bridge-ros2\n", self.text)
        self.assertIn("ConfigurationDirectoryMode=0750\n", self.text)

    def test_the_unit_reads_both_files_from_that_directory_and_not_from_the_shared_one(self):
        self.assertIn("HYDRA_UMC_ROS2_CONFIG=/etc/hydra-umc-bridge-ros2/bridge-ros2.json", self.text)
        self.assertIn("--params-file /etc/hydra-umc-bridge-ros2/bridge-ros2-params.yaml", self.text)
        self.assertNotIn("HYDRA_UMC_ROS2_CONFIG=/etc/hydra-umc/", self.text)
        self.assertNotIn("--params-file /etc/hydra-umc/", self.text)

    def test_the_migration_runs_as_root_and_the_access_check_as_the_service_account_before_the_start(self):
        lines = [line for line in self.text.splitlines() if line.startswith("Exec")]
        prepare = next(i for i, line in enumerate(lines) if "--prepare-config" in line)
        access = next(i for i, line in enumerate(lines) if "--check-access" in line)
        start = next(i for i, line in enumerate(lines) if line.startswith("ExecStart="))
        self.assertTrue(lines[prepare].startswith("ExecStartPre=+"))
        self.assertTrue(lines[access].startswith("ExecStartPre=/"))
        self.assertLess(prepare, access)
        self.assertLess(access, start)

    def test_the_shared_directory_is_not_named_as_a_configuration_directory(self):
        self.assertNotIn("ConfigurationDirectory=hydra-umc\n", self.text)
        self.assertNotIn("ConfigurationDirectory=/etc/hydra-umc", self.text)


if __name__ == "__main__":
    unittest.main()
