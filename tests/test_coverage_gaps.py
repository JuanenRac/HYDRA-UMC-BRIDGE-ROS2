# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Tests of the branches the other suites leave open
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Branches that the coverage report of the CI job shows as open: the private key inside a git working tree, the MQTT client module that is
not installed, the ROS 2 node made on a graph that is not started yet, a spin that fails, an extension module whose own import fails, a message
handler that raises on the network thread and the `python -m` entry point. Nothing here needs openssl, rclpy or a broker."""
import importlib
import logging
import os
import runpy
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from hydra_umc_bridge_ros2 import extensions, mtls
from hydra_umc_bridge_ros2 import mqtt_transport as transport
from hydra_umc_bridge_ros2.mtls import MutualTlsError, MutualTlsSettings
from hydra_umc_bridge_ros2.rclpy_transport import Ros2SafeStopClient, create_ros2_node


class KeyInsideAGitTreeTests(unittest.TestCase):
    def test_a_private_key_inside_a_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            files = {}
            for name in ("ca.pem", "cert.pem", "key.pem"):
                files[name] = root / name
                files[name].write_text("x", encoding="utf-8")
            if os.name != "nt":
                os.chmod(files["key.pem"], stat.S_IRUSR | stat.S_IWUSR)
            settings = MutualTlsSettings(True, str(files["ca.pem"]), str(files["cert.pem"]), str(files["key.pem"]))
            with self.assertRaises(MutualTlsError) as refused:
                mtls.check_files(settings)
            self.assertIn("git working tree", str(refused.exception))

    @unittest.skipIf(os.name == "nt", "the mode bits of a key are a POSIX rule")
    def test_a_key_that_others_can_read_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("ca.pem", "cert.pem", "key.pem"):
                (root / name).write_text("x", encoding="utf-8")
            os.chmod(root / "key.pem", 0o644)
            settings = MutualTlsSettings(True, str(root / "ca.pem"), str(root / "cert.pem"), str(root / "key.pem"))
            with mock.patch.object(mtls, "_inside_git_tree", return_value=False), self.assertRaises(MutualTlsError) as refused:
                mtls.check_files(settings)
            self.assertIn("0600", str(refused.exception))


class MqttModuleTests(unittest.TestCase):
    def test_without_the_mqtt_library_the_error_says_what_to_install(self):
        settings = MutualTlsSettings(True, "/ca", "/cert", "/key")
        with mock.patch.object(mtls, "build_context", return_value=object()), mock.patch.dict(sys.modules, {"paho.mqtt.client": None}):
            with self.assertRaises(RuntimeError) as refused:
                mtls.mqtt_module_for(settings, env={})
        self.assertIn("paho-mqtt is not installed", str(refused.exception))

    def test_off_means_nothing_changes_and_a_given_module_is_wrapped(self):
        self.assertIsNone(mtls.mqtt_module_for(MutualTlsSettings(), env={}))
        made = []

        class Real:
            CallbackAPIVersion = "v2"

            @staticmethod
            def Client(*args, **kwargs):  # noqa: N802 - the name paho uses
                client = types.SimpleNamespace(args=args, context=None)
                client.tls_set_context = lambda context: setattr(client, "context", context)
                made.append(client)
                return client

        marker = object()
        with mock.patch.object(mtls, "build_context", return_value=marker):
            module = mtls.mqtt_module_for(MutualTlsSettings(True, "/ca", "/cert", "/key"), env={}, real=Real)
        client = module.Client("id")  # type: ignore[attr-defined]
        self.assertIs(client.context, marker)
        self.assertEqual(module.CallbackAPIVersion, "v2")  # type: ignore[attr-defined]


def _fake_rclpy(ok: bool):
    calls = []
    rclpy = types.ModuleType("rclpy")
    rclpy.ok = lambda: ok  # type: ignore[attr-defined]
    rclpy.init = lambda: calls.append("init")  # type: ignore[attr-defined]
    node_module = types.ModuleType("rclpy.node")

    class Node:
        def __init__(self, name):
            self.name = name

    node_module.Node = Node  # type: ignore[attr-defined]
    rclpy.node = node_module  # type: ignore[attr-defined]
    return {"rclpy": rclpy, "rclpy.node": node_module}, calls


class CreateNodeTests(unittest.TestCase):
    def test_a_graph_that_is_not_started_is_started_before_the_node_is_made(self):
        modules, calls = _fake_rclpy(ok=False)
        with mock.patch.dict(sys.modules, modules):
            node = create_ros2_node("bridge_test")
        self.assertEqual(calls, ["init"])
        self.assertEqual(node.name, "bridge_test")

    def test_a_graph_that_is_started_is_left_alone(self):
        modules, calls = _fake_rclpy(ok=True)
        with mock.patch.dict(sys.modules, modules):
            create_ros2_node("bridge_test")
        self.assertEqual(calls, [])

    def test_without_rclpy_the_error_is_a_runtime_error_with_the_cause(self):
        with mock.patch.dict(sys.modules, {"rclpy": None}):
            with self.assertRaises(RuntimeError) as refused:
                create_ros2_node("bridge_test")
        self.assertIsInstance(refused.exception.__cause__, ImportError)


class SpinFailureTests(unittest.TestCase):
    def test_a_spin_that_raises_is_reported_as_a_call_that_was_made_and_failed(self):
        class Future:
            def done(self):
                return False

        class Client:
            def service_is_ready(self):
                return True

            def call_async(self, request):
                return Future()

        class Node:
            def create_client(self, trigger, name):
                return Client()

        def spin(node, future, timeout_sec=None):
            raise RuntimeError("the executor is gone")

        rclpy = types.ModuleType("rclpy")
        rclpy.spin_until_future_complete = spin  # type: ignore[attr-defined]
        std_srvs = types.ModuleType("std_srvs")
        srv = types.ModuleType("std_srvs.srv")

        class Trigger:
            class Request:
                pass

        srv.Trigger = Trigger  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"rclpy": rclpy, "std_srvs": std_srvs, "std_srvs.srv": srv}):
            result = Ros2SafeStopClient().call(Node(), "/stop", timeout_sec=0.1)
        self.assertTrue(result.called)
        self.assertFalse(result.success)
        self.assertIn("the executor is gone", result.reason)


class ExtensionImportTests(unittest.TestCase):
    def test_an_extension_whose_own_import_fails_is_not_reported_as_unknown(self):
        failure = ModuleNotFoundError("No module named 'nav_msgs_not_there'", name="nav_msgs_not_there")
        with mock.patch.object(importlib, "import_module", side_effect=failure):
            with self.assertRaises(ModuleNotFoundError):
                extensions.load_module("motion")


class InlineHandlerTests(unittest.TestCase):
    def test_a_handler_that_raises_on_the_network_thread_is_logged_and_the_loop_goes_on(self):
        bridge = types.SimpleNamespace()

        def broken(topic, payload, retained=False):
            raise RuntimeError("one bad message")

        bridge.handle_message = broken
        dispatcher = transport.MessageDispatcher(bridge)
        self.addCleanup(dispatcher.close)
        client = types.SimpleNamespace(publish=lambda *a, **k: self.fail("nothing may be published"))
        message = types.SimpleNamespace(topic="plant/feed/in", payload=b"{}", retain=True)
        with self.assertLogs(transport.__name__, logging.ERROR) as logged:
            dispatcher._run_inline(client, "plant/feed/in", message)
        self.assertIn("could not handle plant/feed/in", logged.output[0])


class EntryPointTests(unittest.TestCase):
    def test_python_dash_m_runs_the_service_main_and_exits_with_its_code(self):
        with mock.patch("hydra_umc_bridge_ros2.service.main", return_value=0) as main:
            with self.assertRaises(SystemExit) as stop:
                runpy.run_module("hydra_umc_bridge_ros2", run_name="__main__")
        self.assertEqual(stop.exception.code, 0)
        main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
