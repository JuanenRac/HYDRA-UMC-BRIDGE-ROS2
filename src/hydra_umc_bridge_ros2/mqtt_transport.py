# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Real MQTT transport over HYDRA-UMC-MQTT-BROKER
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Reach this bridge's already-real logic over the real MQTT broker.

Two real commands this module can send are already implemented:
`Ros2Coordinator.dispatch()` (plan-only - names which real ROS 2
interface a job phase maps to and applies the shared SDK gate, without
touching rclpy) and `Ros2SafeStopClient.call()` (a real
`std_srvs/Trigger` client call, over an already-created rclpy `Node`).
This module adds a new transport (MQTT, per the ecosystem's own "MQTT
via the real broker, real commands included" decision) to both - it does
not grant any new physical authority, and `inspect_service`/`job_action`
stay untouched here for the exact reason `rclpy_transport.py`'s own
module docstring already gives: no real, honest message/action type
exists for them yet.

`Ros2MqttBridge.handle_message()` is the one real place topic routing
happens, and `cmd/job` is fully testable without rclpy installed (it
never imports it). `cmd/safe_stop` calls straight through to
`Ros2SafeStopClient.call()` - on a host without a real ROS 2 install
that already degrades to a real, honest "std_srvs is not available"
result (see `test_rclpy_transport.py`'s own documented limitation: rclpy
cannot be usefully faked at the message-type level, unlike this
ecosystem's other lazily-imported transports). `bridge_state_to_mqtt()`
is the real, intended use of `Ros2StateSubscriber`'s own `on_state`
callback hook (see `rclpy_transport.py`'s own docstring: "becomes a ROS 2
node only through a separately deployed adapter" - this module is that
adapter) - it republishes the real ROS 2 state topic onto
`hydra/bridges/ros2/state` (retained) as each message arrives.
`run_forever()` is the thin real-I/O glue that lazily imports
`paho-mqtt` and creates the real rclpy node.

Topic scheme (see HYDRA-UMC-MQTT-BROKER's own `hydra/bridges/<name>/...`
convention, `docs/BRIDGE_TOPICS.md`):
  hydra/bridges/ros2/state                <- published, RETAINED, republished from the
                                              real ROS 2 state_topic (transient_local QoS)
  hydra/bridges/ros2/cmd/job               -> BridgeJob JSON (job_to_dict shape) - Ros2Dispatch (plan-only)
  hydra/bridges/ros2/cmd/safe_stop         -> (empty) real std_srvs/Trigger call
  hydra/bridges/ros2/cmd/<verb>/result     <- published, one JSON result per command above
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Callable

from hydra_umc_sdk.bridge_contract import BridgeError, CellState, job_from_dict

from .coordinator import Ros2Coordinator
from .rclpy_transport import Ros2Node, Ros2SafeStopClient, Ros2StateSubscriber

TOPIC_PREFIX = "hydra/bridges/ros2/"


class MqttPublish:
    """One real outbound MQTT publish this module decided to make."""

    __slots__ = ("topic", "payload", "retain")

    def __init__(self, topic: str, payload: str, retain: bool = False) -> None:
        self.topic = topic
        self.payload = payload
        self.retain = retain

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, MqttPublish)
            and (self.topic, self.payload, self.retain) == (other.topic, other.payload, other.retain)
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"MqttPublish(topic={self.topic!r}, payload={self.payload!r}, retain={self.retain!r})"


class Ros2MqttBridge:
    """Real command dispatch for this bridge's MQTT topics.

    `node` is an already-created rclpy `Node` (real ROS 2 graph
    membership needed only by `cmd/safe_stop`) - `cmd/job` never touches
    it, matching `Ros2Coordinator.dispatch()`'s own rclpy-free design.
    """

    def __init__(self, node: Ros2Node, cell_state: Callable[[], CellState]) -> None:
        self.node = node
        self._cell_state = cell_state
        self._coordinator = Ros2Coordinator()
        self._safe_stop = Ros2SafeStopClient()

    def handle_message(self, topic: str, payload: bytes) -> list[MqttPublish]:
        """Route one real inbound MQTT message. An unrecognised `cmd/`
        sub-topic (this bridge subscribes to `cmd/#`, a wildcard) is
        silently ignored, never an error - a future sibling topic this
        version does not know about yet must never crash the message loop."""

        if not topic.startswith(TOPIC_PREFIX):
            return []
        suffix = topic[len(TOPIC_PREFIX) :]

        if suffix == "cmd/job":
            return [self._handle_job(payload)]
        if suffix == "cmd/safe_stop":
            result = self._safe_stop.call(self.node, self._coordinator.safe_stop_service)
            return [MqttPublish(f"{TOPIC_PREFIX}cmd/safe_stop/result", json.dumps(asdict(result)))]
        return []

    def _handle_job(self, payload: bytes) -> MqttPublish:
        result_topic = f"{TOPIC_PREFIX}cmd/job/result"
        try:
            job = job_from_dict(json.loads(payload))
        except (json.JSONDecodeError, BridgeError, UnicodeDecodeError) as error:
            dispatch = {"accepted": False, "interface": "none", "reason": f"malformed job payload: {error}", "mode": "plan-only"}
            return MqttPublish(result_topic, json.dumps(dispatch))
        dispatch = self._coordinator.dispatch(job, self._cell_state())
        return MqttPublish(result_topic, json.dumps(asdict(dispatch)))


def bridge_state_to_mqtt(node: Ros2Node, publish: Callable[[str, str, bool], None]) -> None:
    """Subscribe to the real ROS 2 state topic and republish each update
    onto `hydra/bridges/ros2/state` (retained) via `publish(topic,
    payload, retain)`. The real, intended use of `Ros2StateSubscriber`'s
    own `on_state` callback hook - this function IS the "separately
    deployed adapter" `rclpy_transport.py`'s own module docstring
    describes. Not itself unit-tested beyond `Ros2StateSubscriber`'s own
    already-tested degrade-cleanly behavior (see `test_rclpy_transport.py`)
    - it requires a real rclpy `Node` either way.
    """

    def on_state(state: str) -> None:
        publish(f"{TOPIC_PREFIX}state", json.dumps({"state": state}), True)

    Ros2StateSubscriber().subscribe(node, Ros2Coordinator.state_topic, on_state)


def run_forever(
    bridge: Ros2MqttBridge,
    host: str,
    port: int = 1883,
    client_id: str = "hydra-umc-bridge-ros2",
) -> None:
    """Connect to a real HYDRA-UMC-MQTT-BROKER, bridge the real ROS 2
    state topic onto it, and dispatch commands forever.

    The only place this module imports paho-mqtt - lazily, so the rest of
    this module (and every test) works on a host without it installed.
    """

    try:
        import paho.mqtt.client as mqtt  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(
            "paho-mqtt is not installed - install it to connect to a real HYDRA-UMC-MQTT-BROKER "
            "(this module's topic-dispatch/gating logic works and is tested without it)"
        ) from error

    def on_connect(client: object, userdata: object, flags: object, reason_code: object, properties: object = None) -> None:
        client.subscribe(f"{TOPIC_PREFIX}cmd/#")  # type: ignore[attr-defined]

    def on_message(client: object, userdata: object, message: object) -> None:
        for publish in bridge.handle_message(message.topic, message.payload):  # type: ignore[attr-defined]
            client.publish(publish.topic, publish.payload, retain=publish.retain)  # type: ignore[attr-defined]

    client = mqtt.Client(client_id=client_id)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port)
    bridge_state_to_mqtt(bridge.node, lambda topic, payload, retain: client.publish(topic, payload, retain=retain))
    client.loop_forever()
