# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Tests of the motion extension (Nav2 and MoveIt named goals, behind the gate and a signed confirmation)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Everything that stands between a message and a goal, without ROS 2: the switch, the global stop, the signature, the gate, the allowlist, one motion at a time, the timeout, the abort."""

import json
import math
import os
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict
from hydra_umc_sdk.global_stop import GlobalStopGuard, GlobalStopSettings
from hydra_umc_sdk.signed_commands import sign_command

from hydra_umc_bridge_ros2 import Ros2MqttBridge
from hydra_umc_bridge_ros2 import ext_motion
from hydra_umc_bridge_ros2.extensions import ExtensionConfigError, build_extensions, parse_sections
from hydra_umc_bridge_ros2.ext_motion import (
    MOVEIT_VERB,
    NAV2_VERB,
    JointTarget,
    MotionExtension,
    MotionSettings,
    MoveItAdapter,
    Nav2Adapter,
    Waypoint,
    build,
    load_targets,
    load_waypoints,
    parse,
)
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX
from hydra_umc_bridge_ros2.rclpy_transport import SafeStopResult

KEY = b"0123456789abcdef0123456789abcdef-hydra-motion"
STOPPED = json.dumps({"state": "SAFE_STOP"}).encode()
READY = json.dumps({"state": "READY"}).encode()
NAV_TOPIC = f"{TOPIC_PREFIX}{NAV2_VERB}"
MOVE_TOPIC = f"{TOPIC_PREFIX}{MOVEIT_VERB}"


class Files:
    """A folder with the key and the two allowlists."""

    def __init__(self, testcase: unittest.TestCase) -> None:
        self.folder = tempfile.TemporaryDirectory()
        testcase.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.key = str(root / "command.key")
        Path(self.key).write_bytes(KEY)
        os.chmod(self.key, 0o600)
        self.waypoints = str(root / "waypoints.json")
        Path(self.waypoints).write_text(json.dumps({"waypoints": {"dock_a": {"x": 1.5, "y": -2.0, "yaw": 1.0}, "home": {"x": 0, "y": 0, "yaw": 0}}}), encoding="utf-8")
        self.targets = str(root / "targets.json")
        Path(self.targets).write_text(json.dumps({"targets": {"park": {"group": "arm", "joints": {"joint1": 0.0, "joint2": -1.2}}}}), encoding="utf-8")

    def settings(self, **keywords) -> MotionSettings:
        values = dict(
            enabled=True,
            command_key_file=self.key,
            nav2_enabled=True,
            nav2_waypoints_file=self.waypoints,
            moveit_enabled=True,
            moveit_targets_file=self.targets,
            goal_timeout_s=5.0,
        )
        values.update(keywords)
        return parse(values)


class FakeSafeStop:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def call(self, node, name, *, timeout_sec=2.0):
        self.calls += 1
        return SafeStopResult(True, not self.fail, "stopped" if not self.fail else "no answer")


class FakeAdapter:
    def __init__(self) -> None:
        self.sent: list[object] = []
        self.callback = None
        self.is_ready = True
        self.cancels = 0
        self.closed = False
        self.raise_on_send = False
        self.immediate: tuple[str, str] | None = None

    def ready(self):
        return self.is_ready

    def send(self, spec, on_status):
        if self.raise_on_send:
            raise RuntimeError("client is down")
        self.sent.append(spec)
        self.callback = on_status
        if self.immediate is not None:
            on_status(*self.immediate)

    def cancel(self):
        self.cancels += 1
        return True

    def close(self):
        self.closed = True


class FakeTimer:
    created: list["FakeTimer"] = []

    def __init__(self, delay, function):
        self.delay, self.function, self.cancelled, self.daemon = delay, function, False, False
        FakeTimer.created.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


def make_job(phase=JobPhase.PROCESS, key="k-1", target="dock_a", **extra) -> BridgeJob:
    parameters = {"target": target} if target is not None else {}
    parameters.update(extra)
    return BridgeJob("job-" + key, key, "orchestrator", phase, MachineState.IDLE, parameters)


def signed(topic, job=None, key=KEY, **keywords) -> bytes:
    return json.dumps(sign_command(topic, job_to_dict(job or make_job()), key, **keywords)).encode()


class Rig:
    """A bridge that has observed an IDLE machine, the motion extension on top of it with fake adapters, and what it published."""

    def __init__(self, testcase: unittest.TestCase, *, global_stop=None, **keywords) -> None:
        self.files = Files(testcase)
        self.guard = global_stop or GlobalStopGuard(GlobalStopSettings(True))
        self.guard.observe(READY)
        self.safe_stop = FakeSafeStop()
        self.cell = CellState.READY
        self.bridge = Ros2MqttBridge(SimpleNamespace(), lambda: self.cell, global_stop=self.guard, safe_stop_client=self.safe_stop)  # type: ignore[arg-type]
        self.bridge.start_observing()
        self.bridge.observe("IDLE")
        self.nav, self.move = FakeAdapter(), FakeAdapter()
        self.published: list[tuple[str, dict]] = []
        FakeTimer.created.clear()
        settings = self.files.settings(**keywords)
        extension = build(settings, self.bridge)
        assert isinstance(extension, MotionExtension)
        extension._factories = {NAV2_VERB: lambda node: self.nav, MOVEIT_VERB: lambda node: self.move}
        self.extension = extension
        self.bridge.extensions.add(extension)
        extension.start(self.bridge.node, lambda topic, payload, retain: self.published.append((topic, json.loads(payload))))
        assert extension.gateway is not None
        extension.gateway._timer_factory = FakeTimer  # type: ignore[assignment]
        testcase.addCleanup(extension.stop)

    def send(self, topic, payload) -> dict:
        out = self.bridge.handle_message(topic, payload)
        return json.loads(out[0].payload)


