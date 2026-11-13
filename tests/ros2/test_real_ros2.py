# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Tests against a real ROS 2 (run in the container workflow, skipped where rclpy is missing)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""What the host tests cannot show: the interface package built by colcon, a real rclpy node, a real state topic, a real safe stop service.

Run by `.github/workflows/ros2.yml` inside a ROS 2 container after `colcon build` of `interfaces/` and the sourcing of its install folder. Where `rclpy` or the built
package is not importable every test here skips itself, saying why. Evidence: a ROS 2 container with the nodes of this test, never a robot, a controller or a real installation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, QoSProfile
    from std_msgs.msg import String
    from std_srvs.srv import Trigger
except ImportError:  # no ROS 2 on this host
    rclpy = None

try:
    from hydra_umc_interfaces.action import ExecuteCellJob
    from hydra_umc_interfaces.msg import CellStatus
    from hydra_umc_interfaces.srv import InspectCell
except ImportError:  # the interface package is not built/sourced
    ExecuteCellJob = CellStatus = InspectCell = None

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict

from hydra_umc_bridge_ros2 import Ros2MqttBridge, bridge_state_to_mqtt, spin_node_in_background
from hydra_umc_bridge_ros2.coordinator import Ros2Coordinator
from hydra_umc_bridge_ros2.interface_contract import load_package
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX

NEEDS_ROS = unittest.skipIf(rclpy is None, "rclpy is not installed")
NEEDS_INTERFACES = unittest.skipIf(rclpy is None or InspectCell is None, "the hydra_umc_interfaces package is not built and sourced")


