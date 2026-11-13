# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Motion extension against a real ROS 2 (container workflow, skipped where rclpy or the message packages are missing)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The motion extension drives real action clients (`nav2_msgs/NavigateToPose`, `moveit_msgs/MoveGroup`) against FAKE action servers of this test, in the ROS 2 container.

The servers are the test's: they accept, succeed, abort, reject, hang until cancelled or are absent, on command. Seeded random commands with injected faults (forged, expired, a machine that is not idle,
a global stop) must reach the server exactly when every condition holds. Evidence: a ROS 2 container with the nodes of this test, never Nav2, MoveIt or a robot.
"""

from __future__ import annotations

import json
import math
import os
import random
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    import rclpy
    from rclpy.action import ActionServer, CancelResponse, GoalResponse
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.executors import MultiThreadedExecutor
    from std_srvs.srv import Trigger
except ImportError:  # no ROS 2 on this host
    rclpy = None

try:
    from moveit_msgs.action import MoveGroup
    from nav2_msgs.action import NavigateToPose
except ImportError:  # the message packages are not installed
    MoveGroup = NavigateToPose = None

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict
from hydra_umc_sdk.global_stop import GlobalStopGuard, GlobalStopSettings
from hydra_umc_sdk.signed_commands import sign_command

from hydra_umc_bridge_ros2 import Ros2MqttBridge, spin_node_in_background
from hydra_umc_bridge_ros2.coordinator import Ros2Coordinator
from hydra_umc_bridge_ros2.ext_motion import MOVEIT_VERB, NAV2_VERB, build, parse
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX

NEEDS = unittest.skipIf(rclpy is None or NavigateToPose is None or MoveGroup is None, "rclpy, nav2_msgs or moveit_msgs is not installed")
KEY = b"0123456789abcdef0123456789abcdef-hydra-motion"
SEED = 20260
NAV_TOPIC = f"{TOPIC_PREFIX}{NAV2_VERB}"
MOVE_TOPIC = f"{TOPIC_PREFIX}{MOVEIT_VERB}"
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


def job(key: str, target: str | None = "dock_a", phase: JobPhase = JobPhase.PROCESS) -> BridgeJob:
    return BridgeJob(f"job-{key}", key, "orchestrator", phase, MachineState.IDLE, {"target": target} if target is not None else {})


def signed(topic: str, item: BridgeJob, key: bytes = KEY, **keywords) -> bytes:
    return json.dumps(sign_command(topic, job_to_dict(item), key, **keywords)).encode()


class _Robot:
    """The nodes that play Nav2, MoveIt and the safe stop service, on a multi-threaded executor of their own. `mode` says what the next goal does."""

    def __init__(self) -> None:
        self.node = rclpy.create_node("hy_motion_robot")
        self.mode = "succeed"
        self.moveit_code = 1
        self.nav_goals: list = []
        self.move_goals: list = []
        self.cancels = 0
        self.stop_calls = 0
        group = ReentrantCallbackGroup()
        self.servers: list = []
        self.safe_stop = self.node.create_service(Trigger, Ros2Coordinator.safe_stop_service, self._on_stop, callback_group=group)
        self.executor = MultiThreadedExecutor(num_threads=4)
        self.executor.add_node(self.node)
        self.group = group
        self.thread = threading.Thread(target=self.executor.spin, daemon=True)
        self.thread.start()

    def serve(self, nav: bool = True, move: bool = True) -> None:
        if nav:
            self.servers.append(ActionServer(self.node, NavigateToPose, "navigate_to_pose", self._execute_nav, goal_callback=self._on_goal, cancel_callback=self._on_cancel, callback_group=self.group))
        if move:
            self.servers.append(ActionServer(self.node, MoveGroup, "move_action", self._execute_move, goal_callback=self._on_goal, cancel_callback=self._on_cancel, callback_group=self.group))

    def _on_stop(self, request, response):
        self.stop_calls += 1
        response.success = True
        response.message = "stopped by the test robot"
        return response

    def _on_goal(self, goal_request):
        return GoalResponse.REJECT if self.mode == "reject" else GoalResponse.ACCEPT

    def _on_cancel(self, goal_handle):
        self.cancels += 1
        return CancelResponse.ACCEPT

    def _behave(self, goal_handle, result):
        if self.mode == "hang":
            end = time.monotonic() + 40
            while time.monotonic() < end:
                if goal_handle.is_cancel_requested:
                    goal_handle.canceled()
                    return result
                time.sleep(0.05)
            goal_handle.abort()
            return result
        time.sleep(0.2)
        if self.mode == "abort":
            goal_handle.abort()
        else:
            goal_handle.succeed()
        return result

    def _execute_nav(self, goal_handle):
        self.nav_goals.append(goal_handle.request)
        return self._behave(goal_handle, NavigateToPose.Result())

    def _execute_move(self, goal_handle):
        self.move_goals.append(goal_handle.request)
        result = MoveGroup.Result()
        result.error_code.val = self.moveit_code
        return self._behave(goal_handle, result)

    def close(self) -> None:
        for server in self.servers:
            server.destroy()
        self.executor.shutdown()
        self.thread.join(3.0)
        self.node.destroy_node()


class _Rig:
    def __init__(self, testcase: unittest.TestCase, robot: _Robot, **keywords) -> None:
        folder = tempfile.TemporaryDirectory()
        testcase.addCleanup(folder.cleanup)
        root = Path(folder.name)
        key_file = root / "command.key"
        key_file.write_bytes(KEY)
        os.chmod(key_file, 0o600)
        (root / "waypoints.json").write_text(json.dumps({"waypoints": {"dock_a": {"x": 1.5, "y": -2.0, "yaw": math.pi / 2}}}), encoding="utf-8")
        (root / "targets.json").write_text(json.dumps({"targets": {"park": {"group": "arm", "joints": {"joint1": 0.5, "joint2": -1.0}}}}), encoding="utf-8")
        self.robot = robot
        self.node = rclpy.create_node("hy_motion_bridge")
        self.guard = GlobalStopGuard(GlobalStopSettings(True))
        self.guard.observe(READY)
        self.cell = CellState.READY
        self.bridge = Ros2MqttBridge(self.node, lambda: self.cell, node_is_spinning=True, observed_max_age_seconds=None, global_stop=self.guard)
        self.spinner = spin_node_in_background(self.node)
        self.bridge.start_observing()
        self.bridge.observe("IDLE")
        values = dict(
            enabled=True,
            command_key_file=str(key_file),
            nav2_enabled=True,
            nav2_waypoints_file=str(root / "waypoints.json"),
            moveit_enabled=True,
            moveit_targets_file=str(root / "targets.json"),
            moveit_velocity_scale=0.2,
            goal_timeout_s=30.0,
        )
        values.update(keywords)
        self.published: list[tuple[str, dict]] = []
        self.lock = threading.Lock()
        extension = build(parse(values), self.bridge)
        assert extension is not None
        self.extension = extension
        self.bridge.extensions.add(extension)
        extension.start(self.node, self.record)
        testcase.addCleanup(self.close)

    def record(self, topic: str, payload: str, retain: bool) -> None:
        with self.lock:
            self.published.append((topic, json.loads(payload)))

    def statuses(self, topic: str) -> list[str]:
        with self.lock:
            return [body["goal_status"] for published_topic, body in self.published if published_topic == f"{topic}/result"]

    def send(self, topic: str, payload: bytes) -> dict:
        return json.loads(self.bridge.handle_message(topic, payload)[0].payload)

    def close(self) -> None:
        self.extension.stop()
        self.spinner.stop()
        self.node.destroy_node()


@NEEDS
class RealMotionTests(unittest.TestCase):
    def robot(self, **serve) -> _Robot:
        robot = _Robot()
        robot.serve(**serve)
        self.addCleanup(robot.close)
        return robot

    def test_a_named_waypoint_becomes_a_nav2_pose_and_the_goal_succeeds(self) -> None:
        robot = self.robot()
        rig = _Rig(self, robot)
        self.assertTrue(wait_for(lambda: rig.extension.gateway._routes[NAV2_VERB][0].ready()), "the fake Nav2 server was not found")
        answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, job("n1")))
        self.assertTrue(answer["allowed"], answer)
        self.assertTrue(wait_for(lambda: "succeeded" in rig.statuses(NAV_TOPIC)), rig.statuses(NAV_TOPIC))
        self.assertEqual(rig.statuses(NAV_TOPIC)[:2], ["accepted", "succeeded"])
        pose = robot.nav_goals[0].pose
        self.assertEqual(pose.header.frame_id, "map")
        self.assertAlmostEqual(pose.pose.position.x, 1.5)
        self.assertAlmostEqual(pose.pose.position.y, -2.0)
        self.assertAlmostEqual(pose.pose.orientation.z, math.sin(math.pi / 4))
        self.assertAlmostEqual(pose.pose.orientation.w, math.cos(math.pi / 4))
        self.assertFalse(rig.extension.gateway.busy)

    def test_a_named_target_becomes_a_scaled_moveit_goal_and_the_error_code_decides(self) -> None:
        robot = self.robot()
        rig = _Rig(self, robot)
        self.assertTrue(wait_for(lambda: rig.extension.gateway._routes[MOVEIT_VERB][0].ready()))
        self.assertTrue(rig.send(MOVE_TOPIC, signed(MOVE_TOPIC, job("m1", "park")))["allowed"])
        self.assertTrue(wait_for(lambda: "succeeded" in rig.statuses(MOVE_TOPIC)), rig.statuses(MOVE_TOPIC))
        request = robot.move_goals[0].request
        self.assertEqual((request.group_name, round(request.max_velocity_scaling_factor, 3)), ("arm", 0.2))
        joints = {c.joint_name: c.position for c in request.goal_constraints[0].joint_constraints}
        self.assertEqual(joints, {"joint1": 0.5, "joint2": -1.0})
        self.assertFalse(robot.move_goals[0].planning_options.plan_only)
        robot.moveit_code = -1
        self.assertTrue(rig.send(MOVE_TOPIC, signed(MOVE_TOPIC, job("m2", "park")))["allowed"])
        self.assertTrue(wait_for(lambda: rig.statuses(MOVE_TOPIC).count("failed") == 1), rig.statuses(MOVE_TOPIC))

    def test_a_rejected_goal_and_an_aborted_goal_are_reported_and_free_the_extension(self) -> None:
        robot = self.robot()
        rig = _Rig(self, robot)
        wait_for(lambda: rig.extension.gateway._routes[NAV2_VERB][0].ready())
        for index, (mode, expected) in enumerate((("reject", "rejected"), ("abort", "failed"))):
            robot.mode = mode
            self.assertTrue(rig.send(NAV_TOPIC, signed(NAV_TOPIC, job(f"f{index}")))["allowed"])
            self.assertTrue(wait_for(lambda wanted=expected: wanted in rig.statuses(NAV_TOPIC)), (mode, rig.statuses(NAV_TOPIC)))
            self.assertTrue(wait_for(lambda: not rig.extension.gateway.busy))

    def test_an_abort_cancels_the_real_goal_and_calls_the_real_safe_stop_service(self) -> None:
        robot = self.robot()
        rig = _Rig(self, robot)
        wait_for(lambda: rig.extension.gateway._routes[NAV2_VERB][0].ready())
        robot.mode = "hang"
        self.assertTrue(rig.send(NAV_TOPIC, signed(NAV_TOPIC, job("h1")))["allowed"])
        self.assertTrue(wait_for(lambda: "accepted" in rig.statuses(NAV_TOPIC)))
        second = rig.send(NAV_TOPIC, signed(NAV_TOPIC, job("h2")))
        self.assertFalse(second["allowed"])
        self.assertIn("already running", second["reason"])
        abort = rig.send(NAV_TOPIC, signed(NAV_TOPIC, job("h3", None, JobPhase.ABORT)))
        self.assertTrue(abort["allowed"], abort)
        self.assertTrue(abort["safe_stop"]["success"], abort)
        self.assertTrue(wait_for(lambda: "canceled" in rig.statuses(NAV_TOPIC)), rig.statuses(NAV_TOPIC))
        self.assertEqual((robot.cancels, robot.stop_calls, len(robot.nav_goals)), (1, 1, 1))
        self.assertTrue(wait_for(lambda: not rig.extension.gateway.busy))

    def test_a_goal_without_a_result_is_cancelled_by_the_timeout(self) -> None:
        robot = self.robot()
        rig = _Rig(self, robot, goal_timeout_s=1.0)
        wait_for(lambda: rig.extension.gateway._routes[NAV2_VERB][0].ready())
        robot.mode = "hang"
        self.assertTrue(rig.send(NAV_TOPIC, signed(NAV_TOPIC, job("t1")))["allowed"])
        self.assertTrue(wait_for(lambda: "timeout" in rig.statuses(NAV_TOPIC), 10.0), rig.statuses(NAV_TOPIC))
        self.assertTrue(wait_for(lambda: robot.cancels == 1))
        self.assertFalse(rig.extension.gateway.busy)

    def test_without_an_action_server_nothing_is_queued(self) -> None:
        robot = self.robot(nav=False, move=False)
        rig = _Rig(self, robot)
        answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, job("x1")))
        self.assertFalse(answer["allowed"])
        self.assertIn("not available", answer["reason"])
        self.assertEqual(robot.nav_goals, [])

    def test_seeded_commands_with_injected_faults_reach_the_server_only_when_every_condition_holds(self) -> None:
        robot = self.robot()
        rig = _Rig(self, robot)
        wait_for(lambda: rig.extension.gateway._routes[NAV2_VERB][0].ready())
        rng = random.Random(SEED)
        now = int(time.time() * 1000)
        expected = 0
        for index in range(40):
            valid_signature = rng.random() < 0.6
            machine = rng.choice(["IDLE", "IDLE", "RUNNING", "FAULT"])
            stopped = rng.random() < 0.2
            listed = rng.random() < 0.8
            expired = rng.random() < 0.15
            rig.bridge.observe(machine)
            rig.guard.observe(STOPPED if stopped else READY)
            keywords = {"now_ms": now - 120_000, "expires_in_ms": 1000} if expired else {}
            payload = signed(NAV_TOPIC, job(f"z{index}", "dock_a" if listed else "nowhere"), KEY if valid_signature else b"w" * 40, **keywords)
            should = valid_signature and machine == "IDLE" and not stopped and listed and not expired
            before = len(robot.nav_goals)
            rig.send(NAV_TOPIC, payload)
            if should:
                expected += 1
                self.assertTrue(wait_for(lambda seen=before: len(robot.nav_goals) == seen + 1), index)
                self.assertTrue(wait_for(lambda: not rig.extension.gateway.busy, 20.0), index)
            else:
                time.sleep(0.05)
                self.assertEqual(len(robot.nav_goals), before, (index, valid_signature, machine, stopped, listed, expired))
        self.assertEqual(len(robot.nav_goals), expected)
        self.assertGreater(expected, 3)


if __name__ == "__main__":
    unittest.main()
