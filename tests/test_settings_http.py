# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - tests/test_settings_http.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The route by which the SERVER reads and changes `security.require_sros2`: closed unless opened, identity of the SERVER on every request, the change only when
the service was started allowing it, with a confirmation, written to the file and applied at the next restart. A real listener on loopback; no ROS 2 is needed."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hydra_umc_bridge_ros2 import service
from hydra_umc_bridge_ros2.server_identity import sign_token
from hydra_umc_bridge_ros2.service import load_service_config, main, sros2_problems
from hydra_umc_bridge_ros2.settings_http import Sros2Settings, read_required, start_settings_server, write_required

KEY = b"k" * 40


def settings_for(path: Path, active: bool = False, allow: bool = True, env: dict[str, str] | None = None) -> Sros2Settings:
    return Sros2Settings(str(path), active, allow, env or {}, sros2_problems, load_service_config)


class Listener:
    def __init__(self, settings: Sros2Settings):
        self.server = start_settings_server("127.0.0.1", 0, settings, KEY)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def call(self, method: str, body: object = None, role: str | None = "admin", path: str = "/settings/sros2") -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if role:
            headers["Authorization"] = f"Bearer {sign_token(KEY, 'tester', role)}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        try:
            with urlopen(Request(self.base + path, data=data, method=method, headers=headers), timeout=5) as response:  # noqa: S310
                return response.status, json.loads(response.read())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()


class FileTests(unittest.TestCase):
    def test_reads_the_default_when_there_is_no_file_or_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bridge.json"
            self.assertFalse(read_required(path))
            self.assertFalse(read_required(None))
            path.write_text("{}", encoding="utf-8")
            self.assertFalse(read_required(path))
            path.write_text("not json", encoding="utf-8")
            self.assertFalse(read_required(path))

    def test_writes_the_key_and_keeps_everything_else(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bridge.json"
            path.write_text(json.dumps({"mqtt": {"port": 1884}, "security": {"require_sros2": False}}), encoding="utf-8")
            write_required(path, True, load_service_config)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"mqtt": {"port": 1884}, "security": {"require_sros2": True}})
            self.assertTrue(read_required(path))

    def test_a_file_that_would_not_load_is_left_as_it_was(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bridge.json"
            path.write_text(json.dumps({"security": {"other": 1}}), encoding="utf-8")
            before = path.read_text(encoding="utf-8")
            with self.assertRaises(ValueError):
                write_required(path, True, load_service_config)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertEqual([item.name for item in Path(folder).iterdir()], ["bridge.json"])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "bridge.json"
        self.path.write_text(json.dumps({}), encoding="utf-8")
        self.listener = Listener(settings_for(self.path))

    def tearDown(self):
        self.listener.close()
        self.folder.cleanup()

    def test_nothing_is_open_without_a_valid_token(self):
        self.assertEqual(self.listener.call("GET", role=None)[0], 401)
        self.assertEqual(self.listener.call("POST", {"requireSros2": True, "confirm": True}, role=None)[0], 401)

    def test_any_role_reads_and_the_default_is_off(self):
        for role in ("viewer", "operator", "admin"):
            status, body = self.listener.call("GET", role=role)
            self.assertEqual(status, 200)
            self.assertEqual(body, {"requireSros2": False, "active": False, "restartNeeded": False, "canChange": True, "environmentReady": False})

    def test_unknown_paths_are_not_found(self):
        self.assertEqual(self.listener.call("GET", path="/settings")[0], 404)
        self.assertEqual(self.listener.call("POST", {}, path="/other")[0], 404)

    def test_only_an_administrator_changes_it(self):
        for role in ("viewer", "operator"):
            status, _body = self.listener.call("POST", {"requireSros2": True, "confirm": True}, role=role)
            self.assertIn(status, (401, 403))
        self.assertFalse(read_required(self.path))

    def test_the_body_and_the_confirmation_are_checked_before_anything_is_written(self):
        for body in ({}, {"requireSros2": "yes", "confirm": True}, {"requireSros2": True, "confirm": True, "extra": 1}):
            self.assertEqual(self.listener.call("POST", body)[0], 400)
        status, answer = self.listener.call("POST", {"requireSros2": True})
        self.assertEqual((status, answer["code"]), (400, "CONFIRMATION_REQUIRED"))
        status, answer = self.listener.call("POST", {"requireSros2": False})
        self.assertEqual((status, answer["code"]), (400, "CONFIRMATION_REQUIRED"))
        self.assertFalse(read_required(self.path))

    def test_a_change_goes_to_the_file_and_says_it_applies_after_a_restart(self):
        status, body = self.listener.call("POST", {"requireSros2": True, "confirm": True})
        self.assertEqual(status, 200)
        self.assertEqual((body["requireSros2"], body["active"], body["restartNeeded"], body["changed"], body["appliesAfterRestart"]), (True, False, True, True, True))
        self.assertTrue(read_required(self.path))
        self.assertEqual(self.listener.call("GET", role="viewer")[1]["restartNeeded"], True)
        status, body = self.listener.call("POST", {"requireSros2": True, "confirm": True})
        self.assertEqual((status, body["changed"]), (200, False))
        status, body = self.listener.call("POST", {"requireSros2": False, "confirm": True})
        self.assertEqual((status, body["requireSros2"], body["restartNeeded"], body["changed"]), (200, False, False, True))

    def test_a_refused_file_answers_409_and_keeps_the_file(self):
        self.path.write_text(json.dumps({"security": {"unknown": 1}}), encoding="utf-8")
        status, body = self.listener.call("POST", {"requireSros2": True, "confirm": True})
        self.assertEqual((status, body["code"]), (409, "SETTING_REFUSED"))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"security": {"unknown": 1}})


