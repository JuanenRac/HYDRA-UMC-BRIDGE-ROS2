# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Service entry point, state expiry, ABORT and the shared job intake (no ROS 2 needed)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The service configuration and its ROS parameters, the 2 s default for the age of the machine state, an ABORT job that calls the safe stop service, and the shared job intake.

Everything here runs on a host without ROS 2; the same behaviour against a real rclpy node is checked by `tests/ros2/` in the container workflow.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict
from hydra_umc_sdk.job_intake import IdempotencyStore

from hydra_umc_bridge_ros2 import Ros2MqttBridge
from hydra_umc_bridge_ros2.mqtt_transport import (
    DEFAULT_OBSERVED_MAX_AGE_SECONDS,
    OBSERVED_MAX_AGE_ENVIRONMENT,
    TOPIC_PREFIX,
    observed_max_age_from_environment,
)
from hydra_umc_bridge_ros2.rclpy_transport import SafeStopResult
from hydra_umc_bridge_ros2.service import ConfigError, NodeConfig, apply_ros_parameters, build_bridge, load_service_config, main

REPO = Path(__file__).resolve().parent.parent


class FakeNode:
    pass


class FakeStop:
    def __init__(self, result: SafeStopResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def call(self, node: object, service_name: str, *, timeout_sec: float = 2.0) -> SafeStopResult:
        self.calls.append(service_name)
        return self.result


def message(phase: JobPhase = JobPhase.LOAD, key: str = "key-1") -> bytes:
    return json.dumps(job_to_dict(BridgeJob("job-1", key, "orchestrator", phase, MachineState.IDLE, {}))).encode()


def answer(publishes) -> dict:
    return json.loads(publishes[0].payload)


def make(observed="IDLE", **kwargs) -> Ros2MqttBridge:
    bridge = Ros2MqttBridge(FakeNode(), lambda: CellState.READY, **kwargs)  # type: ignore[arg-type]
    bridge.start_observing()
    if observed is not None:
        bridge.observe(observed)
    return bridge


class StaleStateDefaultTests(unittest.TestCase):
    def test_the_state_expires_after_two_seconds_unless_told_otherwise(self) -> None:
        now = [100.0]
        bridge = make(clock=lambda: now[0])
        self.assertEqual(DEFAULT_OBSERVED_MAX_AGE_SECONDS, 2.0)
        self.assertTrue(answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message()))["allowed"])
        now[0] += 1.9
        self.assertTrue(answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(key="k2")))["allowed"])
        now[0] += 0.2  # the publisher died after saying IDLE
        stale = answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(key="k3")))
        self.assertFalse(stale["allowed"])
        self.assertIn("OFFLINE", stale["reason"])
        bridge.observe("IDLE")  # a heartbeat of the publisher
        self.assertTrue(answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(key="k4")))["allowed"])

    def test_the_environment_can_change_or_switch_off_the_limit(self) -> None:
        self.assertEqual(observed_max_age_from_environment({}), 2.0)
        self.assertEqual(observed_max_age_from_environment({OBSERVED_MAX_AGE_ENVIRONMENT: "5"}), 5.0)
        self.assertIsNone(observed_max_age_from_environment({OBSERVED_MAX_AGE_ENVIRONMENT: "0"}))
        for bad in ("abc", "-1", "nan", "inf"):
            with self.subTest(bad), self.assertRaises(ValueError):
                observed_max_age_from_environment({OBSERVED_MAX_AGE_ENVIRONMENT: bad})


