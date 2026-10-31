# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Global stop tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The bridge and the announcement of the global stop: off by default, SAFE_STOP refuses what moves, READY resumes, an unreadable state fails closed."""

import json
import os
import unittest
from unittest import mock

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict

from hydra_umc_bridge_ros2 import Ros2MqttBridge
from hydra_umc_bridge_ros2.global_stop import DEFAULT_TOPIC, GlobalStopGuard, GlobalStopSettings, settings_from_environment
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX, MessageDispatcher

SAFE_STOP = json.dumps({"schema_version": "1.0", "state": "SAFE_STOP", "source": "hydra-umc-server"}).encode()
READY = json.dumps({"schema_version": "1.0", "state": "READY", "source": "hydra-umc-server"}).encode()
ENVIRONMENT = "HYDRA_UMC_ROS2_GLOBAL_STOP"


class FakeNode:
    pass


def make_bridge(settings):
    return Ros2MqttBridge(FakeNode(), lambda: CellState.READY, global_stop=settings)  # type: ignore[arg-type]


def job(phase: JobPhase, number: int = 1) -> bytes:
    return json.dumps(job_to_dict(BridgeJob(f"j-{number}", f"key-{phase.value}-{number}", "orchestrator", phase, MachineState.IDLE, {}))).encode()


def answer(bridge, verb: str, payload: bytes = b"") -> dict:
    out = bridge.handle_message(f"{TOPIC_PREFIX}cmd/{verb}", payload)
    results = [json.loads(p.payload) for p in out if p.topic.endswith(f"cmd/{verb}/result")]
    return results[0] if results else {}


def stopped_by_the_guard(body: dict) -> bool:
    return "global stop" in str(body.get("reason", ""))


class SettingsTests(unittest.TestCase):
    def test_the_default_is_off_and_the_topic_is_the_servers(self) -> None:
        settings = GlobalStopSettings()
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.strict)
        self.assertEqual(settings.topic, DEFAULT_TOPIC)
        self.assertEqual(DEFAULT_TOPIC, "hydra/swarm/safety/estop")

    def test_a_topic_with_wildcards_is_refused(self) -> None:
        for bad in ("", "hydra/#", "hydra/+/estop", "/hydra", "hydra/"):
            with self.subTest(topic=bad), self.assertRaises(ValueError):
                GlobalStopSettings(True, bad)

    def test_the_environment_switches_it_on(self) -> None:
        wanted = {ENVIRONMENT: "1", ENVIRONMENT + "_TOPIC": "plant/safety/estop", ENVIRONMENT + "_STRICT": "true"}
        with mock.patch.dict(os.environ, wanted):
            settings = settings_from_environment("ROS2")
        self.assertEqual((settings.enabled, settings.topic, settings.strict), (True, "plant/safety/estop", True))
        with mock.patch.dict(os.environ):
            os.environ.pop(ENVIRONMENT, None)
            self.assertFalse(settings_from_environment("ROS2").enabled)


class GuardTests(unittest.TestCase):
    def guard(self, **kw) -> GlobalStopGuard:
        return GlobalStopGuard(GlobalStopSettings(True, DEFAULT_TOPIC, **kw))

    def test_stop_then_ready(self) -> None:
        guard = self.guard()
        self.assertFalse(guard.blocks())
        guard.observe(SAFE_STOP)
        self.assertTrue(guard.blocks())
        guard.observe(READY)
        self.assertFalse(guard.blocks())

    def test_anything_unclear_is_a_stop_while_enabled(self) -> None:
        for payload in (b"", b"not json", b"[]", b'{"state": "INHIBITED"}', b'{"state": "FAULT"}', b'{"state": "ready"}', b'{"x": 1}'):
            with self.subTest(payload=payload):
                guard = self.guard()
                guard.observe(READY)
                guard.observe(payload)
                self.assertTrue(guard.blocks())

    def test_strict_also_refuses_before_the_first_message(self) -> None:
        self.assertFalse(self.guard().blocks())
        strict = self.guard(strict=True)
        self.assertTrue(strict.blocks())
        strict.observe(READY)
        self.assertFalse(strict.blocks())

    def test_a_switched_off_guard_never_blocks(self) -> None:
        guard = GlobalStopGuard()
        guard.observe(SAFE_STOP)
        self.assertFalse(guard.blocks())
        self.assertIsNone(guard.refusal("cmd/job", job(JobPhase.PREPARE), moving=frozenset({"cmd/x"})))

    def test_an_abort_is_let_through_and_a_malformed_job_is_not(self) -> None:
        guard = self.guard()
        guard.observe(SAFE_STOP)
        self.assertFalse(guard.blocks_job(json.dumps({"phase": "ABORT"}).encode()))
        self.assertTrue(guard.blocks_job(json.dumps({"phase": "LOAD"}).encode()))
        self.assertTrue(guard.blocks_job(b"garbage"))

    def test_withdrawing_a_line_is_allowed_asserting_is_not(self) -> None:
        guard = self.guard()
        guard.observe(SAFE_STOP)
        self.assertFalse(guard.blocks_assertion(b'{"ready": false}', "ready"))
        self.assertTrue(guard.blocks_assertion(b'{"ready": true}', "ready"))
        self.assertTrue(guard.blocks_assertion(b"", "ready"))


