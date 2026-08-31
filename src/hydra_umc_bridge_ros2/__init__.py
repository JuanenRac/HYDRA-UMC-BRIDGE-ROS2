# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Public package interface
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================

"""Fail-safe, high-level ROS 2 coordination planning for HYDRA-UMC."""

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState

from .coordinator import Ros2Coordinator, Ros2Dispatch, Ros2InterfacePlan
from .mqtt_transport import MqttPublish, Ros2MqttBridge, bridge_state_to_mqtt, run_forever
from .rclpy_transport import (
    Ros2SafeStopClient,
    Ros2StateSubscriber,
    SafeStopResult,
    SubscribeResult,
    create_ros2_node,
)

__all__ = [
    "BridgeJob",
    "CellState",
    "JobPhase",
    "MachineState",
    "Ros2Coordinator",
    "Ros2Dispatch",
    "Ros2InterfacePlan",
    "Ros2SafeStopClient",
    "Ros2StateSubscriber",
    "SafeStopResult",
    "SubscribeResult",
    "create_ros2_node",
    "Ros2MqttBridge",
    "MqttPublish",
    "bridge_state_to_mqtt",
    "run_forever",
]
