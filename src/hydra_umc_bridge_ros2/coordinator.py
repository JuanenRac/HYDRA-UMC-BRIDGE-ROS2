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
    mode: str = "plan-only"


@dataclass(frozen=True)
class Ros2InterfacePlan:
    """Static interface names reserved for a future separately deployed node."""

    schema_version: str
    mode: str
    state_topic: str
    inspect_service: str
    job_action: str
    safe_stop_service: str

    def to_dict(self) -> dict[str, str]:
        """Serialize a dependency-free plan, not a discovered ROS graph."""

        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "state_topic": self.state_topic,
            "inspect_service": self.inspect_service,
            "job_action": self.job_action,
            "safe_stop_service": self.safe_stop_service,
        }


class Ros2Coordinator:
    """Gate jobs before a future rclpy adapter creates a ROS message/action."""

    state_topic = "/hydra_umc/machine_state"
    inspect_service = "/hydra_umc/inspect_cell"
    job_action = "/hydra_umc/execute_cell_job"
    safe_stop_service = "/hydra_umc/request_safe_stop"

    _interfaces = {
        JobPhase.PREPARE: job_action,
        JobPhase.LOAD: job_action,
        JobPhase.PROCESS: job_action,
        JobPhase.UNLOAD: job_action,
        JobPhase.COMPLETE: job_action,
        JobPhase.ABORT: safe_stop_service,
    }

    def interface_plan(self) -> Ros2InterfacePlan:
        """Return static interface evidence without importing or starting ROS 2."""

        return Ros2InterfacePlan(
            "1.0",
            "plan-only",
            self.state_topic,
            self.inspect_service,
            self.job_action,
            self.safe_stop_service,
        )

    def dispatch(self, job: BridgeJob, cell_state: CellState) -> Ros2Dispatch:
        interface = self._interfaces.get(job.phase)
        if interface is None:
            return Ros2Dispatch(
                False,
                "none",
                "job phase is not implemented by the ROS 2 interface plan",
            )
        decision = evaluate_job(job, cell_state)
        return Ros2Dispatch(decision.allowed, interface, decision.reason)
