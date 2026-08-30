# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Coordinator tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================

import json
import unittest
from pathlib import Path

from hydra_umc_bridge_ros2 import BridgeJob, CellState, JobPhase, MachineState, Ros2Coordinator, Ros2InterfacePlan


def job(phase=JobPhase.PROCESS, state=MachineState.IDLE):
    return BridgeJob("job-1", "idempotency-1", "ros2", phase, state, {})


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = Ros2Coordinator()

    def test_ready_job_uses_cancellable_action(self):
        result = self.coordinator.dispatch(job(), CellState.READY)
        self.assertTrue(result.accepted)
        self.assertEqual(result.interface, "/hydra_umc/execute_cell_job")

    def test_busy_machine_is_not_reused(self):
        self.assertFalse(self.coordinator.dispatch(job(state=MachineState.RUNNING), CellState.READY).accepted)

    def test_abort_stays_available_during_fault(self):
        result = self.coordinator.dispatch(job(JobPhase.ABORT, MachineState.FAULT), CellState.FAULT)
        self.assertTrue(result.accepted)
        self.assertEqual(result.interface, "/hydra_umc/request_safe_stop")

    def test_unknown_sdk_phase_fails_closed_instead_of_using_the_job_action(self):
        unknown = BridgeJob("job-2", "idempotency-2", "ros2", "SOME_FUTURE_PHASE", MachineState.IDLE, {})
        result = self.coordinator.dispatch(unknown, CellState.READY)
        self.assertFalse(result.accepted)
        self.assertEqual(result.interface, "none")

    def test_interface_plan_is_static_and_explicitly_not_a_runtime(self):
        plan = self.coordinator.interface_plan().to_dict()
        self.assertEqual(plan["schema_version"], "1.1")
        self.assertEqual(plan["mode"], "plan-only")
        self.assertEqual(plan["job_action"], "/hydra_umc/execute_cell_job")
        self.assertEqual(plan["safe_stop_service"], "/hydra_umc/request_safe_stop")

    def test_interface_plan_declares_transient_local_durability_for_the_state_topic(self):
        # Researched against ROS 2's own real QoS defaults (design.ros2.org/
        # articles/qos.html): "volatile" is the real default and drops the
        # last sample for any subscriber that joins after the last publish.
        # A machine-state topic needs "transient_local" (ROS 2's real
        # equivalent of ROS 1's latched publisher) so a late-joining
        # monitor sees the current state immediately, not just future
        # changes.
        plan = self.coordinator.interface_plan()
        self.assertEqual(plan.state_topic_durability, "transient_local")

    def test_interface_plan_matches_the_published_v1_1_compatibility_fixture(self):
        fixture = Path(__file__).parent / "fixtures" / "interface-plan-v1.1.json"
        expected = json.loads(fixture.read_text(encoding="utf-8"))
        parsed = Ros2InterfacePlan.from_dict(expected)
        self.assertEqual(parsed.to_dict(), self.coordinator.interface_plan().to_dict())

    def test_interface_plan_rejects_the_superseded_v1_0_fixture(self):
        # The real v1.0 contract (no QoS field at all) is a genuinely
        # different, now-superseded schema - a v1.1-only parser must fail
        # closed on it rather than silently accepting a plan with no
        # durability guarantee for the state topic.
        fixture = Path(__file__).parent / "fixtures" / "interface-plan-v1.json"
        expected = json.loads(fixture.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            Ros2InterfacePlan.from_dict(expected)

    def test_interface_plan_rejects_schema_or_namespace_drift(self):
        plan = self.coordinator.interface_plan().to_dict()
        plan["schema_version"] = "2.0"
        with self.assertRaises(ValueError):
            Ros2InterfacePlan.from_dict(plan)
        plan["schema_version"] = "1.1"
        plan["job_action"] = "/unowned/execute"
        with self.assertRaises(ValueError):
            Ros2InterfacePlan.from_dict(plan)

    def test_interface_plan_rejects_an_unreal_durability_value(self):
        # "transient_local" and "volatile" are ROS 2's real, closed set of
        # durability QoS policy values - anything else is a typo, not a
        # real ROS 2 concept, and must fail before it reaches a future
        # rclpy adapter.
        plan = self.coordinator.interface_plan().to_dict()
        plan["state_topic_durability"] = "latched"
        with self.assertRaises(ValueError):
            Ros2InterfacePlan.from_dict(plan)


if __name__ == "__main__":
    unittest.main()
