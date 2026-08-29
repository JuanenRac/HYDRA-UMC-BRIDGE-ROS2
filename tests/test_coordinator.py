# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Coordinator tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================

import unittest

from hydra_umc_bridge_ros2 import BridgeJob, CellState, JobPhase, MachineState, Ros2Coordinator


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
        self.assertEqual(plan["schema_version"], "1.0")
        self.assertEqual(plan["mode"], "plan-only")
        self.assertEqual(plan["job_action"], "/hydra_umc/execute_cell_job")
        self.assertEqual(plan["safe_stop_service"], "/hydra_umc/request_safe_stop")


if __name__ == "__main__":
    unittest.main()
