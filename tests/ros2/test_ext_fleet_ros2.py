# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Fleet extension against a real ROS 2 (container workflow, skipped where rclpy is missing)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A mixed fleet in the ROS 2 container: one ROS 2 vehicle on a real graph (the real state topic and the real safe-stop service) and one VDA 5050 AMR played by a simulated AMR bridge of the test.

The simulated AMR bridge checks the signature with the SDK and its own gate, like the real one; it is not HYDRA-UMC-BRIDGE-AMR and not a vehicle. Seeded commands with injected faults must reach each vehicle exactly
when every condition holds. Evidence: a ROS 2 container with the nodes of this test, never a robot, an AMR or a fleet manager.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, QoSProfile
    from std_msgs.msg import String
    from std_srvs.srv import Trigger
except ImportError:  # no ROS 2 on this host
    rclpy = None

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_from_dict, job_to_dict
from hydra_umc_sdk.global_stop import GlobalStopGuard, GlobalStopSettings
from hydra_umc_sdk.signed_commands import sign_command, verify_command

from hydra_umc_bridge_ros2 import Ros2MqttBridge, spin_node_in_background
from hydra_umc_bridge_ros2.coordinator import Ros2Coordinator
from hydra_umc_bridge_ros2.ext_fleet import JOB_VERB, RESULT_TOPIC, STATE_TOPIC, build, parse
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX

NEEDS_ROS = unittest.skipIf(rclpy is None, "rclpy is not installed")
KEY = b"0123456789abcdef0123456789abcdef-fleet-in"
AMR_KEY = b"fedcba9876543210fedcba9876543210-amr-out"
SEED = 20260
COMMAND = f"{TOPIC_PREFIX}{JOB_VERB}"
AMR = "hydra/bridges/amr/"
READY = b'{"state": "READY"}'
STOPPED = b'{"state": "SAFE_STOP"}'


def wait_for(condition, timeout: float = 15.0) -> bool:
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


def job(key: str, phase: JobPhase = JobPhase.PROCESS, vehicle: str | None = "amr_1", station: str | None = "dock_a") -> BridgeJob:
    parameters = {}
    if vehicle is not None:
        parameters["vehicle"] = vehicle
    if station is not None:
        parameters["station"] = station
    return BridgeJob(f"job-{key}", key, "orchestrator", phase, MachineState.IDLE, parameters)


def signed(item: BridgeJob, key: bytes = KEY) -> bytes:
    return json.dumps(sign_command(COMMAND, job_to_dict(item), key)).encode()


class SimulatedAmrBridge:
    """The AMR bridge as the extension sees it: it verifies the signature of the job with the SDK, gates it on the state its vehicle reported and answers on `cmd/job/result`."""

    def __init__(self) -> None:
        self.machine = MachineState.IDLE
        self.accepted: list[BridgeJob] = []

    def receive(self, publish) -> dict | None:
        body = json.loads(publish.payload)
        if not verify_command(publish.topic, body, AMR_KEY, now_ms=int(time.time() * 1000)).ok:
            return {"allowed": False, "reason": "unsigned", "job_id": body.get("job_id")}
        item = job_from_dict({name: value for name, value in body.items() if name not in ("issued_at_ms", "expires_in_ms", "signature")})
        if item.phase is JobPhase.ABORT or (self.machine is MachineState.IDLE and item.parameters.get("goal_node")):
            self.accepted.append(item)
            return {"allowed": True, "reason": "published", "job_id": item.job_id}
        return {"allowed": False, "reason": "refused by the vehicle gate", "job_id": item.job_id}


