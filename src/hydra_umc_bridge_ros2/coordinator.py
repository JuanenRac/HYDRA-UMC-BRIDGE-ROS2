# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - ROS 2 safety coordinator
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Map a correlated cell job onto ROS 2 interface kinds without raw motion.

This module deliberately has no rclpy dependency.  It can be tested on any
host and becomes a ROS 2 node only through a separately deployed adapter.
"""

from __future__ import annotations

from dataclasses import dataclass

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, evaluate_job


@dataclass(frozen=True)
class Ros2Dispatch:
    accepted: bool
    interface: str
    reason: str


class Ros2Coordinator:
    """Gate jobs before a future rclpy adapter creates a ROS message/action."""

    state_topic = "/hydra_umc/machine_state"
    inspect_service = "/hydra_umc/inspect_cell"
    job_action = "/hydra_umc/execute_cell_job"

    def dispatch(self, job: BridgeJob, cell_state: CellState) -> Ros2Dispatch:
        decision = evaluate_job(job, cell_state)
        return Ros2Dispatch(decision.allowed, self.job_action, decision.reason)
