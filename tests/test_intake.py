# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Retained messages, repeated idempotency keys and the observed machine state
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""docs/BRIDGE_CONTRACT.md: a retained message is not a request, an idempotency key is handled once, and the gate uses the machine state the bridge observes."""

import json
import unittest

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict
from hydra_umc_sdk.job_intake import IdempotencyStore
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX, Ros2MqttBridge


class FakeNode:
    pass


def message(key="key-1", claimed=MachineState.IDLE):
    job = BridgeJob("job-1", key, "orchestrator", JobPhase.LOAD, claimed, {})
    return json.dumps(job_to_dict(job)).encode("utf-8")


def bridge(cell=CellState.READY, store=None, observed="IDLE"):
    # a bridge that has heard the state topic: without a reading of the machine every productive job is refused
    b = Ros2MqttBridge(FakeNode(), lambda: cell, store)  # type: ignore[arg-type]
    if observed is not None:
        b.start_observing()
        b.observe(observed)
    return b


def accepted(publishes):
    return json.loads(publishes[0].payload)["accepted"]


class RetainedTests(unittest.TestCase):
    def test_a_retained_job_and_a_retained_safe_stop_are_not_acted_on(self):
        b = bridge()
        self.assertEqual(b.handle_message(f"{TOPIC_PREFIX}cmd/job", message(), retained=True), [])
        self.assertEqual(b.handle_message(f"{TOPIC_PREFIX}cmd/safe_stop", b"", retained=True), [])

    def test_the_same_job_not_retained_is_acted_on(self):
        self.assertTrue(accepted(bridge().handle_message(f"{TOPIC_PREFIX}cmd/job", message())))


class IdempotencyTests(unittest.TestCase):
    def test_a_repeated_key_gets_the_first_answer(self):
        b = bridge(store=IdempotencyStore())
        first = b.handle_message(f"{TOPIC_PREFIX}cmd/job", message())
        second = b.handle_message(f"{TOPIC_PREFIX}cmd/job", message())
        self.assertTrue(accepted(first))
        self.assertEqual(second[0].payload, first[0].payload)

    def test_a_different_key_is_a_different_job(self):
        b = bridge(store=IdempotencyStore())
        b.handle_message(f"{TOPIC_PREFIX}cmd/job", message("a"))
        self.assertTrue(accepted(b.handle_message(f"{TOPIC_PREFIX}cmd/job", message("b"))))


class ObservedStateTests(unittest.TestCase):
    def test_without_observation_the_job_is_refused_whatever_it_claims(self):
        # the claim in the job message is not a reading of the machine
        for claimed in (MachineState.IDLE, MachineState.RUNNING):
            b = bridge(observed=None)
            publishes = b.handle_message(f"{TOPIC_PREFIX}cmd/job", message(claimed=claimed))
            self.assertFalse(accepted(publishes))
            self.assertIn("did not read", json.loads(publishes[0].payload)["reason"])

    def test_once_observing_the_reported_state_beats_the_claim(self):
        b = bridge()
        b.start_observing()
        b.observe("RUNNING")
        # the requester claims IDLE; the ROS 2 topic says RUNNING
        self.assertFalse(accepted(b.handle_message(f"{TOPIC_PREFIX}cmd/job", message(claimed=MachineState.IDLE))))
        b.observe("idle")
        self.assertTrue(accepted(b.handle_message(f"{TOPIC_PREFIX}cmd/job", message(claimed=MachineState.RUNNING))))

    def test_a_machine_never_heard_from_or_in_an_unknown_state_is_not_idle(self):
        b = bridge()
        b.start_observing()
        self.assertFalse(accepted(b.handle_message(f"{TOPIC_PREFIX}cmd/job", message(claimed=MachineState.IDLE))))
        b.observe("something odd")
        self.assertFalse(accepted(b.handle_message(f"{TOPIC_PREFIX}cmd/job", message(claimed=MachineState.IDLE))))


if __name__ == "__main__":
    unittest.main()