class ParseTests(unittest.TestCase):
    def test_off_by_default_and_a_disabled_section_asks_for_nothing(self):
        self.assertFalse(parse(None).enabled)
        self.assertFalse(parse({"enabled": False, "nav2_enabled": True}).enabled)

    def test_an_enabled_section_needs_a_key_a_kind_and_its_list(self):
        files = Files(self)
        base = {"enabled": True, "command_key_file": files.key, "nav2_enabled": True, "nav2_waypoints_file": files.waypoints}
        parse(base)
        for change, word in (
            ({"command_key_file": ""}, "signed"),
            ({"nav2_enabled": False}, "neither"),
            ({"nav2_waypoints_file": ""}, "nav2_waypoints_file"),
            ({"nav2_waypoints_file": "/no/such/file.json"}, "cannot read"),
            ({"moveit_enabled": True}, "moveit_targets_file"),
            ({"goal_timeout_s": 0}, "goal_timeout_s"),
            ({"goal_timeout_s": float("inf")}, "goal_timeout_s"),
            ({"moveit_velocity_scale": 1.5}, "moveit_velocity_scale"),
            ({"moveit_acceleration_scale": 0}, "moveit_acceleration_scale"),
            ({"moveit_planning_time_s": 100}, "moveit_planning_time_s"),
            ({"nav2_action": "bad action"}, "nav2_action"),
            ({"moveit_action": ""}, "moveit_action"),
            ({"nav2_frame": "a b"}, "nav2_frame"),
            ({"command_max_expires_ms": 0}, "command_max_expires_ms"),
            ({"command_max_skew_ms": -1}, "command_max_skew_ms"),
        ):
            with self.subTest(change=change), self.assertRaises(ExtensionConfigError) as context:
                parse({**base, **change})
            self.assertIn(word, str(context.exception))

    def test_the_lists_are_checked_one_entry_at_a_time(self):
        root = Files(self)
        folder = Path(root.folder.name)
        bad_waypoints = (
            {"waypoints": {}},
            {"waypoints": {"a b": {"x": 0, "y": 0, "yaw": 0}}},
            {"waypoints": {"a": {"x": 0, "y": 0}}},
            {"waypoints": {"a": {"x": 0, "y": 0, "yaw": 0, "speed": 9}}},
            {"waypoints": {"a": {"x": "1", "y": 0, "yaw": 0}}},
            {"waypoints": {"a": {"x": True, "y": 0, "yaw": 0}}},
            {"waypoints": {"a": {"x": 5000, "y": 0, "yaw": 0}}},
            {"waypoints": {"a": {"x": 0, "y": 0, "yaw": 99}}},
            {"waypoints": {"a": {"x": float("nan"), "y": 0, "yaw": 0}}},
            {"waypoints": [1]},
            [],
        )
        for index, body in enumerate(bad_waypoints):
            path = folder / f"w{index}.json"
            path.write_text(json.dumps(body, allow_nan=True), encoding="utf-8")
            with self.subTest(body=body), self.assertRaises(ExtensionConfigError):
                load_waypoints(str(path))
        bad_targets = (
            {"targets": {"t": {"group": "arm"}}},
            {"targets": {"t": {"group": "", "joints": {"j": 0}}}},
            {"targets": {"t": {"group": "arm", "joints": {}}}},
            {"targets": {"t": {"group": "arm", "joints": {"j": 50}}}},
            {"targets": {"t": {"group": "arm", "joints": {"bad name": 0}}}},
            {"targets": {"t": {"group": "arm", "joints": {f"j{i}": 0 for i in range(17)}}}},
            {"targets": {"t": {"group": "arm", "joints": {"j": 0}, "extra": 1}}},
            {"targets": {f"t{i}": {"group": "g", "joints": {"j": 0}} for i in range(300)}},
        )
        for index, body in enumerate(bad_targets):
            path = folder / f"t{index}.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            with self.subTest(body=str(body)[:60]), self.assertRaises(ExtensionConfigError):
                load_targets(str(path))
        (folder / "broken.json").write_text("{", encoding="utf-8")
        with self.assertRaises(ExtensionConfigError):
            load_waypoints(str(folder / "broken.json"))

    def test_the_lists_are_read(self):
        files = Files(self)
        self.assertEqual(load_waypoints(files.waypoints)["dock_a"], Waypoint(1.5, -2.0, 1.0))
        self.assertEqual(load_targets(files.targets)["park"], JointTarget("arm", (("joint1", 0.0), ("joint2", -1.2))))

    def test_the_service_reads_the_section(self):
        files = Files(self)
        parsed = parse_sections({"motion": {"enabled": True, "command_key_file": files.key, "nav2_enabled": True, "nav2_waypoints_file": files.waypoints}})
        self.assertTrue(parsed["motion"].nav2_enabled)  # type: ignore[attr-defined]