def wait_for(condition, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


def setUpModule() -> None:
    if rclpy is not None and not rclpy.ok():
        rclpy.init()


def tearDownModule() -> None:
    if rclpy is not None and rclpy.ok():
        rclpy.shutdown()


def message(phase: JobPhase, key: str) -> bytes:
    return json.dumps(job_to_dict(BridgeJob("job-1", key, "orchestrator", phase, MachineState.IDLE, {}))).encode()


def answer(publishes) -> dict:
    return json.loads(publishes[0].payload)


@NEEDS_INTERFACES
class InterfacePackageTests(unittest.TestCase):
    """The package built by colcon has exactly the fields and constants of the files the host tests check."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.package = load_package(REPO / "interfaces" / "hydra_umc_interfaces")

    def names(self, generated) -> list[str]:
        return list(generated.get_fields_and_field_types())

    def test_the_service_has_the_fields_of_its_file(self) -> None:
        service = self.package["hydra_umc_interfaces/srv/InspectCell"]
        self.assertEqual(self.names(InspectCell.Request), [f.name for f in service.sections[0].fields])
        self.assertEqual(self.names(InspectCell.Response), [f.name for f in service.sections[1].fields])

    def test_the_action_has_the_fields_of_its_file(self) -> None:
        action = self.package["hydra_umc_interfaces/action/ExecuteCellJob"]
        self.assertEqual(self.names(ExecuteCellJob.Goal), [f.name for f in action.sections[0].fields])
        self.assertEqual(self.names(ExecuteCellJob.Result), [f.name for f in action.sections[1].fields])
        self.assertEqual(self.names(ExecuteCellJob.Feedback), [f.name for f in action.sections[2].fields])
        for name, value in action.sections[0].constants.items():
            self.assertEqual(getattr(ExecuteCellJob.Goal, name), value)

    def test_the_message_has_the_fields_and_constants_of_its_file(self) -> None:
        status = self.package["hydra_umc_interfaces/msg/CellStatus"]
        self.assertEqual(self.names(CellStatus), [f.name for f in status.sections[0].fields])
        for name, value in status.sections[0].constants.items():
            self.assertEqual(getattr(CellStatus, name), value)
        self.assertEqual(CellStatus.CELL_READY, CellState.READY.value)
        self.assertEqual(CellStatus.MACHINE_IDLE, MachineState.IDLE.value)

    def test_no_generated_type_carries_a_motion(self) -> None:
        for generated in (InspectCell.Request, InspectCell.Response, ExecuteCellJob.Goal, ExecuteCellJob.Result, ExecuteCellJob.Feedback, CellStatus):
            for name in self.names(generated):
                for word in ("pose", "trajectory", "velocity", "joint", "twist", "position", "torque"):
                    self.assertNotIn(word, name)

    def test_a_goal_can_be_filled_like_a_job(self) -> None:
        goal = ExecuteCellJob.Goal(job_id="job-1", idempotency_key="k", source="orchestrator", phase=ExecuteCellJob.Goal.PHASE_LOAD, parameter_keys=["a"], parameter_values=["b"])
        self.assertEqual(goal.phase, JobPhase.LOAD.value)
        self.assertEqual(Ros2Coordinator.job_action, "/hydra_umc/execute_cell_job")


class _Rig:
    """A node for the bridge, spun on its own executor, and a node that plays the machine (state publisher) and the safe stop service."""

    def __init__(self, max_age: float | None, abort_calls_safe_stop: bool = True) -> None:
        self.stop_calls = 0
        self.mqtt: list[tuple[str, str, bool]] = []
        self.bridge_node = rclpy.create_node("hy_test_bridge")
        self.machine_node = rclpy.create_node("hy_test_machine")
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.state_pub = self.machine_node.create_publisher(String, Ros2Coordinator.state_topic, qos)
        self.service = self.machine_node.create_service(Trigger, Ros2Coordinator.safe_stop_service, self._on_stop)
        self.machine_executor = SingleThreadedExecutor()
        self.machine_executor.add_node(self.machine_node)
        self.machine_thread = threading.Thread(target=self.machine_executor.spin, daemon=True)
        self.machine_thread.start()
        self.bridge = Ros2MqttBridge(self.bridge_node, lambda: CellState.READY, node_is_spinning=True, observed_max_age_seconds=max_age, abort_calls_safe_stop=abort_calls_safe_stop)
        self.spinner = spin_node_in_background(self.bridge_node)
        self.bridge.start_observing()
        subscribed = bridge_state_to_mqtt(self.bridge_node, lambda topic, payload, retain: self.mqtt.append((topic, payload, retain)), self.bridge.observe)
        assert subscribed.subscribed, subscribed.reason

    def _on_stop(self, request, response):
        self.stop_calls += 1
        response.success = True
        response.message = "stopped by the test machine"
        return response

    def say(self, state: str) -> None:
        self.state_pub.publish(String(data=state))

    def close(self) -> None:
        self.spinner.stop()
        self.machine_executor.shutdown()
        self.machine_thread.join(2.0)
        self.bridge_node.destroy_node()
        self.machine_node.destroy_node()


@NEEDS_ROS
class RealGraphTests(unittest.TestCase):
    def test_the_state_of_the_machine_reaches_the_gate_and_expires_without_a_heartbeat(self) -> None:
        rig = _Rig(max_age=1.0)
        self.addCleanup(rig.close)
        rig.say("IDLE")
        self.assertTrue(wait_for(lambda: rig.bridge._observed is MachineState.IDLE), "the state was not heard")
        self.assertTrue(any(topic == f"{TOPIC_PREFIX}state" and retain for topic, _p, retain in rig.mqtt), "the state was not republished retained")
        self.assertTrue(answer(rig.bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.LOAD, "k1")))["allowed"])
        # the publisher stops repeating the state: after the limit the gate refuses as OFFLINE
        time.sleep(1.5)
        stale = answer(rig.bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.LOAD, "k2")))
        self.assertFalse(stale["allowed"])
        self.assertIn("OFFLINE", stale["reason"])
        # a heartbeat of the publisher brings it back
        rig.say("IDLE")
        self.assertTrue(wait_for(lambda: answer(rig.bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.LOAD, "k3")))["allowed"]))

    def test_the_safe_stop_service_is_called_by_the_command_and_by_an_abort_job(self) -> None:
        rig = _Rig(max_age=None)
        self.addCleanup(rig.close)
        stop = answer(rig.bridge.handle_message(f"{TOPIC_PREFIX}cmd/safe_stop", b""))
        self.assertTrue(stop["called"])
        self.assertTrue(stop["success"])
        self.assertEqual(rig.stop_calls, 1)
        abort = answer(rig.bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.ABORT, "abort-1")))
        self.assertTrue(abort["allowed"])
        self.assertTrue(abort["executed"], abort)
        self.assertEqual(abort["mode"], "send")
        self.assertEqual(rig.stop_calls, 2)

    def test_an_abort_job_only_plans_when_the_switch_is_off(self) -> None:
        rig = _Rig(max_age=None, abort_calls_safe_stop=False)
        self.addCleanup(rig.close)
        abort = answer(rig.bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.ABORT, "abort-2")))
        self.assertEqual(abort["mode"], "plan-only")
        self.assertEqual(rig.stop_calls, 0)


@NEEDS_ROS
class EntryPointTests(unittest.TestCase):
    def run_snippet(self, code: str, *ros_arguments: str) -> dict:
        completed = subprocess.run([sys.executable, "-c", code, *ros_arguments], capture_output=True, text=True, timeout=60, env=dict(os.environ), cwd=str(REPO))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    SNIPPET = (
        "import json, sys, rclpy\n"
        "from hydra_umc_bridge_ros2.service import build_bridge, load_service_config\n"
        "rclpy.init(args=sys.argv)\n"
        "bridge = build_bridge(load_service_config('docs/deploy/bridge.json'))\n"
        "print(json.dumps({'age': bridge._observed_max_age, 'abort': bridge.abort_calls_safe_stop, 'node': bridge.node.get_name()}))\n"
        "bridge.node.destroy_node()\n"
        "rclpy.shutdown()\n"
    )

    def test_the_entry_point_creates_the_node_with_the_values_of_the_configuration(self) -> None:
        self.assertEqual(self.run_snippet(self.SNIPPET), {"age": 2.0, "abort": True, "node": "hydra_umc_bridge_ros2"})

    def test_the_ros_parameter_file_of_the_repository_is_read_by_the_node(self) -> None:
        shipped = self.run_snippet(self.SNIPPET, "--ros-args", "--params-file", str(REPO / "docs" / "deploy" / "bridge_params.yaml"))
        self.assertEqual(shipped, {"age": 2.0, "abort": True, "node": "hydra_umc_bridge_ros2"})

    def test_a_parameter_file_changes_the_values(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "params.yaml")
            path.write_text("hydra_umc_bridge_ros2:\n  ros__parameters:\n    observed_max_age_s: 7.0\n    abort_calls_safe_stop: false\n", encoding="utf-8")
            changed = self.run_snippet(self.SNIPPET, "--ros-args", "--params-file", str(path))
        self.assertEqual(changed, {"age": 7.0, "abort": False, "node": "hydra_umc_bridge_ros2"})

    def test_the_launch_file_describes_the_service_process(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("hydra_launch", REPO / "docs" / "deploy" / "hydra_umc_bridge_ros2.launch.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        description = module.generate_launch_description()
        self.assertEqual(len(description.entities), 3)  # two arguments and the process


if __name__ == "__main__":
    unittest.main()
