# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Extension: named goals for Nav2 and MoveIt, behind the gate and a signed confirmation
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Send a robot of the ROS 2 graph to a NAMED place: a Nav2 waypoint (`cmd/nav_goal`) or a MoveIt joint target (`cmd/move_named`). This extension can move something. Off by default.

What stands between a message on the broker and a goal for the robot, in this order, and any of them refusing ends it with an answer on `<command>/result`:

1. the extension is switched on (`extensions.motion.enabled`) and, at the start, the bridge follows the global stop of the server (`HYDRA_UMC_ROS2_GLOBAL_STOP=1`; `require_global_stop` false
   lifts that and is a decision of the operator); while the server says `SAFE_STOP`, or its announcement cannot be read, a command is refused, an abort still goes through;
2. the command is a `BridgeJob` that carries a signature of the SDK (`signed_commands`: HMAC-SHA256 over the topic and the job, an expiry, a key in a file only the service can read). Unsigned,
   forged, altered, expired or future-dated commands are refused, and so are repeats of an idempotency key. There is no switch that turns the signature off;
3. the gate of the SDK (`evaluate_job`) with the cell state of the bridge and the state of the machine that the bridge OBSERVED on the state topic (never the one written in the job): the cell must be
   READY and the machine IDLE;
4. one motion at a time: while a goal is running the next one is refused;
5. the goal is a NAME that the operator wrote in a file (`nav2_waypoints_file`, `moveit_targets_file`). There are no coordinates and no joint values in the command, so a message cannot ask for a
   place the operator did not list. Nav2 goals use the frame of the settings; MoveIt goals scale velocity and acceleration (0.1 by default);
6. the action server must be up now (nothing is queued for later), and a goal that has not finished after `goal_timeout_s` is cancelled.

An `ABORT` job on either command topic cancels the running goal and, as for `cmd/job`, calls the safe-stop service when `node.abort_calls_safe_stop` is on. The answer says what was done. The extension
never publishes a velocity or a trajectory of its own: Nav2 and MoveIt plan and move.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from hydra_umc_sdk.bridge_contract import BridgeJob, JobPhase, evaluate_job
from hydra_umc_sdk.bridge_transport import MqttPublish
from hydra_umc_sdk.job_intake import IdempotencyStore, JobIntake, read_job_message
from hydra_umc_sdk.signed_commands import CommandSigningError, CommandVerifier, SignedCommandSettings

from .extensions import BridgeExtension, ExtensionConfigError, Publish, section_from_dict

_LOG = logging.getLogger(__name__)

NAME = "motion"
TOPIC_PREFIX = "hydra/bridges/ros2/"
NAV2_VERB = "cmd/nav_goal"
MOVEIT_VERB = "cmd/move_named"
_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_FRAME = re.compile(r"[A-Za-z0-9_/\-]{1,64}")
_ROS_ACTION = re.compile(r"/?([A-Za-z0-9_]+/)*[A-Za-z0-9_]+")
MAX_ENTRIES = 256
MAX_COORDINATE_M = 1000.0
MAX_JOINT_RAD = 12.6
MAX_JOINTS = 16
TERMINAL = frozenset({"succeeded", "failed", "canceled", "rejected", "timeout"})


@dataclass(frozen=True)
class MotionSettings:
    enabled: bool = False
    # the key of the signed commands: a file that only the service can read (at least 32 characters)
    command_key_file: str = ""
    command_max_skew_ms: int = 5000
    command_max_expires_ms: int = 30000
    # the bridge must follow the global stop of the server before it may move anything
    require_global_stop: bool = True
    goal_timeout_s: float = 120.0
    nav2_enabled: bool = False
    nav2_action: str = "navigate_to_pose"
    nav2_frame: str = "map"
    nav2_waypoints_file: str = ""
    moveit_enabled: bool = False
    moveit_action: str = "move_action"
    moveit_targets_file: str = ""
    moveit_velocity_scale: float = 0.1
    moveit_acceleration_scale: float = 0.1
    moveit_planning_time_s: float = 5.0


@dataclass(frozen=True)
class Waypoint:
    x: float
    y: float
    yaw: float


@dataclass(frozen=True)
class JointTarget:
    group: str
    joints: tuple[tuple[str, float], ...]