class BuildTests(unittest.TestCase):
    def bridge(self, enabled: bool) -> Ros2MqttBridge:
        return Ros2MqttBridge(SimpleNamespace(), lambda: CellState.READY, global_stop=GlobalStopSettings(enabled))  # type: ignore[arg-type]

    def test_nothing_is_built_while_it_is_off(self):
        self.assertIsNone(build(MotionSettings(), self.bridge(False)))
        bridge = self.bridge(False)
        build_extensions(parse_sections({"motion": {}}), bridge)
        self.assertEqual(len(bridge.extensions), 0)

    def test_it_refuses_to_start_when_the_bridge_does_not_follow_the_global_stop(self):
        files = Files(self)
        with self.assertRaises(ExtensionConfigError) as context:
            build(files.settings(), self.bridge(False))
        self.assertIn("GLOBAL_STOP", str(context.exception))
        self.assertIsNotNone(build(files.settings(require_global_stop=False), self.bridge(False)))
        self.assertIsNotNone(build(files.settings(), self.bridge(True)))

    def test_it_refuses_to_start_without_a_usable_key(self):
        files = Files(self)
        for content in (b"short", b""):
            Path(files.key).write_bytes(content)
            with self.subTest(content=content), self.assertRaises(ExtensionConfigError) as context:
                build(files.settings(), self.bridge(True))
            self.assertIn("key", str(context.exception))
        with self.assertRaises(ExtensionConfigError):
            build(files.settings(command_key_file=files.key + ".missing"), self.bridge(True))
        self.assertNotIn(KEY.decode(), str(context.exception))

    def test_only_the_enabled_kinds_are_claimed_and_both_are_job_verbs(self):
        files = Files(self)
        bridge = self.bridge(True)
        only_nav = build(files.settings(moveit_enabled=False), bridge)
        assert only_nav is not None
        self.assertEqual((only_nav.claims(NAV_TOPIC), only_nav.claims(MOVE_TOPIC)), (True, False))
        self.assertEqual(only_nav.job_verbs, frozenset({NAV2_VERB}))
        both = build(files.settings(), bridge)
        assert both is not None
        self.assertEqual(both.job_verbs, frozenset({NAV2_VERB, MOVEIT_VERB}))
        self.assertEqual(both.moving_verbs, frozenset())