class AbortTests(unittest.TestCase):
    def test_an_abort_job_calls_the_real_safe_stop_service(self) -> None:
        stop = FakeStop(SafeStopResult(True, True, "stopped"))
        bridge = make(safe_stop_client=stop)  # type: ignore[arg-type]
        body = answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.ABORT)))
        self.assertEqual(stop.calls, ["/hydra_umc/request_safe_stop"])
        self.assertTrue(body["allowed"])
        self.assertTrue(body["executed"])
        self.assertEqual(body["mode"], "send")
        self.assertIn("was called", body["reason"])
        self.assertEqual(body["safe_stop"]["success"], True)

    def test_a_stop_that_is_not_confirmed_is_reported_not_hidden(self) -> None:
        for result in (SafeStopResult(False, False, "service is not available"), SafeStopResult(True, False, "timed out")):
            with self.subTest(result.reason):
                bridge = make(safe_stop_client=FakeStop(result))  # type: ignore[arg-type]
                body = answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.ABORT)))
                self.assertTrue(body["allowed"])
                self.assertFalse(body["executed"])
                self.assertIn(result.reason, body["reason"])

    def test_the_stop_is_asked_again_for_a_repeated_abort_but_other_jobs_are_not_repeated(self) -> None:
        stop = FakeStop(SafeStopResult(True, True, "stopped"))
        bridge = make(idempotency=IdempotencyStore(), safe_stop_client=stop)  # type: ignore[arg-type]
        for _ in range(2):
            self.assertTrue(answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.ABORT, "abort-1")))["executed"])
        self.assertEqual(len(stop.calls), 2)
        first = answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.LOAD, "load-1")))
        again = answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.LOAD, "load-1")))
        self.assertEqual(first, again)

    def test_the_switch_makes_an_abort_only_name_the_service(self) -> None:
        stop = FakeStop(SafeStopResult(True, True, "stopped"))
        bridge = make(safe_stop_client=stop, abort_calls_safe_stop=False)  # type: ignore[arg-type]
        body = answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.ABORT)))
        self.assertEqual(stop.calls, [])
        self.assertEqual(body["mode"], "plan-only")

    def test_the_global_stop_still_lets_an_abort_through(self) -> None:
        from hydra_umc_sdk.global_stop import GlobalStopGuard, GlobalStopSettings

        guard = GlobalStopGuard(GlobalStopSettings(enabled=True))
        guard.observe(json.dumps({"state": "SAFE_STOP"}).encode())
        stop = FakeStop(SafeStopResult(True, True, "stopped"))
        bridge = make(safe_stop_client=stop, global_stop=guard)  # type: ignore[arg-type]
        self.assertTrue(answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.ABORT)))["executed"])
        self.assertFalse(answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.LOAD, "load-2")))["allowed"])


class IntakeTests(unittest.TestCase):
    def test_malformed_jobs_are_refused_with_a_reason(self) -> None:
        bridge = make()
        for payload in (b"not json", b"[1, 2]", b'{"job_id": "x"}', b"\xff\xfe"):
            with self.subTest(payload):
                body = answer(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", payload))
                self.assertFalse(body["allowed"])
                self.assertIn("malformed", body["reason"])

    def test_a_repeated_key_gets_the_stored_answer_and_is_not_run_twice(self) -> None:
        bridge = make(idempotency=IdempotencyStore())
        first = bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(key="same"))
        second = bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(key="same"))
        self.assertEqual(first[0].payload, second[0].payload)

    def test_a_retained_job_is_ignored(self) -> None:
        self.assertEqual(make().handle_message(f"{TOPIC_PREFIX}cmd/job", message(), retained=True), [])


