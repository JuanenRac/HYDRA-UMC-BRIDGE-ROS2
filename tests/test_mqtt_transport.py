# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Real MQTT transport tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Tests Ros2MqttBridge's real topic dispatch.

cmd/job never imports rclpy, so it is fully testable with a plain
FakeNode placeholder. cmd/safe_stop calls straight through to
Ros2SafeStopClient.call() - on a host without a real ROS 2 install this
already degrades to a real, honest "std_srvs is not available" result
(see test_rclpy_transport.py's own documented reasoning: rclpy cannot be
usefully faked at the message-type level, unlike this ecosystem's other
lazily-imported transports)."""

import json
import unittest

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict
from hydra_umc_bridge_ros2 import Ros2MqttBridge
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX


def _rclpy_installed() -> bool:
    try:
        import rclpy  # noqa: F401

        return True
    except ImportError:
        return False


class FakeNode:
    """Never actually dereferenced by cmd/job; cmd/safe_stop only reaches
    it after a real rclpy install is confirmed present (see class docstring
    above) - on this host it never gets past the ImportError guard."""

    def create_client(self, srv_type, srv_name):
        raise AssertionError("not reachable in this environment's test run")

    def create_subscription(self, msg_type, topic, callback, qos_profile):
        raise AssertionError("not reachable in this environment's test run")


def bridge(cell_state=CellState.READY):
    return Ros2MqttBridge(FakeNode(), lambda: cell_state)


def job(phase=JobPhase.LOAD, machine_state=MachineState.IDLE):
    return BridgeJob("job-1", "key-1", "orchestrator", phase, machine_state, {})


class TopicRoutingTests(unittest.TestCase):
    def test_unknown_prefix_is_ignored(self):
        self.assertEqual(bridge().handle_message("some/other/topic", b""), [])

    def test_unrecognised_cmd_topic_is_ignored_not_an_error(self):
        self.assertEqual(bridge().handle_message(f"{TOPIC_PREFIX}cmd/move", b""), [])


class JobCommandTests(unittest.TestCase):
    def test_a_valid_load_job_maps_to_the_real_job_action_interface(self):
        publishes = bridge().handle_message(f"{TOPIC_PREFIX}cmd/job", json.dumps(job_to_dict(job())).encode("utf-8"))
        self.assertEqual(publishes[0].topic, f"{TOPIC_PREFIX}cmd/job/result")
        dispatch = json.loads(publishes[0].payload)
        self.assertTrue(dispatch["accepted"])
        self.assertEqual(dispatch["interface"], "/hydra_umc/execute_cell_job")
        self.assertEqual(dispatch["mode"], "plan-only")

    def test_an_abort_maps_to_the_real_safe_stop_service_interface(self):
        payload = job_to_dict(job(phase=JobPhase.ABORT, machine_state=MachineState.FAULT))
        publishes = bridge(cell_state=CellState.FAULT).handle_message(
            f"{TOPIC_PREFIX}cmd/job", json.dumps(payload).encode("utf-8")
        )
        dispatch = json.loads(publishes[0].payload)
        self.assertTrue(dispatch["accepted"])
        self.assertEqual(dispatch["interface"], "/hydra_umc/request_safe_stop")

    def test_a_job_against_a_non_ready_cell_is_rejected(self):
        publishes = bridge(cell_state=CellState.FAULT).handle_message(
            f"{TOPIC_PREFIX}cmd/job", json.dumps(job_to_dict(job())).encode("utf-8")
        )
        dispatch = json.loads(publishes[0].payload)
        self.assertFalse(dispatch["accepted"])

    def test_malformed_json_fails_closed_with_a_real_result_not_a_crash(self):
        publishes = bridge().handle_message(f"{TOPIC_PREFIX}cmd/job", b"{not valid json")
        dispatch = json.loads(publishes[0].payload)
        self.assertFalse(dispatch["accepted"])
        self.assertEqual(dispatch["mode"], "plan-only")
        self.assertIn("malformed job payload", dispatch["reason"])


class SafeStopCommandTests(unittest.TestCase):
    def test_safe_stop_degrades_honestly_without_a_real_ros2_install(self):
        if _rclpy_installed():
            self.skipTest("rclpy is installed in this environment - nothing to prove here")
        publishes = bridge().handle_message(f"{TOPIC_PREFIX}cmd/safe_stop", b"")
        self.assertEqual(publishes[0].topic, f"{TOPIC_PREFIX}cmd/safe_stop/result")
        result = json.loads(publishes[0].payload)
        self.assertFalse(result["called"])
        self.assertIn("std_srvs is not available", result["reason"])


class RunForeverTests(unittest.TestCase):
    def test_missing_paho_mqtt_raises_a_clear_runtime_error_not_an_import_error(self):
        try:
            import paho.mqtt.client  # noqa: F401

            self.skipTest("paho-mqtt is installed in this environment - nothing to prove here")
        except ImportError:
            pass
        from hydra_umc_bridge_ros2 import run_forever

        with self.assertRaises(RuntimeError) as context:
            run_forever(bridge(), "127.0.0.1")
        self.assertIn("paho-mqtt is not installed", str(context.exception))


if __name__ == "__main__":
    unittest.main()