class GateTests(unittest.TestCase):
    def test_a_signed_job_for_a_listed_target_sends_one_goal(self):
        rig = Rig(self)
        answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC))
        self.assertTrue(answer["allowed"], answer)
        self.assertEqual((answer["mode"], answer["goal_status"], answer["target"]), ("send", "sent", "dock_a"))
        self.assertEqual(rig.nav.sent, [Waypoint(1.5, -2.0, 1.0)])
        self.assertEqual(rig.move.sent, [])
        self.assertEqual(FakeTimer.created[-1].delay, 5.0)

    def test_the_moveit_verb_sends_its_own_goal(self):
        rig = Rig(self)
        answer = rig.send(MOVE_TOPIC, signed(MOVE_TOPIC, make_job(target="park")))
        self.assertTrue(answer["allowed"], answer)
        self.assertEqual(rig.move.sent, [JointTarget("arm", (("joint1", 0.0), ("joint2", -1.2)))])
        self.assertEqual(rig.nav.sent, [])

    def test_an_unsigned_forged_altered_expired_or_future_command_sends_nothing(self):
        rig = Rig(self)
        now = int(time.time() * 1000)
        good = json.loads(signed(NAV_TOPIC))
        altered = dict(good, parameters={"target": "home"})
        cases = {
            "unsigned": json.dumps(job_to_dict(make_job(key="a"))).encode(),
            "wrong key": signed(NAV_TOPIC, make_job(key="b"), key=b"x" * 40),
            "another topic": signed(MOVE_TOPIC, make_job(key="c")),
            "altered": json.dumps(altered).encode(),
            "expired": signed(NAV_TOPIC, make_job(key="d"), now_ms=now - 60_000, expires_in_ms=1000),
            "future": signed(NAV_TOPIC, make_job(key="e"), now_ms=now + 600_000),
            "too long": signed(NAV_TOPIC, make_job(key="f"), expires_in_ms=3_600_000),
            "not json": b"{not json",
            "not an object": b"[1, 2]",
        }
        for name, payload in cases.items():
            with self.subTest(name):
                answer = rig.send(NAV_TOPIC, payload)
                self.assertFalse(answer["allowed"], answer)
        self.assertEqual(rig.nav.sent, [])

    def test_a_repeated_key_gets_the_stored_answer_and_no_second_goal(self):
        rig = Rig(self)
        payload = signed(NAV_TOPIC, make_job(key="same"))
        first = rig.send(NAV_TOPIC, payload)
        rig.nav.callback("succeeded", "done")
        second = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="same")))
        self.assertEqual(second, first)
        self.assertEqual(len(rig.nav.sent), 1)

    def test_the_machine_the_bridge_observed_decides_not_the_one_written_in_the_job(self):
        rig = Rig(self)
        rig.bridge.observe("RUNNING")
        answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="r")))
        self.assertFalse(answer["allowed"])
        self.assertIn("RUNNING", answer["reason"])
        rig.bridge.observe("FAULT")
        self.assertIn("FAULT", rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="f")))["reason"])
        rig.bridge.observe("IDLE")
        self.assertTrue(rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="ok")))["allowed"])
        self.assertEqual(len(rig.nav.sent), 1)

    def test_an_unobserved_machine_a_stale_one_and_a_cell_that_is_not_ready_send_nothing(self):
        rig = Rig(self)
        rig.bridge._observed = None
        self.assertFalse(rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="u")))["allowed"])
        rig.bridge.observe("IDLE")
        rig.cell = CellState.INHIBITED
        self.assertIn("INHIBITED", rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="c")))["reason"])
        rig.cell = CellState.READY
        rig.bridge._observed_max_age = 0.001
        rig.bridge._observed_at -= 10
        self.assertIn("OFFLINE", rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="s")))["reason"])
        self.assertEqual(rig.nav.sent, [])

    def test_a_target_that_is_not_in_the_list_is_refused_whatever_it_looks_like(self):
        rig = Rig(self)
        for index, target in enumerate(("elsewhere", "", "../../etc", "dock_a ", "DOCK_A", "x" * 500, None)):
            with self.subTest(target=target):
                job = make_job(key=f"t{index}", target=target)
                answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, job))
                self.assertFalse(answer["allowed"])
        raw = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="xy", x="9", y="9")))
        self.assertTrue(raw["allowed"])  # an extra parameter such as x or y is not read: only the name counts
        self.assertEqual(rig.nav.sent, [Waypoint(1.5, -2.0, 1.0)])

    def test_the_other_kinds_list_does_not_answer_for_this_one(self):
        rig = Rig(self)
        self.assertFalse(rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="p", target="park")))["allowed"])
        self.assertFalse(rig.send(MOVE_TOPIC, signed(MOVE_TOPIC, make_job(key="d", target="dock_a")))["allowed"])
        self.assertEqual((rig.nav.sent, rig.move.sent), ([], []))

    def test_one_motion_at_a_time_and_the_next_waits_for_the_end_of_the_first(self):
        rig = Rig(self)
        self.assertTrue(rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="one")))["allowed"])
        second = rig.send(MOVE_TOPIC, signed(MOVE_TOPIC, make_job(key="two", target="park")))
        self.assertFalse(second["allowed"])
        self.assertIn("already running", second["reason"])
        rig.nav.callback("succeeded", "done")
        self.assertTrue(rig.send(MOVE_TOPIC, signed(MOVE_TOPIC, make_job(key="three", target="park")))["allowed"])

    def test_an_action_server_that_is_not_up_and_a_client_that_raises_are_refusals(self):
        rig = Rig(self)
        rig.nav.is_ready = False
        self.assertIn("not available", rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="n")))["reason"])
        rig.nav.is_ready = True
        rig.nav.raise_on_send = True
        with self.assertLogs(ext_motion.__name__, "ERROR"):
            answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="r")))
        self.assertIn("RuntimeError", answer["reason"])
        rig.nav.raise_on_send = False
        self.assertTrue(rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="again")))["allowed"])  # the refusal left nothing running

    def test_a_status_that_arrives_while_the_goal_is_being_sent_does_not_leave_a_timer_or_a_busy_flag(self):
        rig = Rig(self)
        rig.nav.immediate = ("rejected", "no")
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="q")))
        assert rig.extension.gateway is not None
        self.assertFalse(rig.extension.gateway.busy)
        self.assertEqual(FakeTimer.created, [])


class GlobalStopTests(unittest.TestCase):
    def test_while_the_server_says_stop_no_goal_is_sent_and_ready_lets_it_go_on(self):
        guard = GlobalStopGuard(GlobalStopSettings(True))
        rig = Rig(self, global_stop=guard)
        guard.observe(STOPPED)
        answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="g1")))
        self.assertFalse(answer["allowed"])
        self.assertIn("global stop", answer["reason"])
        self.assertEqual(rig.nav.sent, [])
        guard.observe(b"{unreadable")
        self.assertIn("global stop", rig.send(MOVE_TOPIC, signed(MOVE_TOPIC, make_job(key="g2", target="park")))["reason"])
        guard.observe(READY)
        self.assertTrue(rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="g3")))["allowed"])

    def test_an_abort_goes_through_during_the_stop(self):
        guard = GlobalStopGuard(GlobalStopSettings(True))
        rig = Rig(self, global_stop=guard)
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="go")))
        guard.observe(STOPPED)
        answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(JobPhase.ABORT, key="stop", target=None)))
        self.assertTrue(answer["allowed"], answer)
        self.assertEqual(rig.nav.cancels, 1)


