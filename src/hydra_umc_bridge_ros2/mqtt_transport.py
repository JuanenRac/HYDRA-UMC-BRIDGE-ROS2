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
                                              real ROS 2 state_topic (transient_local QoS):
                                              {"state", "machine_state", "observed_at" (epoch ms)}
  hydra/bridges/ros2/availability          <- "online" once connected, "offline" (Last Will) when the link is lost
  hydra/bridges/ros2/cmd/job               -> BridgeJob JSON (job_to_dict shape) - Ros2Dispatch (plan-only)
  hydra/bridges/ros2/cmd/safe_stop         -> (empty) real std_srvs/Trigger call
  hydra/bridges/ros2/cmd/<verb>/result     <- published, one JSON result per command above
                                              (cmd/job: "allowed" as every bridge answers, and "accepted" as before)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from typing import Callable

from hydra_umc_sdk.bridge_contract import BridgeError, CellState, MachineState, job_from_dict
from hydra_umc_sdk.job_intake import IdempotencyStore
from hydra_umc_sdk.bridge_transport import MessageDispatcher as SharedMessageDispatcher
from hydra_umc_sdk.bridge_transport import MqttPublish, connect_with_retry, open_bridge_client

from .coordinator import Ros2Coordinator, Ros2Dispatch
from .rclpy_transport import Ros2Node, Ros2SafeStopClient, Ros2StateSubscriber, SubscribeResult, spin_node_in_background

_LOG = logging.getLogger(__name__)

TOPIC_PREFIX = "hydra/bridges/ros2/"
AVAILABILITY_TOPIC = f"{TOPIC_PREFIX}availability"
# Nothing legitimate is larger than this (a BridgeJob is a few hundred bytes); anything bigger is refused unparsed.
MAX_PAYLOAD_BYTES = 65536
# The commands that must never wait behind another one.
STOP_VERBS = frozenset({"cmd/safe_stop"})


def _dispatch_result(dispatch: Ros2Dispatch) -> str:
    """The answer to a `cmd/job`: `allowed` is the key every bridge answers with, `accepted` stays for existing readers."""
    answer = asdict(dispatch)
    answer["allowed"] = dispatch.accepted
    return json.dumps(answer)


def _refusal(suffix: str, reason: str) -> list["MqttPublish"]:
    result = {"allowed": False, "accepted": False, "interface": "none", "reason": reason, "mode": "plan-only"}
    return [MqttPublish(f"{TOPIC_PREFIX}{suffix}/result", json.dumps(result))]


def state_payload(state: str, observed_at_ms: int) -> str:
    """What is republished on `state`: the raw string the ROS 2 topic carried, the `MachineState` it normalizes to (null when it is none), and when it was heard."""
    try:
        machine_state: str | None = MachineState(str(state).strip().upper()).value
    except ValueError:
        machine_state = None
    return json.dumps({"state": state, "machine_state": machine_state, "observed_at": observed_at_ms})


