# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Interface plan validation, stale state, safe-stop routing and payload edge cases
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
import json
import unittest

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict

from hydra_umc_bridge_ros2 import Ros2Coordinator, Ros2MqttBridge
from hydra_umc_bridge_ros2.coordinator import Ros2InterfacePlan
from hydra_umc_bridge_ros2.mqtt_transport import MAX_PAYLOAD_BYTES, STOP_VERBS, TOPIC_PREFIX, state_payload


class Node:
    pass


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def job(phase=JobPhase.LOAD, claimed=MachineState.IDLE, key="k1"):
    return BridgeJob("job-1", key, "orchestrator", phase, claimed, {})


def bridge(cell=CellState.READY, observed="IDLE", **kwargs):
    # a bridge that has heard the state topic: without a reading of the machine every productive job is refused
    b = Ros2MqttBridge(Node(), lambda: cell, **kwargs)  # type: ignore[arg-type]
    if observed is not None:
        b.start_observing()
        b.observe(observed)
    return b


def answer(publishes):
    return json.loads(publishes[0].payload)


class InterfacePlanTests(unittest.TestCase):
    def plan(self):
        return Ros2Coordinator().interface_plan().to_dict()

    def test_the_plan_round_trips(self):
        plan = Ros2InterfacePlan.from_dict(self.plan())
        self.assertEqual(plan.to_dict(), self.plan())

    def test_a_changed_or_missing_field_is_refused(self):
        base = self.plan()
        missing = dict(base)
        del missing["job_action"]
        extra = dict(base, rogue="x")
        wrong_schema = dict(base, schema_version="1.0")
        live = dict(base, mode="live")
        outside = dict(base, safe_stop_service="/other/request_safe_stop")
        same = dict(base, inspect_service=base["state_topic"])
        durability = dict(base, state_topic_durability="best_effort")
        empty = dict(base, state_topic="")
        number = dict(base, state_topic=5)
        for name, payload in (("missing", missing), ("extra", extra), ("schema", wrong_schema), ("mode", live), ("namespace", outside), ("distinct", same), ("durability", durability), ("empty", empty), ("number", number)):
            with self.assertRaises(ValueError, msg=name):
                Ros2InterfacePlan.from_dict(payload)


class DispatchTests(unittest.TestCase):
    def test_the_abort_is_accepted_in_every_cell_state_and_goes_to_the_safe_stop_service(self):
        for cell in CellState:
            dispatch = Ros2Coordinator().dispatch(job(JobPhase.ABORT, MachineState.FAULT), cell, MachineState.IDLE)
            self.assertTrue(dispatch.accepted, cell)
            self.assertEqual(dispatch.interface, Ros2Coordinator.safe_stop_service)
            self.assertEqual(dispatch.mode, "plan-only")

    def test_productive_phases_need_a_ready_cell(self):
        for phase in (JobPhase.PREPARE, JobPhase.LOAD, JobPhase.PROCESS, JobPhase.UNLOAD, JobPhase.COMPLETE):
            self.assertFalse(Ros2Coordinator().dispatch(job(phase), CellState.FAULT, MachineState.IDLE).accepted, phase)
            self.assertEqual(Ros2Coordinator().dispatch(job(phase), CellState.READY, MachineState.IDLE).interface, Ros2Coordinator.job_action)


class ObservedStateTests(unittest.TestCase):
    def test_a_state_that_was_never_heard_is_not_trusted_once_observation_starts(self):
        b = bridge()
        b.start_observing()
        self.assertFalse(answer(b.handle_message(f"{TOPIC_PREFIX}cmd/job", json.dumps(job_to_dict(job())).encode()))["allowed"])

    def test_a_name_that_is_not_a_machine_state_reads_as_offline(self):
        b = bridge()
        b.observe("exploding")
        self.assertFalse(answer(b.handle_message(f"{TOPIC_PREFIX}cmd/job", json.dumps(job_to_dict(job())).encode()))["allowed"])

    def test_the_state_name_is_read_case_and_space_insensitively(self):
        b = bridge()
        b.observe("  idle ")
        self.assertTrue(answer(b.handle_message(f"{TOPIC_PREFIX}cmd/job", json.dumps(job_to_dict(job())).encode()))["allowed"])

    def test_a_state_older_than_the_limit_reads_as_offline_and_a_fresh_one_does_not(self):
        clock = Clock()
        b = bridge(clock=clock, observed_max_age_seconds=5.0)
        b.observe("IDLE")
        payload = json.dumps(job_to_dict(job())).encode()
        clock.now += 5.0
        self.assertTrue(answer(b.handle_message(f"{TOPIC_PREFIX}cmd/job", payload))["allowed"])
        clock.now += 0.1
        self.assertFalse(answer(b.handle_message(f"{TOPIC_PREFIX}cmd/job", payload.replace(b"job-1", b"job-2")))["allowed"])

    def test_the_republished_state_names_the_normalized_state_or_null(self):
        known = json.loads(state_payload("running", 5))
        self.assertEqual((known["state"], known["machine_state"], known["observed_at"]), ("running", MachineState.RUNNING.value, 5))
        self.assertIsNone(json.loads(state_payload("???", 5))["machine_state"])


class RoutingTests(unittest.TestCase):
    def test_only_the_two_known_verbs_are_answered(self):
        b = bridge()
        for topic in (f"{TOPIC_PREFIX}cmd/other", f"{TOPIC_PREFIX}state", "other/cmd/job", "cmd/job", ""):
            self.assertEqual(b.handle_message(topic, b"{}"), [], topic)

    def test_a_retained_job_or_safe_stop_is_never_acted_on(self):
        b = bridge()
        self.assertEqual(b.handle_message(f"{TOPIC_PREFIX}cmd/job", b"{}", retained=True), [])
        self.assertEqual(b.handle_message(f"{TOPIC_PREFIX}cmd/safe_stop", b"", retained=True), [])

    def test_safe_stop_is_the_only_verb_with_its_own_lane(self):
        self.assertEqual(STOP_VERBS, frozenset({"cmd/safe_stop"}))

    def test_an_oversized_safe_stop_is_refused_on_its_own_result_topic(self):
        publishes = bridge().handle_message(f"{TOPIC_PREFIX}cmd/safe_stop", b"x" * (MAX_PAYLOAD_BYTES + 1))
        self.assertEqual(publishes[0].topic, f"{TOPIC_PREFIX}cmd/safe_stop/result")

    def test_json_that_is_not_a_job_is_always_a_refusal(self):
        for payload in (b"null", b"[]", b"7", b'"x"', b"", b"{", bytes([255, 254]), b'{"phase": "load"}'):
            publishes = bridge().handle_message(f"{TOPIC_PREFIX}cmd/job", payload)
            self.assertEqual(len(publishes), 1, payload)
            self.assertFalse(answer(publishes)["allowed"], payload)

    def test_a_repeated_key_gets_the_first_answer_and_a_new_key_is_judged_again(self):
        from hydra_umc_sdk.job_intake import IdempotencyStore

        b = bridge(CellState.READY, idempotency=IdempotencyStore())
        payload = json.dumps(job_to_dict(job(key="same"))).encode()
        first = b.handle_message(f"{TOPIC_PREFIX}cmd/job", payload)[0].payload
        again = b.handle_message(f"{TOPIC_PREFIX}cmd/job", payload)[0].payload
        self.assertEqual(first, again)


if __name__ == "__main__":
    unittest.main()