class AbortTests(unittest.TestCase):
    def test_an_abort_cancels_the_running_goal_and_calls_the_safe_stop(self):
        rig = Rig(self)
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="go")))
        answer = rig.send(MOVE_TOPIC, signed(MOVE_TOPIC, make_job(JobPhase.ABORT, key="stop", target=None)))
        self.assertTrue(answer["allowed"])
        self.assertTrue(answer["cancelled"])
        self.assertEqual(answer["running"], "job-go")
        self.assertEqual((rig.nav.cancels, rig.move.cancels, rig.safe_stop.calls), (1, 0, 1))
        self.assertTrue(answer["safe_stop"]["success"])
        rig.nav.callback("canceled", "the goal was cancelled")
        self.assertEqual(rig.published[-1][1]["goal_status"], "canceled")
        self.assertFalse(rig.extension.gateway.busy)  # type: ignore[union-attr]

    def test_an_abort_with_nothing_running_still_calls_the_safe_stop_and_says_so(self):
        rig = Rig(self)
        answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(JobPhase.ABORT, key="stop", target=None)))
        self.assertEqual((answer["cancelled"], answer["running"], rig.safe_stop.calls), (False, None, 1))

    def test_a_safe_stop_that_does_not_confirm_is_reported_and_the_switch_off_only_cancels(self):
        rig = Rig(self)
        rig.safe_stop.fail = True
        answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(JobPhase.ABORT, key="s1", target=None)))
        self.assertIn("did not confirm", answer["reason"])
        rig.bridge.abort_calls_safe_stop = False
        off = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(JobPhase.ABORT, key="s2", target=None)))
        self.assertFalse(off["safe_stop"]["called"])
        self.assertEqual(rig.safe_stop.calls, 1)

    def test_an_abort_with_no_safe_stop_hook_only_cancels(self):
        rig = Rig(self)
        assert rig.extension.gateway is not None
        rig.extension.gateway._safe_stop = None
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="go")))
        answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(JobPhase.ABORT, key="a", target=None)))
        self.assertEqual(answer["reason"], "abort accepted; the running goal was cancelled")
        none = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(JobPhase.ABORT, key="b", target=None)))
        self.assertEqual(none["reason"], "abort accepted; the running goal was cancelled")  # the cancel is asked for again: the goal has not ended yet

    def test_an_abort_whose_cancel_raises_still_asks_for_the_safe_stop(self):
        rig = Rig(self)
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="go")))

        def broken():
            raise RuntimeError("client gone")

        rig.nav.cancel = broken  # type: ignore[method-assign]
        with self.assertLogs(ext_motion.__name__, "ERROR"):
            answer = rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(JobPhase.ABORT, key="a", target=None)))
        self.assertEqual((answer["cancelled"], rig.safe_stop.calls), (False, 1))