class BridgeTests(unittest.TestCase):
    def test_off_by_default_nothing_changes(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop(ENVIRONMENT, None)
            bridge = make_bridge(None)
        self.assertFalse(bridge.global_stop.enabled)
        bridge.global_stop.observe(SAFE_STOP)  # even a stop that was heard is ignored while the switch is off
        self.assertFalse(stopped_by_the_guard(answer(bridge, "job", job(JobPhase.LOAD))))

    def test_a_job_is_refused_during_a_stop_and_answered_after_ready(self) -> None:
        bridge = make_bridge(GlobalStopSettings(True))
        bridge.global_stop.observe(SAFE_STOP)
        refused = answer(bridge, "job", job(JobPhase.LOAD))
        self.assertFalse(refused["allowed"])
        self.assertTrue(stopped_by_the_guard(refused), refused)
        bridge.global_stop.observe(READY)
        self.assertFalse(stopped_by_the_guard(answer(bridge, "job", job(JobPhase.LOAD, 2))))

    def test_an_abort_job_is_never_stopped_by_the_guard(self) -> None:
        bridge = make_bridge(GlobalStopSettings(True))
        bridge.global_stop.observe(SAFE_STOP)
        self.assertFalse(stopped_by_the_guard(answer(bridge, "job", job(JobPhase.ABORT))))

    def test_a_malformed_job_is_refused_during_a_stop(self) -> None:
        bridge = make_bridge(GlobalStopSettings(True))
        bridge.global_stop.observe(SAFE_STOP)
        self.assertTrue(stopped_by_the_guard(answer(bridge, "job", b"{not json")))

    def test_an_unreadable_announcement_fails_closed(self) -> None:
        bridge = make_bridge(GlobalStopSettings(True))
        bridge.global_stop.observe(READY)
        bridge.global_stop.observe(b"\xff\xfe")
        self.assertTrue(stopped_by_the_guard(answer(bridge, "job", job(JobPhase.LOAD))))

    def test_the_strict_switch_refuses_before_the_first_message(self) -> None:
        bridge = make_bridge(GlobalStopSettings(True, DEFAULT_TOPIC, True))
        self.assertTrue(stopped_by_the_guard(answer(bridge, "job", job(JobPhase.LOAD))))
        bridge.global_stop.observe(READY)
        self.assertFalse(stopped_by_the_guard(answer(bridge, "job", job(JobPhase.LOAD, 2))))

    def test_a_retained_replay_of_a_command_is_still_ignored(self) -> None:
        bridge = make_bridge(GlobalStopSettings(True))
        bridge.global_stop.observe(SAFE_STOP)
        self.assertEqual(bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", job(JobPhase.LOAD), retained=True), [])

    def test_the_safe_stop_is_never_refused_by_the_guard(self) -> None:
        bridge = make_bridge(GlobalStopSettings(True))
        bridge.global_stop.observe(SAFE_STOP)
        self.assertFalse(stopped_by_the_guard(answer(bridge, "safe_stop")))


class FakeMessage:
    def __init__(self, topic: str, payload: bytes, retain: bool) -> None:
        self.topic, self.payload, self.retain = topic, payload, retain


class DispatcherTests(unittest.TestCase):
    def test_the_retained_announcement_is_the_state_not_a_replay(self) -> None:
        bridge = make_bridge(GlobalStopSettings(True))
        dispatcher = MessageDispatcher(bridge)
        self.addCleanup(dispatcher.close)
        dispatcher.on_message(None, None, FakeMessage(DEFAULT_TOPIC, SAFE_STOP, True))
        self.assertTrue(bridge.global_stop.blocks())
        dispatcher.on_message(None, None, FakeMessage(DEFAULT_TOPIC, READY, True))
        self.assertFalse(bridge.global_stop.blocks())

    def test_a_switched_off_bridge_does_not_listen_to_the_topic(self) -> None:
        bridge = make_bridge(GlobalStopSettings(False))
        dispatcher = MessageDispatcher(bridge)
        self.addCleanup(dispatcher.close)
        dispatcher.on_message(None, None, FakeMessage(DEFAULT_TOPIC, SAFE_STOP, True))
        self.assertEqual(bridge.global_stop.state, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
