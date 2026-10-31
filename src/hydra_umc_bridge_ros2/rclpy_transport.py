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

import threading
import time
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


class Ros2Future(Protocol):
    """Matches rclpy's own real `Future` interface - the pending result of a `call_async()`."""

    def done(self) -> bool: ...
    def result(self) -> object: ...
    def exception(self) -> BaseException | None: ...


class Ros2ServiceClient(Protocol):
    """Matches rclpy's own real `Client` interface for an async-style call."""

    def wait_for_service(self, timeout_sec: float | None = None) -> bool: ...
    def call_async(self, request: object) -> Ros2Future: ...


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


class Ros2NodeSpinner:
    """Runs the node's callbacks (the state subscription among them) on a thread of its own.

    In rclpy a subscription callback only runs while an executor spins the
    node. The main thread of the MQTT bridge sits in the network loop, so
    without this nothing would ever deliver the machine state and every
    productive job would be refused as OFFLINE. A node that is spun here must
    not also be spun by `rclpy.spin_until_future_complete` (two executors on one
    node fail), so `Ros2SafeStopClient(node_is_spinning=True)` waits for its
    answer instead of spinning.
    """

    def __init__(self, executor: object, thread: threading.Thread) -> None:
        self._executor = executor
        self._thread = thread

    def stop(self, timeout_sec: float = 2.0) -> None:
        self._executor.shutdown()  # type: ignore[attr-defined]
        self._thread.join(timeout_sec)


def spin_node_in_background(node: Ros2Node) -> Ros2NodeSpinner:
    """Spin `node` on a daemon thread with a `SingleThreadedExecutor`. Raises
    RuntimeError, like `create_ros2_node()`, when rclpy is not installed."""

    try:
        from rclpy.executors import SingleThreadedExecutor  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(
            "rclpy is not installed - install a real ROS 2 distribution to talk to a real ROS 2 graph "
            "(this module's request-building/gating logic works and is tested without it)"
        ) from error
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, name="hydra-ros2-spin", daemon=True)
    thread.start()
    return Ros2NodeSpinner(executor, thread)


@dataclass(frozen=True)
class SafeStopResult:
    called: bool
    success: bool
    reason: str


class Ros2SafeStopClient:
    """Calls the real safe_stop_service via a real std_srvs/Trigger client.

    :
    this used to call the synchronous-style `client.call(request)` -
    `wait_for_service(timeout_sec=...)` above only bounds whether the
    service exists, never the response itself. Real rclpy's own
    synchronous `call()` needs an executor already spinning to resolve
    its underlying future at all, and even then carries no deadline of
    its own - a service that accepts the request but never replies (a
    real, possible failure on a degraded ROS 2 graph, not a hypothetical)
    left this call blocked with no bounded progress guarantee. Fixed:
    `call_async()` + `rclpy.spin_until_future_complete(node, future,
    timeout_sec=...)` - the same node this bridge already owns drives its
    own future to completion or to the real timeout, whichever comes
    first, so a service that never answers times out instead of hanging
    this bridge's own request handling forever.

    The service client is created once per node and service and reused (a new
    one per call leaks DDS entities), and nothing is waited for when the
    service is already known to be up. When the node is spun by
    `spin_node_in_background()` pass `node_is_spinning=True`: the call then
    waits for the answer instead of spinning the node a second time.
    Whatever goes wrong comes back as a `SafeStopResult`, never as an exception.
    """

    def __init__(self, *, node_is_spinning: bool = False) -> None:
        self._node_is_spinning = node_is_spinning
        self._client: Ros2ServiceClient | None = None
        self._client_key: tuple[int, str] | None = None

    def _client_for(self, node: Ros2Node, trigger: object, service_name: str) -> Ros2ServiceClient:
        key = (id(node), service_name)
        if self._client is None or self._client_key != key:
            self._client = node.create_client(trigger, service_name)
            self._client_key = key
        return self._client

    def call(self, node: Ros2Node, service_name: str, *, timeout_sec: float = 2.0) -> SafeStopResult:
        try:
            import rclpy  # type: ignore[import-untyped]
            from std_srvs.srv import Trigger  # type: ignore[import-untyped]
        except ImportError as error:
            return SafeStopResult(False, False, f"rclpy/std_srvs is not available: {error}")

        try:
            client = self._client_for(node, Trigger, service_name)
            ready = getattr(client, "service_is_ready", None)
            if not (callable(ready) and ready()) and not client.wait_for_service(timeout_sec=timeout_sec):
                return SafeStopResult(False, False, f"service {service_name} is not available")
            future = client.call_async(Trigger.Request())
        except Exception as error:  # noqa: BLE001 - e.g. a down rclpy context: fail safe and say so, never crash the message loop
            return SafeStopResult(False, False, f"safe stop call could not be made: {error}")
        try:
            if self._node_is_spinning:
                deadline = time.monotonic() + timeout_sec
                while not future.done() and time.monotonic() < deadline:
                    time.sleep(0.01)
            else:
                rclpy.spin_until_future_complete(node, future, timeout_sec=timeout_sec)
        except Exception as error:  # noqa: BLE001 - a real, unexpected executor/transport failure must fail safe, not crash the caller
            return SafeStopResult(True, False, f"safe stop call failed: {error}")

        if not future.done():
            return SafeStopResult(True, False, f"safe stop call to {service_name} timed out after {timeout_sec}s")
        exc = future.exception()
        if exc is not None:
            return SafeStopResult(True, False, f"safe stop call failed: {exc}")
        response = future.result()
        return SafeStopResult(True, bool(response.success), str(response.message))  # type: ignore[attr-defined]


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
        subscription = node.create_subscription(String, topic, lambda msg: on_state(msg.data), qos)  # type: ignore[attr-defined]
        return SubscribeResult(True, "subscribed", subscription)
