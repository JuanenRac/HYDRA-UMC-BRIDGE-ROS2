# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Real, partial rclpy transport
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Real rclpy transport for the 2 real, standard-typed interfaces this
bridge can honestly connect today - never invents a message type.

Of the 4 interfaces `Ros2Coordinator` plans (state_topic/inspect_service/
job_action/safe_stop_service), only 2 have a real, standard ROS 2 message
type this module can honestly use without this repository first defining
its own custom `.srv`/`.action` package (a separate, larger task - not
something to invent here just to claim "connected"):

- `safe_stop_service` -> a real `std_srvs/srv/Trigger` client (empty
  request, `{success: bool, message: string}` response) - a real, common
  ROS 2 pattern for exactly this "call this service, tell me if it
  worked" shape, and ROS 2 ships `std_srvs` by default.
- `state_topic` -> a real `std_msgs/msg/String` subscriber, using the
  real `transient_local` durability QoS `Ros2InterfacePlan` already
  declares (see coordinator.py's own comment on why: ROS 2's real default
  is `volatile`, which would miss the current state for a late-joining
  subscriber).

`inspect_service` and `job_action` need a custom `.srv`/`.action`
definition this repository does not have yet - there is no real, honest
message type to build a client against today, so this module deliberately
does not touch them. See docs/BRIDGE_GUIDE.md for this scope boundary.

rclpy is imported lazily, in `create_ros2_node()` only, so the rest of this
module (and every test) works on a host without a ROS 2 install.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

# Real ROS 2 QoS durability values this module can request - matches the
# same closed set already validated in Ros2InterfacePlan.from_dict().
TRANSIENT_LOCAL = "transient_local"


class Ros2Node(Protocol):
    """The minimal real interface this module depends on - matches
    rclpy.node.Node's own real method signatures for the 2 calls it needs."""

    def create_client(self, srv_type: object, srv_name: str) -> "Ros2ServiceClient": ...
    def create_subscription(
        self, msg_type: object, topic: str, callback: Callable[[object], None], qos_profile: object
    ) -> object: ...


class Ros2ServiceClient(Protocol):
    """Matches rclpy's own real `Client` interface for a synchronous-style call."""

    def wait_for_service(self, timeout_sec: float | None = None) -> bool: ...
    def call(self, request: object) -> object: ...


def create_ros2_node(node_name: str) -> Ros2Node:
    """Initialize rclpy and return a real Node. The only place this module
    imports rclpy/std_srvs/std_msgs.

    Raises RuntimeError with a clear message if rclpy isn't installed,
    rather than letting an ImportError surface from deep inside this
    module.
    """

    try:
        import rclpy  # type: ignore[import-untyped]
        from rclpy.node import Node  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(
            "rclpy is not installed - install a real ROS 2 distribution to talk to a real ROS 2 graph "
            "(this module's request-building/gating logic works and is tested without it)"
        ) from error
    if not rclpy.ok():
        rclpy.init()
    return Node(node_name)


@dataclass(frozen=True)
class SafeStopResult:
    called: bool
    success: bool
    reason: str


class Ros2SafeStopClient:
    """Calls the real safe_stop_service via a real std_srvs/Trigger client."""

    def call(self, node: Ros2Node, service_name: str, *, timeout_sec: float = 2.0) -> SafeStopResult:
        try:
            from std_srvs.srv import Trigger  # type: ignore[import-untyped]
        except ImportError as error:
            return SafeStopResult(False, False, f"std_srvs is not available: {error}")

        client = node.create_client(Trigger, service_name)
        if not client.wait_for_service(timeout_sec=timeout_sec):
            return SafeStopResult(False, False, f"service {service_name} is not available")
        try:
            response = client.call(Trigger.Request())
        except OSError as error:
            return SafeStopResult(True, False, f"safe stop call failed: {error}")
        return SafeStopResult(True, bool(response.success), str(response.message))


@dataclass(frozen=True)
class SubscribeResult:
    subscribed: bool
    reason: str
    subscription: object = None


class Ros2StateSubscriber:
    """Subscribes to the real state_topic via a real std_msgs/String subscription.

    Uses the real transient_local durability QoS Ros2InterfacePlan already
    declares - see this module's own docstring for why a plain default
    (volatile) QoS would miss the current state for a late-joining
    subscriber.
    """

    def subscribe(self, node: Ros2Node, topic: str, on_state: Callable[[str], None]) -> SubscribeResult:
        try:
            from std_msgs.msg import String  # type: ignore[import-untyped]
            from rclpy.qos import DurabilityPolicy, QoSProfile  # type: ignore[import-untyped]
        except ImportError as error:
            # Graceful degradation, matching Ros2SafeStopClient.call()'s own
            # shape - a missing ROS 2 package is a real, expected condition
            # on a host without a full ROS 2 install, not a crash.
            return SubscribeResult(False, f"std_msgs/rclpy.qos is not available: {error}")

        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        subscription = node.create_subscription(String, topic, lambda msg: on_state(msg.data), qos)
        return SubscribeResult(True, "subscribed", subscription)
