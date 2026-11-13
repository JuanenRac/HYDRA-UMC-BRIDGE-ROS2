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
import math
import os
import time
from dataclasses import asdict
from typing import Any, Callable

from hydra_umc_sdk.bridge_contract import CellState, JobPhase, MachineState
from hydra_umc_sdk.job_intake import IdempotencyStore, JobIntake
from hydra_umc_sdk.bridge_transport import BridgeHardening, hardening_from_environment

from .mtls import MutualTlsSettings, mqtt_module_for
from .mtls import settings_from_environment as mtls_settings_from_environment
from hydra_umc_sdk.bridge_transport import MessageDispatcher as SharedMessageDispatcher
from hydra_umc_sdk.bridge_transport import MqttPublish, connect_with_retry, open_bridge_client  # noqa: F401

from hydra_umc_sdk.global_stop import GlobalStopGuard, GlobalStopSettings, follow_on_connect
from .coordinator import Ros2Coordinator, Ros2Dispatch
from .extensions import BridgeExtension, ExtensionHost
from .rclpy_transport import Ros2Node, Ros2SafeStopClient, Ros2StateSubscriber, SubscribeResult, spin_node_in_background

_LOG = logging.getLogger(__name__)

TOPIC_PREFIX = "hydra/bridges/ros2/"
AVAILABILITY_TOPIC = f"{TOPIC_PREFIX}availability"
# Nothing legitimate is larger than this (a BridgeJob is a few hundred bytes); anything bigger is refused unparsed.
MAX_PAYLOAD_BYTES = 65536
# The commands that must never wait behind another one.
STOP_VERBS = frozenset({"cmd/safe_stop"})

# Refused while the server announces a global stop (when the bridge follows it): a job asks for work (an abort is let through); the safe stop is never refused.
MOVING_VERBS: frozenset[str] = frozenset()
GLOBAL_STOP_ENVIRONMENT_NAME = "ROS2"

# How old the last state of the machine may be before it stops counting. On by default: a publisher that died after saying IDLE must not be believed for ever, so the
# publisher of the state topic has to repeat it (a heartbeat) at least this often. `None` (or 0 in the environment/configuration) keeps the state for ever, which is only
# right for a publisher that is known to republish on a timer. A deployment on a real ROS 2 graph also needs SROS2 (DDS security permissions) so that no other node of the
# same domain can publish on the state topic: that is a configuration of ROS 2, not something this bridge can check.
DEFAULT_OBSERVED_MAX_AGE_SECONDS = 2.0
OBSERVED_MAX_AGE_ENVIRONMENT = "HYDRA_UMC_ROS2_OBSERVED_MAX_AGE_S"


def observed_max_age_from_environment(env: dict[str, str] | None = None) -> float | None:
    """The age limit of the observed state from `HYDRA_UMC_ROS2_OBSERVED_MAX_AGE_S`: unset gives the default, `0` switches the limit off (None), anything else must be a positive number."""
    source = os.environ if env is None else env
    raw = (source.get(OBSERVED_MAX_AGE_ENVIRONMENT) or "").strip()
    if not raw:
        return DEFAULT_OBSERVED_MAX_AGE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{OBSERVED_MAX_AGE_ENVIRONMENT} must be a number of seconds, 0 to switch the limit off") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{OBSERVED_MAX_AGE_ENVIRONMENT} must be a finite number of seconds, 0 or more")
    return None if value == 0 else value


def _dispatch_result(dispatch: Ros2Dispatch) -> str:
    """The answer to a `cmd/job`: `allowed` is the key every bridge answers with, `accepted` stays for existing readers."""
    answer = asdict(dispatch)
    answer["allowed"] = dispatch.accepted
    return json.dumps(answer)


def _refusal(suffix: str, reason: str) -> list["MqttPublish"]:
    result = {"allowed": False, "accepted": False, "interface": "none", "reason": reason, "mode": "plan-only"}
    return [MqttPublish(f"{TOPIC_PREFIX}{suffix}/result", json.dumps(result))]