class NotAllowedTests(unittest.TestCase):
    def test_without_the_switch_the_change_is_refused_and_the_file_is_not_touched(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bridge.json"
            path.write_text("{}", encoding="utf-8")
            listener = Listener(settings_for(path, allow=False))
            try:
                status, body = listener.call("POST", {"requireSros2": True, "confirm": True})
                self.assertEqual((status, body["code"]), (403, "CHANGE_NOT_ALLOWED"))
                self.assertFalse(listener.call("GET")[1]["canChange"])
            finally:
                listener.close()
            self.assertEqual(path.read_text(encoding="utf-8"), "{}")

    def test_the_environment_is_reported_honestly(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "enclaves").mkdir()
            env = {"ROS_SECURITY_ENABLE": "true", "ROS_SECURITY_STRATEGY": "Enforce", "ROS_SECURITY_KEYSTORE": folder}
            path = Path(folder) / "bridge.json"
            listener = Listener(settings_for(path, active=True, env=env))
            try:
                body = listener.call("GET")[1]
            finally:
                listener.close()
            self.assertEqual((body["environmentReady"], body["active"], body["requireSros2"], body["restartNeeded"]), (True, True, False, True))


class MainTests(unittest.TestCase):
    def run_main(self, extra: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bridge.json"
            path.write_text("{}", encoding="utf-8")
            err = io.StringIO()
            with mock.patch.dict("os.environ", env or {}, clear=True), mock.patch.object(service, "build_bridge", lambda c: SimpleNamespace(node=None)), mock.patch.object(
                service, "run_forever", lambda *a, **k: None
            ), redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = main(["--config", str(path), *extra])
            return code, err.getvalue()

    def test_the_route_is_closed_by_default(self):
        with mock.patch.object(service, "start_settings_server") as start:
            code, _err = self.run_main([])
        self.assertNotEqual(code, 2)
        start.assert_not_called()

    def test_a_port_without_the_identity_key_is_a_configuration_error(self):
        with mock.patch.object(service, "start_settings_server") as start:
            code, err = self.run_main(["--settings-port", "18999"])
        self.assertEqual(code, 2)
        self.assertIn("identity key", err)
        start.assert_not_called()

    def test_a_bad_port_is_a_configuration_error(self):
        code, err = self.run_main(["--settings-port", "70000"])
        self.assertEqual(code, 2)
        self.assertIn("1 to 65535", err)

    def test_with_the_port_and_the_key_the_route_opens_with_what_the_service_started_with(self):
        with tempfile.TemporaryDirectory() as folder:
            key_file = Path(folder) / "identity.key"
            key_file.write_text("k" * 40, encoding="utf-8")
            with mock.patch.object(service, "start_settings_server") as start:
                code, _err = self.run_main(["--settings-port", "18999", "--identity-key-file", str(key_file), "--allow-settings-change"])
        self.assertNotEqual(code, 2)
        host, port, settings, key = start.call_args.args
        self.assertEqual((host, port, key), ("127.0.0.1", 18999, b"k" * 40))
        self.assertEqual((settings.active, settings.allow_change), (False, True))

    def test_a_port_that_cannot_be_bound_stops_the_service_with_a_clear_error(self):
        with tempfile.TemporaryDirectory() as folder:
            key_file = Path(folder) / "identity.key"
            key_file.write_text("k" * 40, encoding="utf-8")
            with mock.patch.object(service, "start_settings_server", side_effect=OSError("in use")):
                code, err = self.run_main(["--settings-port", "18999", "--identity-key-file", str(key_file)])
        self.assertEqual(code, 2)
        self.assertIn("cannot open the settings route", err)


if __name__ == "__main__":
    unittest.main()
