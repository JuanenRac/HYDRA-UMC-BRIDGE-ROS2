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
from typing import Mapping

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

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "Ros2InterfacePlan":
        """Validate a serialized interface-plan contract before using it.

        The v1 plan is deliberately a small JSON-compatible object so a future
        rclpy adapter can consume the same evidence. Unknown or missing fields
        are rejected rather than silently becoming a different interface.
        """

        expected = {
            "schema_version",
            "mode",
            "state_topic",
            "inspect_service",
            "job_action",
            "safe_stop_service",
        }
        if set(payload) != expected:
            raise ValueError("ROS 2 interface plan fields do not match schema 1.0")
        values = {name: payload[name] for name in expected}
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("ROS 2 interface plan values must be non-empty strings")
        if values["schema_version"] != "1.0":
            raise ValueError(f"unsupported ROS 2 interface plan schema: {values['schema_version']}")
        if values["mode"] != "plan-only":
            raise ValueError("ROS 2 interface plan must remain plan-only")
        for name in ("state_topic", "inspect_service", "job_action", "safe_stop_service"):
            if not values[name].startswith("/hydra_umc/"):
                raise ValueError(f"ROS 2 interface {name} must stay in the /hydra_umc namespace")
        if len({values[name] for name in expected - {"schema_version", "mode"}}) != 4:
            raise ValueError("ROS 2 interface names must remain distinct")
        return cls(**values)  # type: ignore[arg-type]


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