def _extension_refusal(suffix: str | None, reason: str) -> list["MqttPublish"]:
    """The answer of a refused message of an extension; a message that is not a command (a feed, a replay) has nothing to answer."""
    if suffix is None:
        return []
    return [MqttPublish(f"{TOPIC_PREFIX}{suffix}/result", json.dumps({"allowed": False, "reason": reason}))]


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
        observed_max_age_seconds: float | None = DEFAULT_OBSERVED_MAX_AGE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        global_stop: GlobalStopGuard | GlobalStopSettings | None = None,
        abort_calls_safe_stop: bool = True,
        safe_stop_client: Ros2SafeStopClient | None = None,
    ) -> None:
        self.node = node
        self._cell_state = cell_state
        self._coordinator = Ros2Coordinator()
        self._safe_stop = safe_stop_client if safe_stop_client is not None else Ros2SafeStopClient(node_is_spinning=node_is_spinning)
        # an ABORT job calls the real safe stop service (stopping is the safe direction); False makes it only name the service, and the stop is then `cmd/safe_stop`
        self.abort_calls_safe_stop = bool(abort_calls_safe_stop)
        # How old the last reported state may be before it stops counting (2 s unless told otherwise, see DEFAULT_OBSERVED_MAX_AGE_SECONDS). None keeps it for ever, which is
        # only right for a publisher that republishes on a timer: a publisher that died after saying IDLE would otherwise be believed IDLE for ever.
        self._observed_max_age = observed_max_age_seconds
        self._clock = clock
        self._observed_at = 0.0
        # docs/BRIDGE_CONTRACT.md: the idempotency key of a job is handled once; a repeat gets the answer that was given
        self._idempotency = idempotency
        # the shared first look at a job: malformed ones are refused and a repeated idempotency key is recognised (the same intake as the other bridges)
        self._intake = JobIntake(idempotency)
        # follows the global stop that the server announces (global_stop.py); off unless HYDRA_UMC_ROS2_GLOBAL_STOP=1 or a guard is given
        if isinstance(global_stop, GlobalStopGuard):
            self.global_stop = global_stop
        elif global_stop is not None:
            self.global_stop = GlobalStopGuard(global_stop)
        else:
            self.global_stop = GlobalStopGuard.from_environment(GLOBAL_STOP_ENVIRONMENT_NAME)
        # The machine state as the ROS 2 state topic reports it. Before `start_observing()` nothing has been read and the gate refuses the job; once observing, a machine whose state
        # was never heard (or is not a known state) counts as OFFLINE, so nothing productive is forwarded on a claim.
        self._observing = False
        self._observed: MachineState | None = None
        # the optional extensions (extensions.py): none unless the configuration names them
        self.extensions = ExtensionHost()

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

        extension = self.extensions.claiming(topic)
        if extension is not None:
            return self._handle_extension(extension, topic, payload, retained)
        if retained or not topic.startswith(TOPIC_PREFIX):
            return []
        suffix = topic[len(TOPIC_PREFIX) :]
        if suffix not in ("cmd/job", "cmd/safe_stop"):
            return []
        if len(payload) > MAX_PAYLOAD_BYTES:
            return _refusal(suffix, f"payload is larger than {MAX_PAYLOAD_BYTES} bytes")
        refused = self.global_stop.refusal(suffix, payload, moving=MOVING_VERBS)
        if refused is not None:
            return _refusal(suffix, refused)
        try:
            if suffix == "cmd/job":
                return [self._handle_job(payload)]
            result = self._safe_stop.call(self.node, self._coordinator.safe_stop_service)
            return [MqttPublish(f"{TOPIC_PREFIX}cmd/safe_stop/result", json.dumps(asdict(result)))]
        except Exception as error:  # noqa: BLE001 - nothing a message does may end the loop
            _LOG.exception("could not handle %s", topic)
            return _refusal(suffix, f"the command could not be handled: {type(error).__name__}")

    def _handle_extension(self, extension: BridgeExtension, topic: str, payload: bytes, retained: bool) -> list[MqttPublish]:
        """A message of a topic that an extension claims. Under the prefix of the bridge a command is refused while the global stop of the server is active when the extension
        names its verb as moving (or as a job, which an abort still passes); every refusal and every failure is an answer on the result topic, never an exception."""
        suffix = topic[len(TOPIC_PREFIX) :] if topic.startswith(TOPIC_PREFIX) else None
        answer_to = suffix if suffix is not None and not retained else None
        if len(payload) > MAX_PAYLOAD_BYTES:
            return _extension_refusal(answer_to, f"payload is larger than {MAX_PAYLOAD_BYTES} bytes")
        if answer_to is not None:
            refused = self.global_stop.refusal(answer_to, payload, moving=self.extensions.moving_verbs(), jobs=self.extensions.job_verbs())
            if refused is not None:
                return _extension_refusal(answer_to, refused)
        try:
            return extension.on_message(topic, payload, retained)
        except Exception as error:  # noqa: BLE001 - nothing a message does may end the loop
            _LOG.exception("could not handle %s", topic)
            return _extension_refusal(answer_to, f"the command could not be handled: {type(error).__name__}")

    def _handle_job(self, payload: bytes) -> MqttPublish:
        result_topic = f"{TOPIC_PREFIX}cmd/job/result"
        try:
            raw = json.loads(payload)
        except (ValueError, UnicodeDecodeError) as error:
            dispatch: Any = {"allowed": False, "accepted": False, "interface": "none", "reason": f"malformed job payload: {error}", "mode": "plan-only"}
            return MqttPublish(result_topic, json.dumps(dispatch))
        intake = self._intake.accept(raw)
        job = intake.job
        if job is None:
            dispatch = {"allowed": False, "accepted": False, "interface": "none", "reason": f"malformed job payload: {intake.reason}", "mode": "plan-only"}
            return MqttPublish(result_topic, json.dumps(dispatch))
        if not intake.accepted:
            # an ABORT is repeated when its key was already handled: asking for a stop twice is harmless and a first attempt that died half way must not leave the machine running
            if not (job.phase is JobPhase.ABORT and self.abort_calls_safe_stop):
                stored = self._idempotency.recall(job.idempotency_key) if self._idempotency is not None else None
                if stored is None:
                    stored = json.dumps({"allowed": False, "accepted": False, "interface": "none", "reason": intake.reason, "mode": "plan-only"})
                return MqttPublish(result_topic, stored)
        dispatch_result = self._coordinator.dispatch(job, self._cell_state(), self._observed_state())
        answer = json.loads(_dispatch_result(dispatch_result))
        if dispatch_result.accepted and job.phase is JobPhase.ABORT and self.abort_calls_safe_stop:
            stop = self._safe_stop.call(self.node, self._coordinator.safe_stop_service)
            answer["safe_stop"] = asdict(stop)
            answer["executed"] = bool(stop.called and stop.success)
            if stop.called:
                answer["mode"] = "send"
            if stop.called and stop.success:
                answer["reason"] = f"abort accepted and the safe stop service was called: {stop.reason}"
            else:
                answer["reason"] = f"abort accepted, but the safe stop service did not confirm: {stop.reason}"
        publish = MqttPublish(result_topic, json.dumps(answer))
        if self._idempotency is not None and intake.accepted:
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

    def __init__(self, bridge: object, stop_verbs: frozenset[str] = STOP_VERBS, **limits: int) -> None:
        super().__init__(bridge, TOPIC_PREFIX, stop_verbs, **limits)  # type: ignore[arg-type]
        guard = getattr(bridge, "global_stop", None)
        self._global_stop = guard if isinstance(guard, GlobalStopGuard) else None
        host = getattr(bridge, "extensions", None)
        self._extensions = host if isinstance(host, ExtensionHost) else None
        self._bridge_handler = bridge

    def on_message(self, client: object, userdata: object, message: object) -> None:
        # the announcement of the global stop is retained: it is the state, not a replay, so it is read before the retained check of the shared dispatcher
        guard = self._global_stop
        topic = getattr(message, "topic", None)
        claimed = self._extensions.claiming(topic) if self._extensions is not None and isinstance(topic, str) else None
        if guard is not None and guard.enabled and topic == guard.topic:
            guard.observe(bytes(message.payload))  # type: ignore[attr-defined]
            if claimed is None:
                return
        if claimed is not None and isinstance(topic, str) and claimed.inline(topic):
            self._run_inline(client, topic, message)
            return
        super().on_message(client, userdata, message)

    def _run_inline(self, client: object, topic: str, message: object) -> None:
        """A message that an extension asked to have handled on the network thread (cheap, never blocking); retained ones included."""
        try:
            for publish in self._bridge_handler.handle_message(topic, bytes(message.payload), retained=bool(getattr(message, "retain", False))):  # type: ignore[attr-defined]
                client.publish(publish.topic, publish.payload, qos=getattr(publish, "qos", 0), retain=publish.retain)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - one bad message never ends the loop
            _LOG.exception("could not handle %s", topic)


