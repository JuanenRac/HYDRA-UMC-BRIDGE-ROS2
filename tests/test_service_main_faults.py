# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - tests/test_service_main_faults.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The start-up of the ROS 2 bridge service when things go wrong: a configuration that is damaged, a secret file that is missing or empty, a ROS 2 that is not
installed, a transport loop that raises or simply ends. The service must exit with a code that tells systemd what happened and must never print the secret.
A stand-in node and a stand-in transport loop are used: no ROS 2 and no broker are needed."""

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
from hydra_umc_bridge_ros2.service import ConfigError, MqttConfig, NodeConfig, _read_secret, _section, load_service_config, main


class FakeNode:
    def declare_parameter(self, name: str, default: object) -> SimpleNamespace:
        return SimpleNamespace(value=default)


def run(argv: list[str], *, loop=None, build=None) -> tuple[int, str, str, list[tuple]]:
    """Runs main() with a stand-in transport loop and node; returns (exit code, stdout, stderr, the calls to the loop)."""
    calls: list[tuple] = []

    def fake_loop(bridge: object, host: str, port: int, client_id: str, **kwargs: object) -> None:
        calls.append((host, port, client_id, kwargs))
        if loop is not None:
            loop()

    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(service, "run_forever", fake_loop), mock.patch.object(service, "build_bridge", build or (lambda config: SimpleNamespace(node=FakeNode()))):
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
    return code, out.getvalue(), err.getvalue(), calls


class Sections(unittest.TestCase):
    def test_a_section_must_be_an_object_with_known_keys_of_the_right_type(self):
        self.assertEqual(_section(None, NodeConfig, "node"), NodeConfig())
        with self.assertRaisesRegex(ConfigError, "'node' must be a JSON object"):
            _section([], NodeConfig, "node")
        with self.assertRaisesRegex(ConfigError, "unknown keys in 'node': speed"):
            _section({"speed": 1}, NodeConfig, "node")
        with self.assertRaisesRegex(ConfigError, "'node.name' must be a str"):
            _section({"name": 5}, NodeConfig, "node")
        with self.assertRaisesRegex(ConfigError, "'mqtt.port' must be a int"):
            _section({"port": True}, MqttConfig, "mqtt")
        with self.assertRaisesRegex(ConfigError, "'mqtt.port' must be a int"):
            _section({"port": "1883"}, MqttConfig, "mqtt")

    def test_a_whole_number_is_a_good_float(self):
        self.assertEqual(_section({"observed_max_age_s": 3}, NodeConfig, "node").observed_max_age_s, 3)


class ConfigFiles(unittest.TestCase):
    def write(self, folder: str, content: object) -> Path:
        path = Path(folder) / "bridge.json"
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path

    def test_what_is_refused_at_load(self):
        with tempfile.TemporaryDirectory() as folder:
            cases = [
                ("{not json", "cannot read"),
                ("[]", "must be a JSON object"),
                ({"extra": {}}, "unknown keys: extra"),
                ({"mqtt": {"host": ""}}, "must name a broker"),
                ({"mqtt": {"port": 0}}, "must name a broker"),
                ({"mqtt": {"port": 65536}}, "must name a broker"),
                ({"mqtt": {"max_connect_attempts": 0}}, "at least 1 attempt"),
                ({"mqtt": {"connect_retry_delay_seconds": -1}}, "at least 1 attempt"),
                ({"node": {"name": "bad name!"}}, "ROS 2 node name"),
                ({"node": {"name": "1node"}}, "ROS 2 node name"),
                ({"node": {"observed_max_age_s": -1}}, "0 or more"),
            ]
            for content, text in cases:
                with self.assertRaisesRegex(ConfigError, text, msg=str(content)):
                    load_service_config(self.write(folder, content))

    def test_the_edges_that_are_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            config = load_service_config(self.write(folder, {"mqtt": {"port": 65535, "connect_retry_delay_seconds": 0}, "node": {"name": "_n1", "observed_max_age_s": 0}}))
            self.assertEqual((config.mqtt.port, config.node.name, config.node.observed_max_age_s), (65535, "_n1", 0))

    def test_a_missing_file_gives_the_conservative_defaults(self):
        self.assertEqual(load_service_config(Path(tempfile.gettempdir()) / "no-such-bridge-config.json"), load_service_config(None))


class Secrets(unittest.TestCase):
    def test_a_password_file_is_read_trimmed_and_never_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            good = Path(folder) / "pw"
            good.write_text("  s3cret\n", encoding="utf-8")
            self.assertEqual(_read_secret(str(good), "MQTT password"), "s3cret")
            empty = Path(folder) / "empty"
            empty.write_text(" \n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "MQTT password file is empty"):
                _read_secret(str(empty), "MQTT password")
            with self.assertRaisesRegex(ConfigError, "cannot read the MQTT password file"):
                _read_secret(str(Path(folder) / "missing"), "MQTT password")


class Main(unittest.TestCase):
    def config(self, folder: str, **extra: object) -> str:
        path = Path(folder) / "bridge.json"
        path.write_text(json.dumps({"mqtt": {"host": "broker.test", "port": 1884, "client_id": "ros2-sim", **extra}}), encoding="utf-8")
        return str(path)

    def test_check_validates_and_exits_before_anything_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            code, out, _, calls = run(["--config", self.config(folder), "--check"])
        self.assertEqual((code, calls), (0, []))
        self.assertIn("CONFIG=PASS", out)

    def test_a_damaged_configuration_exits_2_and_says_why(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "bad.json"
            bad.write_text("{oops", encoding="utf-8")
            code, _, err, calls = run(["--config", str(bad)])
        self.assertEqual((code, calls), (2, []))
        self.assertIn("configuration error", err)

    def test_an_unreadable_password_file_exits_2_without_starting(self):
        with tempfile.TemporaryDirectory() as folder:
            code, _, err, calls = run(["--config", self.config(folder, password_file=str(Path(folder) / "missing"))])
        self.assertEqual((code, calls), (2, []))
        self.assertIn("password file", err)

    def test_ros2_not_installed_exits_3(self):
        def no_ros(config: object) -> object:
            raise RuntimeError("rclpy is not installed")

        with tempfile.TemporaryDirectory() as folder:
            code, _, err, calls = run(["--config", self.config(folder)], build=no_ros)
        self.assertEqual((code, calls), (3, []))
        self.assertIn("rclpy is not installed", err)

    def test_the_transport_receives_the_broker_and_the_secret_only_as_an_argument(self):
        with tempfile.TemporaryDirectory() as folder:
            secret = Path(folder) / "pw"
            secret.write_text("hunter2\n", encoding="utf-8")
            code, out, err, calls = run(["--config", self.config(folder, username="ros", password_file=str(secret))])
        self.assertEqual(code, 1, "a loop that ends is a failure: systemd restarts the service")
        host, port, client_id, kwargs = calls[0]
        self.assertEqual((host, port, client_id), ("broker.test", 1884, "ros2-sim"))
        self.assertEqual((kwargs["username"], kwargs["password"]), ("ros", "hunter2"))
        self.assertNotIn("hunter2", out + err)

    def test_no_username_is_passed_as_none(self):
        with tempfile.TemporaryDirectory() as folder:
            _, _, _, calls = run(["--config", self.config(folder)])
        self.assertEqual((calls[0][3]["username"], calls[0][3]["password"]), (None, None))

    def test_a_loop_that_raises_is_logged_and_exits_1(self):
        def boom() -> None:
            raise OSError("broker went away")

        with tempfile.TemporaryDirectory() as folder, self.assertLogs(service._LOG, level="ERROR") as logged:
            code, _, _, calls = run(["--config", self.config(folder)], loop=boom)
        self.assertEqual((code, len(calls)), (1, 1))
        self.assertTrue(any("the bridge stopped" in line for line in logged.output))

    def test_the_configuration_path_may_come_from_the_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict("os.environ", {service.CONFIG_ENVIRONMENT: self.config(folder)}):
                code, out, _, _ = run(["--check"])
        self.assertEqual(code, 0)
        self.assertIn("CONFIG=PASS", out)


if __name__ == "__main__":
    unittest.main()