@NEEDS_ROS
class RealFleetTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        for name, content in (("in.key", KEY), ("amr.key", AMR_KEY)):
            (root / name).write_bytes(content)
            os.chmod(root / name, 0o600)
        (root / "fleet.json").write_text(json.dumps({"vehicles": {"amr_1": {"kind": "vda5050", "stations": {"dock_a": "N12"}}, "arm_1": {"kind": "ros2"}}}), encoding="utf-8")
        self.stop_calls = 0
        self.bridge_node = rclpy.create_node("hy_fleet_bridge")
        self.machine_node = rclpy.create_node("hy_fleet_machine")
        self.state_pub = self.machine_node.create_publisher(String, Ros2Coordinator.state_topic, QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.service = self.machine_node.create_service(Trigger, Ros2Coordinator.safe_stop_service, self._on_stop)
        self.machine_executor = SingleThreadedExecutor()
        self.machine_executor.add_node(self.machine_node)
        self.machine_thread = threading.Thread(target=self.machine_executor.spin, daemon=True)
        self.machine_thread.start()
        self.guard = GlobalStopGuard(GlobalStopSettings(True))
        self.guard.observe(READY)
        self.bridge = Ros2MqttBridge(self.bridge_node, lambda: CellState.READY, node_is_spinning=True, observed_max_age_seconds=None, global_stop=self.guard)
        self.spinner = spin_node_in_background(self.bridge_node)
        self.bridge.start_observing()
        self.mqtt: list[tuple[str, str, bool]] = []
        from hydra_umc_bridge_ros2 import bridge_state_to_mqtt

        subscribed = bridge_state_to_mqtt(self.bridge_node, lambda topic, payload, retain: self.mqtt.append((topic, payload, retain)), self.bridge.observe)
        assert subscribed.subscribed, subscribed.reason
        settings = parse(
            {
                "enabled": True,
                "fleet_file": str(root / "fleet.json"),
                "command_key_file": str(root / "in.key"),
                "amr_command_key_file": str(root / "amr.key"),
                "state_interval_s": 3600.0,
            }
        )
        extension = build(settings, self.bridge)
        assert extension is not None
        self.extension = extension
        self.bridge.extensions.add(extension)
        self.fleet_state: list[dict] = []
        extension.start(self.bridge_node, lambda topic, payload, retain: self.fleet_state.append(json.loads(payload)) if topic == STATE_TOPIC else None)
        self.amr = SimulatedAmrBridge()
        self.relayed: list[dict] = []
        self.addCleanup(self.close)

    def _on_stop(self, request, response):
        self.stop_calls += 1
        response.success = True
        response.message = "stopped by the test machine"
        return response

    def close(self) -> None:
        self.extension.stop()
        self.spinner.stop()
        self.machine_executor.shutdown()
        self.machine_thread.join(2.0)
        self.bridge_node.destroy_node()
        self.machine_node.destroy_node()

    def say(self, state: str) -> None:
        wanted = MachineState(state)
        self.state_pub.publish(String(data=state))
        self.assertTrue(wait_for(lambda: self.bridge._observed is wanted), f"the state {state} was not heard")

    def amr_says(self, machine: str, age_ms: int = 0) -> None:
        self.amr.machine = MachineState(machine)
        body = json.dumps({"observed_at": int(time.time() * 1000) - age_ms, "machine_state": machine}).encode()
        self.bridge.handle_message(f"{AMR}state", body, retained=True)

    def command(self, payload: bytes):
        out = self.bridge.handle_message(COMMAND, payload)
        answer = json.loads(out[0].payload)
        for forward in out[1:]:
            reply = self.amr.receive(forward)
            if reply is not None:
                for relayed in self.bridge.handle_message(f"{AMR}cmd/job/result", json.dumps(reply).encode()):
                    if relayed.topic == RESULT_TOPIC:
                        self.relayed.append(json.loads(relayed.payload))
        return answer, out[1:]

    def test_each_kind_of_vehicle_is_served_by_its_own_path_and_the_answer_of_the_amr_is_relayed(self) -> None:
        self.say("IDLE")
        self.amr_says("IDLE")
        answer, forwards = self.command(signed(job("a1")))
        self.assertTrue(answer["allowed"], answer)
        self.assertEqual((len(forwards), len(self.amr.accepted)), (1, 1))
        self.assertEqual(self.amr.accepted[0].parameters, {"goal_node": "N12"})
        self.assertEqual([item["allowed"] for item in self.relayed], [True])
        arm, none = self.command(signed(job("r1", vehicle="arm_1", station=None)))
        self.assertTrue(arm["allowed"], arm)
        self.assertEqual((arm["kind"], arm["mode"], none), ("ros2", "plan-only", []))

    def test_abort_for_everyone_calls_the_real_safe_stop_service_and_cancels_the_amr(self) -> None:
        self.say("RUNNING")
        self.amr_says("RUNNING")
        answer, forwards = self.command(signed(job("stop", JobPhase.ABORT, vehicle="*", station=None)))
        self.assertTrue(answer["allowed"], answer)
        self.assertEqual(self.stop_calls, 1)
        self.assertEqual(answer["vehicles"]["arm_1"]["allowed"], True)
        self.assertEqual([item.phase for item in self.amr.accepted], [JobPhase.ABORT])

    def test_the_fleet_state_shows_the_real_state_topic_and_the_amr_state(self) -> None:
        self.say("HOLDING")
        self.amr_says("RUNNING")
        self.assertTrue(self.extension.tick())
        self.assertEqual(self.fleet_state[-1]["vehicles"], {"amr_1": {"kind": "vda5050", "machine_state": "RUNNING"}, "arm_1": {"kind": "ros2", "machine_state": "HOLDING"}})

    def test_seeded_commands_with_injected_faults_reach_each_vehicle_only_when_every_condition_holds(self) -> None:
        rng = random.Random(SEED)
        expected_amr = expected_arm = 0
        for index in range(60):
            ros_state = rng.choice(["IDLE", "IDLE", "RUNNING", "FAULT"])
            amr_state = rng.choice(["IDLE", "IDLE", "RUNNING"])
            age = rng.choice([0, 0, 60_000])
            stopped = rng.random() < 0.2
            valid = rng.random() < 0.7
            target = rng.choice(["amr_1", "arm_1"])
            self.say(ros_state)
            self.amr_says(amr_state, age)
            self.guard.observe(STOPPED if stopped else READY)
            before_amr, before_arm = len(self.amr.accepted), 0
            answer, forwards = self.command(signed(job(f"z{index}", vehicle=target, station="dock_a" if target == "amr_1" else None), KEY if valid else b"w" * 40))
            if target == "amr_1":
                should = valid and amr_state == "IDLE" and age == 0 and not stopped
                self.assertEqual(len(forwards), 1 if should else 0, (index, valid, amr_state, age, stopped))
                self.assertEqual(len(self.amr.accepted) - before_amr, 1 if should else 0)
                expected_amr += 1 if should else 0
            else:
                should = valid and ros_state == "IDLE" and not stopped
                self.assertEqual(answer["allowed"], should, (index, valid, ros_state, stopped, answer))
                self.assertEqual((forwards, before_arm), ([], 0))
                expected_arm += 1 if should else 0
        self.assertEqual(len(self.amr.accepted), expected_amr)
        self.assertGreater(expected_amr, 2)
        self.assertGreater(expected_arm, 2)


if __name__ == "__main__":
    unittest.main()