def _on_connected(guard: object, extensions: ExtensionHost | None) -> Callable[[object], None] | None:
    """What runs after every accepted (re)connect: follow the global stop and subscribe to the extra filters of the extensions; None when there is nothing to do."""
    follow = follow_on_connect(guard) if isinstance(guard, GlobalStopGuard) else None
    extra = extensions.subscriptions() if extensions is not None else []
    if follow is None and not extra:
        return None

    def on_connected(client: object) -> None:
        if follow is not None:
            follow(client)
        for topic_filter, qos in extra:
            client.subscribe(topic_filter, qos=qos)  # type: ignore[attr-defined]

    return on_connected


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
    hardening: BridgeHardening | None = None,
    mtls: MutualTlsSettings | None = None,
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

    # a client certificate for the server: off unless HYDRA_UMC_ROS2_MTLS=1 (see mtls.py); checked before anything connects
    mqtt_module = mqtt_module_for(mtls if mtls is not None else mtls_settings_from_environment())

    # the limits of the transport are off unless the settings (the file of the service, else its environment) switch them on: see hydra_umc_sdk.bridge_transport.BridgeHardening
    hardening = hardening if hardening is not None else hardening_from_environment(GLOBAL_STOP_ENVIRONMENT_NAME)
    dispatcher = MessageDispatcher(bridge, **hardening.dispatcher_kwargs())  # type: ignore[arg-type]  # the keywords are ints; the stop verbs stay the default
    guard = getattr(bridge, "global_stop", None)  # a bridge object that is not one of ours has none: nothing to follow
    spinner = None
    found = getattr(bridge, "extensions", None)
    extensions = found if isinstance(found, ExtensionHost) else None
    try:
        client = open_bridge_client(
            host,
            port,
            client_id=client_id,
            topic_prefix=TOPIC_PREFIX,
            dispatcher=dispatcher,
            username=username,
            password=password,
            mqtt_module=mqtt_module,
            max_connect_attempts=max_connect_attempts,
            connect_retry_delay_seconds=connect_retry_delay_seconds,
            on_connected=_on_connected(guard, extensions),
            **hardening.connect_kwargs(),
        )
        spinner = spin_node_in_background(bridge.node)
        bridge.start_observing()   # from here the gate has a machine state to use: the one the ROS 2 topic reports (without it the job is refused)
        subscribed = bridge_state_to_mqtt(bridge.node, lambda topic, payload, retain: client.publish(topic, payload, retain=retain), bridge.observe)  # type: ignore[attr-defined]
        if not subscribed.subscribed:
            raise RuntimeError(f"the ROS 2 state topic cannot be observed: {subscribed.reason}")
        if extensions is not None:
            extensions.start(bridge.node, lambda topic, payload, retain: client.publish(topic, payload, retain=retain))  # type: ignore[attr-defined]
        client.loop_forever()  # type: ignore[attr-defined]
    finally:
        if extensions is not None:
            extensions.stop()
        if spinner is not None:
            spinner.stop()
        dispatcher.close(wait=False)