class StatusTests(unittest.TestCase):
    def test_statuses_of_the_goal_are_published_and_the_last_one_frees_the_extension(self):
        rig = Rig(self)
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="go")))
        rig.nav.callback("accepted", "the action server accepted the goal")
        rig.nav.callback("succeeded", "the goal was reached")
        self.assertEqual([item[1]["goal_status"] for item in rig.published], ["accepted", "succeeded"])
        self.assertEqual(rig.published[-1][0], f"{NAV_TOPIC}/result")
        self.assertFalse(rig.extension.gateway.busy)  # type: ignore[union-attr]
        rig.nav.callback("failed", "late")
        self.assertEqual(len(rig.published), 2)  # a late answer of a goal that is over is ignored

    def test_every_end_frees_the_extension(self):
        for status in ("failed", "canceled", "rejected"):
            with self.subTest(status=status):
                rig = Rig(self)
                rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="go")))
                rig.nav.callback(status, "x")
                self.assertTrue(rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="next")))["allowed"])

    def test_a_goal_without_a_result_is_cancelled_by_the_timeout(self):
        rig = Rig(self)
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="go")))
        FakeTimer.created[-1].function()
        self.assertEqual(rig.nav.cancels, 1)
        self.assertEqual(rig.published[-1][1]["goal_status"], "timeout")
        self.assertFalse(rig.extension.gateway.busy)  # type: ignore[union-attr]
        rig.nav.callback("canceled", "late")
        self.assertEqual(len(rig.published), 1)

    def test_a_timeout_of_an_old_goal_does_not_touch_the_new_one(self):
        rig = Rig(self)
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="one")))
        old = FakeTimer.created[-1]
        rig.nav.callback("succeeded", "done")
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="two")))
        old.function()
        self.assertEqual(rig.nav.cancels, 0)
        self.assertTrue(rig.extension.gateway.busy)  # type: ignore[union-attr]

    def test_a_timeout_whose_cancel_raises_is_still_reported(self):
        rig = Rig(self)
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="go")))

        def broken():
            raise RuntimeError("client gone")

        rig.nav.cancel = broken  # type: ignore[method-assign]
        with self.assertLogs(ext_motion.__name__, "ERROR"):
            FakeTimer.created[-1].function()
        self.assertEqual(rig.published[-1][1]["goal_status"], "timeout")

    def test_a_publish_that_fails_does_not_break_the_adapter_callback(self):
        rig = Rig(self)
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="go")))
        rig.extension._publish = lambda *a: (_ for _ in ()).throw(OSError("link down"))
        with self.assertLogs(ext_motion.__name__, "ERROR"):
            rig.nav.callback("succeeded", "done")
        self.assertFalse(rig.extension.gateway.busy)  # type: ignore[union-attr]

    def test_the_real_timer_cancels_and_the_stop_cancels_a_running_goal_and_closes_the_clients(self):
        rig = Rig(self, goal_timeout_s=0.05)
        assert rig.extension.gateway is not None
        rig.extension.gateway._timer_factory = lambda delay, function: __import__("threading").Timer(delay, function)
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="go")))
        deadline = time.monotonic() + 3
        while rig.nav.cancels == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(rig.nav.cancels, 1)
        rig.send(NAV_TOPIC, signed(NAV_TOPIC, make_job(key="again")))
        rig.extension.stop()
        self.assertEqual(rig.nav.cancels, 2)
        self.assertTrue(rig.nav.closed and rig.move.closed)
        rig.extension.stop()

    def test_before_the_start_and_after_the_stop_a_command_gets_a_refusal(self):
        files = Files(self)
        bridge = Ros2MqttBridge(SimpleNamespace(), lambda: CellState.READY, global_stop=GlobalStopSettings(True))  # type: ignore[arg-type]
        extension = build(files.settings(), bridge)
        assert extension is not None
        out = extension.on_message(NAV_TOPIC, b"{}", False)
        self.assertIn("has not started", json.loads(out[0].payload)["reason"])
        self.assertEqual(extension.on_message(NAV_TOPIC, b"{}", True), [])
        extension.stop()


class FuzzTests(unittest.TestCase):
    def test_a_seeded_stream_of_commands_sends_a_goal_only_when_every_condition_holds(self):
        rng = random.Random(20260)
        guard = GlobalStopGuard(GlobalStopSettings(True))
        rig = Rig(self, global_stop=guard)
        expected = 0
        for index in range(400):
            signature_ok = rng.random() < 0.7
            machine = rng.choice(["IDLE", "IDLE", "RUNNING", "FAULT", "bogus"])
            stopped = rng.random() < 0.2
            cell = rng.choice([CellState.READY, CellState.READY, CellState.INHIBITED])
            listed = rng.random() < 0.7
            busy = rig.extension.gateway.busy  # type: ignore[union-attr]
            rig.bridge.observe(machine)
            rig.cell = cell
            guard.observe(STOPPED if stopped else READY)
            job = make_job(key=f"k{index}", target="dock_a" if listed else "nowhere")
            payload = signed(NAV_TOPIC, job, key=KEY if signature_ok else b"y" * 40)
            before = len(rig.nav.sent)
            rig.send(NAV_TOPIC, payload)
            should = signature_ok and machine == "IDLE" and not stopped and cell is CellState.READY and listed and not busy
            self.assertEqual(len(rig.nav.sent) - before, 1 if should else 0, (index, signature_ok, machine, stopped, cell, listed, busy))
            expected += 1 if should else 0
            if rng.random() < 0.6 and rig.extension.gateway.busy:  # type: ignore[union-attr]
                rig.nav.callback(rng.choice(["succeeded", "failed", "canceled", "rejected"]), "x")
        self.assertEqual(len(rig.nav.sent), expected)
        self.assertGreater(expected, 5)


class FakeFuture:
    def __init__(self) -> None:
        self.callbacks: list = []
        self.value: object = None
        self.error: Exception | None = None

    def add_done_callback(self, callback):
        self.callbacks.append(callback)

    def resolve(self, value=None, error=None):
        self.value, self.error = value, error
        for callback in self.callbacks:
            callback(self)

    def result(self):
        if self.error is not None:
            raise self.error
        return self.value


class FakeHandle:
    def __init__(self, accepted=True) -> None:
        self.accepted = accepted
        self.result_future = FakeFuture()
        self.cancels = 0

    def get_result_async(self):
        return self.result_future

    def cancel_goal_async(self):
        self.cancels += 1


class FakeActionClient:
    created: list["FakeActionClient"] = []

    def __init__(self, node, action_type, name) -> None:
        self.node, self.action_type, self.name = node, action_type, name
        self.up = True
        self.goals: list[object] = []
        self.futures: list[FakeFuture] = []
        self.destroyed = False
        FakeActionClient.created.append(self)

    def server_is_ready(self):
        return self.up

    def send_goal_async(self, goal):
        self.goals.append(goal)
        future = FakeFuture()
        self.futures.append(future)
        return future

    def destroy(self):
        self.destroyed = True


def ns(**fields):
    return SimpleNamespace(**fields)