class ServiceConfigTests(unittest.TestCase):
    def test_the_shipped_configuration_is_the_conservative_one(self) -> None:
        config = load_service_config(REPO / "docs" / "deploy" / "bridge.json")
        self.assertEqual(config.node.observed_max_age_s, 2.0)
        self.assertTrue(config.node.abort_calls_safe_stop)
        self.assertEqual(config, load_service_config(None))

    def test_bad_values_are_errors(self) -> None:
        bad = [
            {"mqtt": {"port": 0}},
            {"mqtt": {"host": 1}},
            {"mqtt": {"surprise": 1}},
            {"node": {"name": "bad-name"}},
            {"node": {"observed_max_age_s": -1}},
            {"node": {"abort_calls_safe_stop": "yes"}},
            {"other": {}},
            [],
        ]
        for body in bad:
            with self.subTest(body), tempfile.TemporaryDirectory() as folder:
                path = Path(folder, "c.json")
                path.write_text(json.dumps(body), encoding="utf-8")
                with self.assertRaises(ConfigError):
                    load_service_config(path)

    def test_ros_parameters_override_the_configuration_and_are_checked(self) -> None:
        class Node:
            def __init__(self, **given: object) -> None:
                self.given = given

            def declare_parameter(self, name: str, default: object) -> SimpleNamespace:
                return SimpleNamespace(value=self.given.get(name, default))

        base = NodeConfig()
        self.assertEqual(apply_ros_parameters(Node(), base), base)
        merged = apply_ros_parameters(Node(observed_max_age_s=5, abort_calls_safe_stop=False), base)
        self.assertEqual((merged.observed_max_age_s, merged.abort_calls_safe_stop), (5.0, False))
        for given in ({"observed_max_age_s": "fast"}, {"observed_max_age_s": True}, {"abort_calls_safe_stop": "no"}, {"observed_max_age_s": -3.0}):
            with self.subTest(given), self.assertRaises(ConfigError):
                apply_ros_parameters(Node(**given), base)

    def test_build_bridge_wires_the_node_the_limit_and_the_abort_switch(self) -> None:
        class Node:
            def declare_parameter(self, name: str, default: object) -> SimpleNamespace:
                return SimpleNamespace(value=default)

        names: list[str] = []

        def factory(name: str) -> Node:
            names.append(name)
            return Node()

        config = load_service_config(None)
        bridge = build_bridge(config, factory)
        self.assertEqual(names, ["hydra_umc_bridge_ros2"])
        self.assertEqual(bridge._observed_max_age, 2.0)
        self.assertTrue(bridge.abort_calls_safe_stop)
        self.assertIs(bridge._cell_state(), CellState.READY)
        bridge.observe("FAULT")
        self.assertIs(bridge._cell_state(), CellState.FAULT)
        bridge.observe("SAFE_STOP")
        self.assertIs(bridge._cell_state(), CellState.SAFE_STOP)

    def test_a_missing_ros_install_is_a_clear_error_and_check_validates_without_opening(self) -> None:
        self.assertEqual(main(["--check", "--config", str(REPO / "docs" / "deploy" / "bridge.json")]), 0)
        self.assertEqual(main(["--check", "--config", str(REPO / "docs" / "deploy" / "no-such-file.json"), "--ros-args", "-p", "x:=1"]), 0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "c.json")
            path.write_text(json.dumps({"mqtt": {"port": 0}}), encoding="utf-8")
            self.assertEqual(main(["--config", str(path)]), 2)
            ok = Path(folder, "ok.json")
            ok.write_text("{}", encoding="utf-8")
            try:
                import rclpy  # noqa: F401
            except ImportError:
                self.assertEqual(main(["--config", str(ok)]), 3)


class DeploymentFilesTests(unittest.TestCase):
    def test_the_launch_file_compiles_and_names_the_service_and_its_files(self) -> None:
        text = (REPO / "docs" / "deploy" / "hydra_umc_bridge_ros2.launch.py").read_text(encoding="utf-8")
        compile(text, "launch", "exec")
        for needle in ("hydra_umc_bridge_ros2", "--params-file", "bridge_params.yaml", "bridge.json"):
            self.assertIn(needle, text)

    def test_the_parameter_file_holds_the_two_parameters_of_the_node(self) -> None:
        text = (REPO / "docs" / "deploy" / "bridge_params.yaml").read_text(encoding="utf-8")
        self.assertIn("hydra_umc_bridge_ros2:", text)
        self.assertIn("ros__parameters:", text)
        self.assertIn("observed_max_age_s: 2.0", text)
        self.assertIn("abort_calls_safe_stop: true", text)

    def test_the_systemd_unit_restarts_on_failure_and_does_not_run_as_root(self) -> None:
        text = (REPO / "docs" / "deploy" / "hydra-umc-bridge-ros2.service").read_text(encoding="utf-8")
        for needle in ("Restart=on-failure", "User=hydra-umc", "NoNewPrivileges=true", "-m hydra_umc_bridge_ros2", "HYDRA_UMC_ROS2_CONFIG"):
            self.assertIn(needle, text)
        self.assertNotIn("User=root", text)


if __name__ == "__main__":
    unittest.main()