def _finite(value: object, limit: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > limit:
        raise ExtensionConfigError(f"{label} must be a number between -{limit:g} and {limit:g}")
    return float(value)


def _read_json(path: str, key: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ExtensionConfigError(f"cannot read {path}: {error}") from error
    entries = raw.get(key) if isinstance(raw, dict) else None
    if not isinstance(entries, dict) or not entries:
        raise ExtensionConfigError(f"{path} must be a JSON object with a non-empty '{key}' object")
    if len(entries) > MAX_ENTRIES:
        raise ExtensionConfigError(f"{path} lists more than {MAX_ENTRIES} entries")
    return entries


def load_waypoints(path: str) -> dict[str, Waypoint]:
    """`{"waypoints": {"dock_a": {"x": 1.0, "y": 2.0, "yaw": 0.0}}}`: metres in the frame of the map, yaw in radians. Anything else in an entry is an error."""
    out: dict[str, Waypoint] = {}
    for name, entry in _read_json(path, "waypoints").items():
        if not _NAME.fullmatch(name) or not isinstance(entry, dict) or set(entry) != {"x", "y", "yaw"}:
            raise ExtensionConfigError(f"waypoint '{name}' of {path} must be named with letters, digits, _ . - and hold exactly x, y and yaw")
        out[name] = Waypoint(
            _finite(entry["x"], MAX_COORDINATE_M, f"x of '{name}'"),
            _finite(entry["y"], MAX_COORDINATE_M, f"y of '{name}'"),
            _finite(entry["yaw"], 2 * math.pi, f"yaw of '{name}'"),
        )
    return out


def load_targets(path: str) -> dict[str, JointTarget]:
    """`{"targets": {"home": {"group": "arm", "joints": {"joint1": 0.0}}}}`: joint values in radians (or metres for a prismatic joint)."""
    out: dict[str, JointTarget] = {}
    for name, entry in _read_json(path, "targets").items():
        if not _NAME.fullmatch(name) or not isinstance(entry, dict) or set(entry) != {"group", "joints"}:
            raise ExtensionConfigError(f"target '{name}' of {path} must be named with letters, digits, _ . - and hold exactly group and joints")
        group, joints = entry["group"], entry["joints"]
        if not isinstance(group, str) or not _NAME.fullmatch(group) or not isinstance(joints, dict) or not 1 <= len(joints) <= MAX_JOINTS:
            raise ExtensionConfigError(f"target '{name}' of {path} needs a group name and between 1 and {MAX_JOINTS} joints")
        if any(not isinstance(joint, str) or not _NAME.fullmatch(joint) for joint in joints):
            raise ExtensionConfigError(f"a joint name of target '{name}' of {path} is not valid")
        out[name] = JointTarget(group, tuple((joint, _finite(value, MAX_JOINT_RAD, f"joint {joint} of '{name}'")) for joint, value in joints.items()))
    return out


def parse(section: object) -> MotionSettings:
    settings = section_from_dict(section, MotionSettings, NAME)
    if not settings.enabled:
        return settings
    if not settings.command_key_file:
        raise ExtensionConfigError("'extensions.motion.command_key_file' is needed: a motion command must be signed")
    if not (settings.nav2_enabled or settings.moveit_enabled):
        raise ExtensionConfigError("'extensions.motion' is enabled but neither nav2_enabled nor moveit_enabled is")
    for key in ("command_max_skew_ms", "command_max_expires_ms"):
        if getattr(settings, key) < (0 if key.endswith("skew_ms") else 1):
            raise ExtensionConfigError(f"'extensions.motion.{key}' must be a whole number of milliseconds")
    for key, top in (("goal_timeout_s", 3600.0), ("moveit_planning_time_s", 60.0)):
        value = getattr(settings, key)
        if not math.isfinite(value) or not 0 < value <= top:
            raise ExtensionConfigError(f"'extensions.motion.{key}' must be a number of seconds above 0 and at most {top:g}")
    for key in ("moveit_velocity_scale", "moveit_acceleration_scale"):
        value = getattr(settings, key)
        if not math.isfinite(value) or not 0 < value <= 1:
            raise ExtensionConfigError(f"'extensions.motion.{key}' must be above 0 and at most 1")
    for key in ("nav2_action", "moveit_action"):
        if not _ROS_ACTION.fullmatch(getattr(settings, key)):
            raise ExtensionConfigError(f"'extensions.motion.{key}' must be a ROS 2 action name")
    if not _FRAME.fullmatch(settings.nav2_frame):
        raise ExtensionConfigError("'extensions.motion.nav2_frame' must be a frame name")
    if settings.nav2_enabled:
        if not settings.nav2_waypoints_file:
            raise ExtensionConfigError("'extensions.motion.nav2_waypoints_file' is needed when nav2_enabled is on")
        load_waypoints(settings.nav2_waypoints_file)
    if settings.moveit_enabled:
        if not settings.moveit_targets_file:
            raise ExtensionConfigError("'extensions.motion.moveit_targets_file' is needed when moveit_enabled is on")
        load_targets(settings.moveit_targets_file)
    return settings


StatusCallback = Callable[[str, str], None]


class MotionAdapter(Protocol):
    """What the gateway needs of a robot interface: whether its action server is up now, a goal, and a cancel."""

    def ready(self) -> bool: ...

    def send(self, spec: Any, on_status: StatusCallback) -> None: ...

    def cancel(self) -> bool: ...

    def close(self) -> None: ...


@dataclass
class _Active:
    token: int
    verb: str
    job_id: str
    target: str


class MotionGateway:
    """The decision about one command and the one motion that may run. Pure: it needs no ROS 2 (the adapters are given)."""

    def __init__(
        self,
        intake: JobIntake,
        store: IdempotencyStore | None,
        cell_state: Callable[[], Any],
        observed_state: Callable[[], Any],
        routes: dict[str, tuple[MotionAdapter, dict[str, Any]]],
        *,
        goal_timeout_s: float,
        safe_stop: Callable[[], dict[str, Any]] | None = None,
        publish: Callable[[], Publish | None] = lambda: None,
        timer_factory: Callable[[float, Callable[[], None]], Any] = lambda delay, function: threading.Timer(delay, function),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._intake = intake
        self._store = store
        self._cell_state = cell_state
        self._observed_state = observed_state
        self._routes = routes
        self._timeout = goal_timeout_s
        self._safe_stop = safe_stop
        self._publish = publish
        self._timer_factory = timer_factory
        self._clock = clock
        self._lock = threading.RLock()
        self._active: _Active | None = None
        self._timer: Any = None
        self._counter = 0

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._active is not None

    @staticmethod
    def _answer(verb: str, body: dict[str, object]) -> list[MqttPublish]:
        return [MqttPublish(f"{TOPIC_PREFIX}{verb}/result", json.dumps(body))]

    def handle(self, verb: str, topic: str, payload: bytes, retained: bool = False) -> list[MqttPublish]:
        message = read_job_message(self._intake, topic, payload, retained=retained)
        if message.ignore:
            return []
        if message.refusal is not None:
            return self._answer(verb, dict(message.refusal, goal_status="refused"))
        if message.repeat_answer is not None:
            return [MqttPublish(f"{TOPIC_PREFIX}{verb}/result", message.repeat_answer)]
        job = message.job
        assert job is not None
        body = self._abort(job) if job.phase is JobPhase.ABORT else self._start(verb, job)
        if self._store is not None:
            self._store.remember(job.idempotency_key, json.dumps(body))
        return self._answer(verb, body)

    def _refuse(self, job: BridgeJob, reason: str) -> dict[str, object]:
        return {"allowed": False, "reason": reason, "job_id": job.job_id, "goal_status": "refused"}

    def _start(self, verb: str, job: BridgeJob) -> dict[str, object]:
        decision = evaluate_job(job, self._cell_state(), self._observed_state())
        if not decision.allowed:
            return self._refuse(job, decision.reason)
        adapter, specs = self._routes[verb]
        target = job.parameters.get("target", "")
        spec = specs.get(target)
        if spec is None:
            return self._refuse(job, "the target is not in the list of the configuration")
        with self._lock:
            if self._active is not None:
                return self._refuse(job, f"a motion is already running (job {self._active.job_id})")
            if not adapter.ready():
                return self._refuse(job, "the action server is not available now")
            self._counter += 1
            token = self._counter
            self._active = _Active(token, verb, job.job_id, target)
            try:
                adapter.send(spec, lambda status, detail: self._on_status(token, status, detail))
            except Exception as error:  # noqa: BLE001 - a goal that could not be sent is a refusal, not a crash
                _LOG.exception("could not send the goal of %s", job.job_id)
                self._active = None
                return self._refuse(job, f"the goal could not be sent: {type(error).__name__}")
            if self._active is not None and self._active.token == token:  # a status that arrived while sending may have ended it already
                self._arm_timeout(token)
        return {"allowed": True, "reason": "goal sent", "mode": "send", "job_id": job.job_id, "target": target, "goal_status": "sent"}

    def _arm_timeout(self, token: int) -> None:
        self._disarm()
        timer = self._timer_factory(self._timeout, lambda: self._timed_out(token))
        try:
            timer.daemon = True
        except AttributeError:
            pass
        self._timer = timer
        timer.start()

    def _disarm(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()

    def _timed_out(self, token: int) -> None:
        with self._lock:
            active = self._active
            if active is None or active.token != token:
                return
            adapter = self._routes[active.verb][0]
        try:
            adapter.cancel()
        except Exception:  # noqa: BLE001 - the timeout is reported whatever the cancel did
            _LOG.exception("could not cancel the goal of %s", active.job_id)
        self._on_status(token, "timeout", f"no result after {self._timeout:g} s; the goal was cancelled")

    def _on_status(self, token: int, status: str, detail: str) -> None:
        with self._lock:
            active = self._active
            if active is None or active.token != token:
                return  # a late answer of a goal that is over
            if status in TERMINAL:
                self._active = None
                self._disarm()
        publish = self._publish()
        if publish is not None:
            body = {"allowed": True, "job_id": active.job_id, "target": active.target, "goal_status": status, "detail": detail}
            try:
                publish(f"{TOPIC_PREFIX}{active.verb}/result", json.dumps(body), False)
            except Exception:  # noqa: BLE001 - a lost status message must not break the adapter callback
                _LOG.exception("could not publish the status of %s", active.job_id)

    def _abort(self, job: BridgeJob) -> dict[str, object]:
        with self._lock:
            active = self._active
        cancelled = False
        if active is not None:
            try:
                cancelled = bool(self._routes[active.verb][0].cancel())
            except Exception:  # noqa: BLE001 - the safe stop below is still asked for
                _LOG.exception("could not cancel the goal of %s", active.job_id)
        body: dict[str, object] = {"allowed": True, "job_id": job.job_id, "goal_status": "abort", "cancelled": cancelled, "running": active.job_id if active is not None else None}
        if self._safe_stop is not None:
            stop = self._safe_stop()
            body["safe_stop"] = stop
            body["reason"] = "abort accepted; the running goal was cancelled and the safe stop service " + ("confirmed" if stop.get("success") else "did not confirm")
        else:
            body["reason"] = "abort accepted; the running goal was cancelled" if cancelled else "abort accepted; no goal was running"
        return body

    def close(self) -> None:
        with self._lock:
            active, self._active = self._active, None
            self._disarm()
        if active is not None:
            try:
                self._routes[active.verb][0].cancel()
            except Exception:  # noqa: BLE001 - closing must finish
                _LOG.exception("could not cancel the goal of %s", active.job_id)
        for adapter, _specs in self._routes.values():
            try:
                adapter.close()
            except Exception:  # noqa: BLE001 - closing must finish
                _LOG.exception("could not close an action client")


class _ActionAdapter:
    """The goal flow every action client shares: send, learn whether it was accepted, wait for the result, cancel. Subclasses build the goal and read the result."""

    def __init__(self, node: Any, client: Any) -> None:
        self._node = node
        self._client = client
        self._handle: Any = None
        self._cancel_when_accepted = False
        self._lock = threading.Lock()

    def ready(self) -> bool:
        return bool(self._client.server_is_ready())

    def _build_goal(self, spec: Any) -> Any:
        raise NotImplementedError

    def _outcome(self, wrapper: Any) -> tuple[str, str]:
        raise NotImplementedError

    def send(self, spec: Any, on_status: StatusCallback) -> None:
        goal = self._build_goal(spec)
        with self._lock:
            self._handle = None
            self._cancel_when_accepted = False
        self._client.send_goal_async(goal).add_done_callback(lambda future: self._accepted(future, on_status))

    def _accepted(self, future: Any, on_status: StatusCallback) -> None:
        try:
            handle = future.result()
        except Exception as error:  # noqa: BLE001 - the answer is a status, not an exception on the executor thread
            on_status("failed", f"the goal could not be sent: {error}")
            return
        if not handle.accepted:
            on_status("rejected", "the action server rejected the goal")
            return
        with self._lock:
            self._handle = handle
            cancel_now = self._cancel_when_accepted
        on_status("accepted", "the action server accepted the goal")
        if cancel_now:
            handle.cancel_goal_async()
        handle.get_result_async().add_done_callback(lambda result: self._finished(result, on_status))

    def _finished(self, future: Any, on_status: StatusCallback) -> None:
        try:
            status, detail = self._outcome(future.result())
        except Exception as error:  # noqa: BLE001 - see _accepted
            status, detail = "failed", f"the result could not be read: {error}"
        with self._lock:
            self._handle = None
        on_status(status, detail)

    def cancel(self) -> bool:
        with self._lock:
            handle = self._handle
            if handle is None:
                self._cancel_when_accepted = True  # a goal that is still waiting for its answer is cancelled as soon as it has one
                return True
        handle.cancel_goal_async()
        return True

    def close(self) -> None:
        try:
            self._client.destroy()
        except Exception:  # noqa: BLE001 - the node may already be gone
            _LOG.debug("an action client was already gone", exc_info=True)


class Nav2Adapter(_ActionAdapter):
    """`nav2_msgs/action/NavigateToPose` towards a named waypoint."""

    def __init__(self, node: Any, action: str, frame: str, *, action_client: Any = None, messages: Any = None) -> None:
        if messages is None:
            messages = self._import_messages()
        self._navigate, self._status = messages
        client_class = action_client if action_client is not None else _action_client_class()
        super().__init__(node, client_class(node, self._navigate, action))
        self._frame = frame

    @staticmethod
    def _import_messages() -> tuple[Any, Any]:
        try:
            from action_msgs.msg import GoalStatus  # type: ignore[import-untyped]
            from nav2_msgs.action import NavigateToPose  # type: ignore[import-untyped]
        except ImportError as error:
            raise RuntimeError(f"nav2_msgs is not installed - the Nav2 adapter cannot start: {error}") from error
        return NavigateToPose, GoalStatus

    def _build_goal(self, spec: Waypoint) -> Any:
        goal = self._navigate.Goal()
        goal.pose.header.frame_id = self._frame
        goal.pose.header.stamp = self._node.get_clock().now().to_msg()
        goal.pose.pose.position.x = spec.x
        goal.pose.pose.position.y = spec.y
        goal.pose.pose.orientation.z = math.sin(spec.yaw / 2.0)
        goal.pose.pose.orientation.w = math.cos(spec.yaw / 2.0)
        return goal

    def _outcome(self, wrapper: Any) -> tuple[str, str]:
        return _status_name(self._status, wrapper.status)


class MoveItAdapter(_ActionAdapter):
    """`moveit_msgs/action/MoveGroup` towards a named joint target (plan and execute, scaled velocity and acceleration)."""

    def __init__(self, node: Any, action: str, settings: MotionSettings, *, action_client: Any = None, messages: Any = None) -> None:
        if messages is None:
            messages = self._import_messages()
        self._move_group, self._constraints, self._joint_constraint, self._status = messages
        client_class = action_client if action_client is not None else _action_client_class()
        super().__init__(node, client_class(node, self._move_group, action))
        self._settings = settings

    @staticmethod
    def _import_messages() -> tuple[Any, Any, Any, Any]:
        try:
            from action_msgs.msg import GoalStatus  # type: ignore[import-untyped]
            from moveit_msgs.action import MoveGroup  # type: ignore[import-untyped]
            from moveit_msgs.msg import Constraints, JointConstraint  # type: ignore[import-untyped]
        except ImportError as error:
            raise RuntimeError(f"moveit_msgs is not installed - the MoveIt adapter cannot start: {error}") from error
        return MoveGroup, Constraints, JointConstraint, GoalStatus

    def _build_goal(self, spec: JointTarget) -> Any:
        goal = self._move_group.Goal()
        request = goal.request
        request.group_name = spec.group
        request.num_planning_attempts = 1
        request.allowed_planning_time = self._settings.moveit_planning_time_s
        request.max_velocity_scaling_factor = self._settings.moveit_velocity_scale
        request.max_acceleration_scaling_factor = self._settings.moveit_acceleration_scale
        constraints = self._constraints()
        for joint, position in spec.joints:
            constraint = self._joint_constraint()
            constraint.joint_name = joint
            constraint.position = position
            constraint.tolerance_above = 0.001
            constraint.tolerance_below = 0.001
            constraint.weight = 1.0
            constraints.joint_constraints.append(constraint)
        request.goal_constraints.append(constraints)
        goal.planning_options.plan_only = False
        goal.planning_options.planning_scene_diff.is_diff = True
        goal.planning_options.planning_scene_diff.robot_state.is_diff = True
        return goal

    def _outcome(self, wrapper: Any) -> tuple[str, str]:
        status, detail = _status_name(self._status, wrapper.status)
        if status != "succeeded":
            return status, detail
        code = wrapper.result.error_code.val
        return ("succeeded", "planned and executed") if code == 1 else ("failed", f"MoveIt error code {code}")


def _status_name(goal_status: Any, code: int) -> tuple[str, str]:
    if code == goal_status.STATUS_SUCCEEDED:
        return "succeeded", "the goal was reached"
    if code == goal_status.STATUS_CANCELED:
        return "canceled", "the goal was cancelled"
    return "failed", f"the action server ended the goal with status {code}"


def _action_client_class() -> Any:
    try:
        from rclpy.action import ActionClient  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(f"rclpy is not installed - the motion extension cannot start: {error}") from error
    return ActionClient


class MotionExtension(BridgeExtension):
    name = NAME

    def __init__(self, settings: MotionSettings, bridge: Any, verifier: CommandVerifier, *, adapter_factories: dict[str, Callable[[Any], MotionAdapter]] | None = None) -> None:
        self.settings = settings
        self._bridge = bridge
        self._verifier = verifier
        self._factories = adapter_factories or self._default_factories()
        self.verbs: dict[str, dict[str, Any]] = {}
        if settings.nav2_enabled:
            self.verbs[NAV2_VERB] = load_waypoints(settings.nav2_waypoints_file)
        if settings.moveit_enabled:
            self.verbs[MOVEIT_VERB] = load_targets(settings.moveit_targets_file)
        self.job_verbs = frozenset(self.verbs)
        self._topics = {f"{TOPIC_PREFIX}{verb}": verb for verb in self.verbs}
        self._publish: Publish | None = None
        self.gateway: MotionGateway | None = None
        self._own_store: IdempotencyStore | None = None

    def _default_factories(self) -> dict[str, Callable[[Any], MotionAdapter]]:
        settings = self.settings
        return {
            NAV2_VERB: lambda node: Nav2Adapter(node, settings.nav2_action, settings.nav2_frame),
            MOVEIT_VERB: lambda node: MoveItAdapter(node, settings.moveit_action, settings),
        }

    def claims(self, topic: str) -> bool:
        return topic in self._topics

    def on_message(self, topic: str, payload: bytes, retained: bool) -> list[MqttPublish]:
        gateway = self.gateway
        if gateway is None:
            return [MqttPublish(f"{topic}/result", json.dumps({"allowed": False, "reason": "the motion extension has not started", "goal_status": "refused"}))] if not retained else []
        return gateway.handle(self._topics[topic], topic, payload, retained)

    def _safe_stop(self) -> dict[str, Any]:
        bridge = self._bridge
        if not getattr(bridge, "abort_calls_safe_stop", False):
            return {"called": False, "success": False, "reason": "node.abort_calls_safe_stop is off: the stop is the cmd/safe_stop command"}
        result = bridge._safe_stop.call(bridge.node, bridge._coordinator.safe_stop_service)
        return {"called": result.called, "success": result.success, "reason": result.reason}

    def start(self, node: Any, publish: Publish) -> None:
        self._publish = publish
        bridge = self._bridge
        routes = {verb: (self._factories[verb](node), specs) for verb, specs in self.verbs.items()}
        store = getattr(bridge, "_idempotency", None)
        if store is None:
            store = self._own_store = IdempotencyStore()
        self.gateway = MotionGateway(
            JobIntake(store, self._verifier),
            store,
            bridge._cell_state,
            bridge._observed_state,
            routes,
            goal_timeout_s=self.settings.goal_timeout_s,
            safe_stop=self._safe_stop,
            publish=lambda: self._publish,
        )

    def stop(self) -> None:
        gateway, self.gateway = self.gateway, None
        self._publish = None
        if gateway is not None:
            gateway.close()
        if self._own_store is not None:
            self._own_store.close()
            self._own_store = None


def build(settings: MotionSettings, bridge: Any) -> MotionExtension | None:
    if not settings.enabled:
        return None
    guard = getattr(bridge, "global_stop", None)
    if settings.require_global_stop and not getattr(guard, "enabled", False):
        raise ExtensionConfigError("the motion extension needs the bridge to follow the global stop of the server: set HYDRA_UMC_ROS2_GLOBAL_STOP=1 (or 'require_global_stop' to false if the installation has another way)")
    try:
        verifier = CommandVerifier(
            SignedCommandSettings(True, settings.command_key_file, settings.command_max_skew_ms, settings.command_max_expires_ms),
        )
    except CommandSigningError as error:
        raise ExtensionConfigError(f"the command key cannot be used: {error}") from error
    return MotionExtension(settings, bridge, verifier)