class Ros2MqttBridge:
    """Real command dispatch for this bridge's MQTT topics.

    `node` is an already-created rclpy `Node` (real ROS 2 graph
    membership needed only by `cmd/safe_stop`) - `cmd/job` never touches
    it, matching `Ros2Coordinator.dispatch()`'s own rclpy-free design.
    """

    def __init__(
        self,
        node: Ros2Node,
        cell_state: Callable[[], CellState],
        idempotency: IdempotencyStore | None = None,
        *,
        node_is_spinning: bool = False,
        observed_max_age_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.node = node
        self._cell_state = cell_state
        self._coordinator = Ros2Coordinator()
        self._safe_stop = Ros2SafeStopClient(node_is_spinning=node_is_spinning)
        # How old the last reported state may be before it stops counting. None keeps it for ever, which is only right
        # for a publisher that republishes on a timer: a publisher that died after saying IDLE would otherwise
        # be believed IDLE for ever. Set it to a few of that publisher's periods.
        self._observed_max_age = observed_max_age_seconds
        self._clock = clock
        self._observed_at = 0.0
        # docs/BRIDGE_CONTRACT.md: the idempotency key of a job is handled once; a repeat gets the answer that was given
        self._idempotency = idempotency
        # The machine state as the ROS 2 state topic reports it. Before `start_observing()` nothing has been read and the gate refuses the job; once observing, a machine whose state
        # was never heard (or is not a known state) counts as OFFLINE, so nothing productive is forwarded on a claim.
        self._observing = False
        self._observed: MachineState | None = None

    def start_observing(self) -> None:
        self._observing = True
        self._observed = None

    def observe(self, state: str) -> None:
        """One message of the ROS 2 state topic (`MachineState` names; anything else is not trusted)."""
        self._observing = True
        try:
            self._observed = MachineState(str(state).strip().upper())
        except ValueError:
            self._observed = None
        self._observed_at = self._clock()

    def _observed_state(self) -> MachineState | None:
        if not self._observing:
            return None
        if self._observed is not None and self._observed_max_age is not None and self._clock() - self._observed_at > self._observed_max_age:
            return MachineState.OFFLINE
        return self._observed if self._observed is not None else MachineState.OFFLINE

    def handle_message(self, topic: str, payload: bytes, retained: bool = False) -> list[MqttPublish]:
        """Route one real inbound MQTT message. An unrecognised `cmd/`
        sub-topic (this bridge subscribes to `cmd/#`, a wildcard) is
        silently ignored, never an error - a future sibling topic this
        version does not know about yet must never crash the message loop.

        A RETAINED message is ignored: the broker replays it at every
        reconnect, so a retained `cmd/job` or `cmd/safe_stop` would repeat a
        command nobody sent again.

        This never raises: an oversized payload and any failure while handling
        the message (a deeply nested JSON, an rclpy error) come back as a
        refusal on the verb's result topic."""

        if retained or not topic.startswith(TOPIC_PREFIX):
            return []
        suffix = topic[len(TOPIC_PREFIX) :]
        if suffix not in ("cmd/job", "cmd/safe_stop"):
            return []
        if len(payload) > MAX_PAYLOAD_BYTES:
            return _refusal(suffix, f"payload is larger than {MAX_PAYLOAD_BYTES} bytes")
        try:
            if suffix == "cmd/job":
                return [self._handle_job(payload)]
            result = self._safe_stop.call(self.node, self._coordinator.safe_stop_service)
            return [MqttPublish(f"{TOPIC_PREFIX}cmd/safe_stop/result", json.dumps(asdict(result)))]
        except Exception as error:  # noqa: BLE001 - nothing a message does may end the loop
            _LOG.exception("could not handle %s", topic)
            return _refusal(suffix, f"the command could not be handled: {type(error).__name__}")

    def _handle_job(self, payload: bytes) -> MqttPublish:
        result_topic = f"{TOPIC_PREFIX}cmd/job/result"
        try:
            job = job_from_dict(json.loads(payload))
        except (json.JSONDecodeError, BridgeError, UnicodeDecodeError) as error:
            dispatch = {"allowed": False, "accepted": False, "interface": "none", "reason": f"malformed job payload: {error}", "mode": "plan-only"}
            return MqttPublish(result_topic, json.dumps(dispatch))
        if self._idempotency is not None and not self._idempotency.claim(job.idempotency_key):
            stored = self._idempotency.recall(job.idempotency_key)
            if stored is None:
                stored = json.dumps({"allowed": False, "accepted": False, "interface": "none", "reason": "this idempotency_key was already handled", "mode": "plan-only"})
            return MqttPublish(result_topic, stored)
        dispatch = self._coordinator.dispatch(job, self._cell_state(), self._observed_state())
        publish = MqttPublish(result_topic, _dispatch_result(dispatch))
        if self._idempotency is not None:
            self._idempotency.remember(job.idempotency_key, publish.payload)
        return publish


def bridge_state_to_mqtt(
    node: Ros2Node,
    publish: Callable[[str, str, bool], None],
    on_observed: Callable[[str], None] | None = None,
    clock: Callable[[], float] = time.time,
) -> SubscribeResult:
    """Subscribe to the real ROS 2 state topic and republish each update
    onto `hydra/bridges/ros2/state` (retained) via `publish(topic,
    payload, retain)`. The real, intended use of `Ros2StateSubscriber`'s
    own `on_state` callback hook - this function IS the "separately
    deployed adapter" `rclpy_transport.py`'s own module docstring
    describes. Returns the `SubscribeResult`: when ROS 2's message packages are
    missing it says so (`subscribed` False) and `run_forever()` refuses to start
    on it instead of "observing" nothing and refusing every job as OFFLINE.
    """

    def on_state(state: str) -> None:
        if on_observed is not None:
            on_observed(state)
        publish(f"{TOPIC_PREFIX}state", state_payload(state, int(clock() * 1000)), True)

    return Ros2StateSubscriber().subscribe(node, Ros2Coordinator.state_topic, on_state)


class MessageDispatcher(SharedMessageDispatcher):
    """Takes the messages off the network thread.

    A safe-stop call waits for the ROS 2 service (up to its timeout); doing that
    on the thread that runs the MQTT keep-alive would hold up everything else.
    Ordinary commands run one at a time, in order, on one lane; the stop
    commands (`STOP_VERBS`) run on a second lane so they never wait behind one.
    """

    def __init__(self, bridge: object, stop_verbs: frozenset[str] = STOP_VERBS) -> None:
        super().__init__(bridge, TOPIC_PREFIX, stop_verbs)  # type: ignore[arg-type]


def run_forever(
    bridge: Ros2MqttBridge,
    host: str,
    port: int = 1883,
    client_id: str = "hydra-umc-bridge-ros2",
    *,
    max_connect_attempts: int = 5,
    connect_retry_delay_seconds: float = 2.0,
    username: str | None = None,
    password: str | None = None,
) -> None:
    """Connect to a real HYDRA-UMC-MQTT-BROKER, bridge the real ROS 2
    state topic onto it, and dispatch commands forever.

    The only place this module imports paho-mqtt - lazily, so the rest of
    this module (and every test) works on a host without it installed.
    The initial connect is retried (see connect_with_retry()) - once
    connected, paho-mqtt's own loop_forever() already handles a
    mid-session drop/reconnect on its own, so only the first connect
    needed this.

    The bridge's node is spun on a thread of its own (so the state topic is
    really delivered) and the bridge must have been built with
    `node_is_spinning=True`, as the safe-stop call then waits instead of
    spinning the node a second time. The bridge announces itself on
    `availability`: "online" once connected, and the broker publishes "offline"
    (the Last Will) if the connection is lost. When the ROS 2 message
    packages are missing there is nothing to observe, so this refuses to
    start rather than refuse every job as OFFLINE.
    """

    if password is not None and username is None:
        raise ValueError("password was given without a username - MQTT credentials need both")

    dispatcher = MessageDispatcher(bridge)
    spinner = None
    try:
        client = open_bridge_client(
            host,
            port,
            client_id=client_id,
            topic_prefix=TOPIC_PREFIX,
            dispatcher=dispatcher,
            username=username,
            password=password,
            max_connect_attempts=max_connect_attempts,
            connect_retry_delay_seconds=connect_retry_delay_seconds,
        )
        spinner = spin_node_in_background(bridge.node)
        bridge.start_observing()   # from here the gate has a machine state to use: the one the ROS 2 topic reports (without it the job is refused)
        subscribed = bridge_state_to_mqtt(bridge.node, lambda topic, payload, retain: client.publish(topic, payload, retain=retain), bridge.observe)
        if not subscribed.subscribed:
            raise RuntimeError(f"the ROS 2 state topic cannot be observed: {subscribed.reason}")
        client.loop_forever()
    finally:
        if spinner is not None:
            spinner.stop()
        dispatcher.close(wait=False)