class FakeGoalStatus:
    STATUS_SUCCEEDED = 4
    STATUS_CANCELED = 5
    STATUS_ABORTED = 6


class FakeNavigate:
    class Goal:
        def __init__(self) -> None:
            self.pose = ns(header=ns(frame_id="", stamp=None), pose=ns(position=ns(x=0.0, y=0.0), orientation=ns(z=0.0, w=1.0)))


class FakeMoveGroup:
    class Goal:
        def __init__(self) -> None:
            self.request = ns(goal_constraints=[], group_name="")
            self.planning_options = ns(plan_only=True, planning_scene_diff=ns(is_diff=False, robot_state=ns(is_diff=False)))


class FakeNode:
    def get_clock(self):
        return ns(now=lambda: ns(to_msg=lambda: "STAMP"))


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeActionClient.created.clear()
        self.statuses: list[tuple[str, str]] = []

    def on_status(self, status, detail):
        self.statuses.append((status, detail))

    def nav(self) -> tuple[Nav2Adapter, FakeActionClient]:
        adapter = Nav2Adapter(FakeNode(), "navigate_to_pose", "map", action_client=FakeActionClient, messages=(FakeNavigate, FakeGoalStatus))
        return adapter, FakeActionClient.created[-1]

    def move(self, **keywords) -> tuple[MoveItAdapter, FakeActionClient]:
        settings = MotionSettings(**keywords)
        messages = (FakeMoveGroup, lambda: ns(joint_constraints=[]), lambda: ns(), FakeGoalStatus)
        adapter = MoveItAdapter(FakeNode(), "move_action", settings, action_client=FakeActionClient, messages=messages)
        return adapter, FakeActionClient.created[-1]

    def test_the_nav2_goal_is_a_pose_in_the_frame_of_the_settings_with_the_yaw_as_a_quaternion(self):
        adapter, client = self.nav()
        self.assertEqual(client.name, "navigate_to_pose")
        adapter.send(Waypoint(1.5, -2.0, math.pi / 2), self.on_status)
        goal = client.goals[0]
        self.assertEqual((goal.pose.header.frame_id, goal.pose.header.stamp), ("map", "STAMP"))  # type: ignore[attr-defined]
        self.assertEqual((goal.pose.pose.position.x, goal.pose.pose.position.y), (1.5, -2.0))  # type: ignore[attr-defined]
        self.assertAlmostEqual(goal.pose.pose.orientation.z, math.sin(math.pi / 4))  # type: ignore[attr-defined]
        self.assertAlmostEqual(goal.pose.pose.orientation.w, math.cos(math.pi / 4))  # type: ignore[attr-defined]

    def test_accept_then_succeed(self):
        adapter, client = self.nav()
        adapter.send(Waypoint(0, 0, 0), self.on_status)
        handle = FakeHandle()
        client.futures[0].resolve(handle)
        handle.result_future.resolve(ns(status=4))
        self.assertEqual([item[0] for item in self.statuses], ["accepted", "succeeded"])

    def test_a_rejected_goal_an_aborted_one_a_cancelled_one_and_errors_are_reported(self):
        for outcome, expected in ((ns(status=6), "failed"), (ns(status=5), "canceled")):
            with self.subTest(expected=expected):
                self.statuses.clear()
                adapter, client = self.nav()
                adapter.send(Waypoint(0, 0, 0), self.on_status)
                handle = FakeHandle()
                client.futures[-1].resolve(handle)
                handle.result_future.resolve(outcome)
                self.assertEqual(self.statuses[-1][0], expected)
        self.statuses.clear()
        adapter, client = self.nav()
        adapter.send(Waypoint(0, 0, 0), self.on_status)
        client.futures[-1].resolve(FakeHandle(accepted=False))
        self.assertEqual(self.statuses[0][0], "rejected")
        self.statuses.clear()
        adapter.send(Waypoint(0, 0, 0), self.on_status)
        client.futures[-1].resolve(error=RuntimeError("context down"))
        self.assertEqual(self.statuses[0][0], "failed")
        self.statuses.clear()
        adapter.send(Waypoint(0, 0, 0), self.on_status)
        handle = FakeHandle()
        client.futures[-1].resolve(handle)
        handle.result_future.resolve(error=RuntimeError("lost"))
        self.assertEqual([item[0] for item in self.statuses], ["accepted", "failed"])
        self.statuses.clear()
        adapter.send(Waypoint(0, 0, 0), self.on_status)
        handle = FakeHandle()
        client.futures[-1].resolve(handle)
        handle.result_future.resolve(object())  # a result that cannot be read
        self.assertEqual(self.statuses[-1][0], "failed")

    def test_cancel_reaches_the_handle_and_one_asked_before_the_answer_is_sent_when_it_comes(self):
        adapter, client = self.nav()
        adapter.send(Waypoint(0, 0, 0), self.on_status)
        self.assertTrue(adapter.cancel())  # no answer yet
        handle = FakeHandle()
        client.futures[0].resolve(handle)
        self.assertEqual(handle.cancels, 1)
        adapter.send(Waypoint(0, 0, 0), self.on_status)  # a new goal forgets the old request
        late = FakeHandle()
        client.futures[1].resolve(late)
        self.assertEqual(late.cancels, 0)
        self.assertTrue(adapter.cancel())
        self.assertEqual(late.cancels, 1)

    def test_ready_follows_the_action_server_and_close_destroys_the_client(self):
        adapter, client = self.nav()
        self.assertTrue(adapter.ready())
        client.up = False
        self.assertFalse(adapter.ready())
        adapter.close()
        self.assertTrue(client.destroyed)
        client.destroy = lambda: (_ for _ in ()).throw(RuntimeError("gone"))  # type: ignore[method-assign]
        adapter.close()

    def test_the_moveit_goal_has_one_tight_constraint_per_joint_and_scaled_speed(self):
        adapter, client = self.move(moveit_velocity_scale=0.2, moveit_acceleration_scale=0.3, moveit_planning_time_s=7.0)
        adapter.send(JointTarget("arm", (("joint1", 0.5), ("joint2", -1.0))), self.on_status)
        goal = client.goals[0]
        request = goal.request  # type: ignore[attr-defined]
        self.assertEqual((request.group_name, request.max_velocity_scaling_factor, request.max_acceleration_scaling_factor, request.allowed_planning_time), ("arm", 0.2, 0.3, 7.0))
        constraints = request.goal_constraints[0].joint_constraints
        self.assertEqual([(c.joint_name, c.position, c.tolerance_above, c.tolerance_below, c.weight) for c in constraints], [("joint1", 0.5, 0.001, 0.001, 1.0), ("joint2", -1.0, 0.001, 0.001, 1.0)])
        self.assertFalse(goal.planning_options.plan_only)  # type: ignore[attr-defined]

    def test_the_moveit_result_needs_the_error_code_success_too(self):
        for code, expected in ((1, "succeeded"), (-1, "failed"), (99999, "failed")):
            with self.subTest(code=code):
                self.statuses.clear()
                adapter, client = self.move()
                adapter.send(JointTarget("arm", (("joint1", 0.0),)), self.on_status)
                handle = FakeHandle()
                client.futures[-1].resolve(handle)
                handle.result_future.resolve(ns(status=4, result=ns(error_code=ns(val=code))))
                self.assertEqual(self.statuses[-1][0], expected)
        self.statuses.clear()
        adapter, client = self.move()
        adapter.send(JointTarget("arm", (("joint1", 0.0),)), self.on_status)
        handle = FakeHandle()
        client.futures[-1].resolve(handle)
        handle.result_future.resolve(ns(status=5, result=ns(error_code=ns(val=1))))
        self.assertEqual(self.statuses[-1][0], "canceled")

    def test_without_the_message_packages_the_adapters_say_why(self):
        import sys
        from unittest import mock

        with mock.patch.dict(sys.modules, {"nav2_msgs": None, "nav2_msgs.action": None, "moveit_msgs": None, "moveit_msgs.action": None, "moveit_msgs.msg": None, "rclpy": None, "rclpy.action": None}):
            with self.assertRaises(RuntimeError) as nav:
                Nav2Adapter(FakeNode(), "navigate_to_pose", "map")
            with self.assertRaises(RuntimeError) as move:
                MoveItAdapter(FakeNode(), "move_action", MotionSettings())
            with self.assertRaises(RuntimeError) as client:
                ext_motion._action_client_class()
        self.assertIn("nav2_msgs", str(nav.exception))
        self.assertIn("moveit_msgs", str(move.exception))
        self.assertIn("rclpy", str(client.exception))

    def test_the_base_class_leaves_goal_and_outcome_to_the_subclasses(self):
        base = ext_motion._ActionAdapter(FakeNode(), FakeActionClient(None, None, "x"))
        with self.assertRaises(NotImplementedError):
            base._build_goal(None)
        with self.assertRaises(NotImplementedError):
            base._outcome(None)

    def test_the_default_factories_build_the_two_adapters_from_the_settings(self):
        files = Files(self)
        bridge = Ros2MqttBridge(SimpleNamespace(), lambda: CellState.READY, global_stop=GlobalStopSettings(True))  # type: ignore[arg-type]
        extension = build(files.settings(nav2_action="nav/go", moveit_action="mv"), bridge)
        assert extension is not None
        from unittest import mock

        with mock.patch.object(Nav2Adapter, "_import_messages", return_value=(FakeNavigate, FakeGoalStatus)), mock.patch.object(
            MoveItAdapter, "_import_messages", return_value=(FakeMoveGroup, lambda: ns(joint_constraints=[]), lambda: ns(), FakeGoalStatus)
        ), mock.patch.object(ext_motion, "_action_client_class", return_value=FakeActionClient):
            extension.start(FakeNode(), lambda *a: None)
        self.assertEqual([client.name for client in FakeActionClient.created], ["nav/go", "mv"])
        extension.stop()
        self.assertTrue(all(client.destroyed for client in FakeActionClient.created))


if __name__ == "__main__":
    unittest.main()
