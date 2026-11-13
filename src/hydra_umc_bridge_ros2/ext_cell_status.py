# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Extension: the state of the HYDRA-UMC cells on ROS 2 (read only)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Publish the state of the cells of HYDRA-UMC on ROS 2 as `hydra_umc_interfaces/msg/CellStatus`, one topic per cell.

HYDRA-UMC-SERVER announces its robots on the broker (retained, `hydra/swarm/robot_<id>/state`, its own presence on `hydra/swarm/status` and the global stop on
`hydra/swarm/safety/estop`). A ROS 2 node that wants to know whether a cell is ready, running or stopped should not have to speak MQTT: this extension reads those three kinds of topic
and publishes `CellStatus` on `<ros_prefix>/robot_<id>/status` (default `/hydra_umc/cells/robot_<id>/status`) with the `transient_local` durability that the state topic of the bridge
already uses, so a node that starts later still sees the last state.

Read only in both directions: MQTT is only subscribed to (nothing is published back, no command is sent to the server) and on the ROS 2 side it creates publishers of one message type and
nothing else (no subscription, service, client or action). A ROS 2 node cannot use this feed to move anything.

How the two states are worked out (the summary of the server holds no fault or job, so none is invented):

* machine: `online` false or missing is `OFFLINE`; online and playing and paused is `HOLDING`; playing is `RUNNING`; otherwise `IDLE`. While the global stop is active an online machine is `SAFE_STOP`.
* cell: a global stop that says `SAFE_STOP` or cannot be read is `SAFE_STOP`; a server that is offline or was never heard, a global stop never heard (both unless the settings say otherwise) and
  an `OFFLINE` machine are `INHIBITED`; otherwise `READY`. `FAULT` is never reported: nothing the server publishes says it.
* a state message that cannot be read makes that cell `OFFLINE` and `INHIBITED`, never the last good state.
* `job_id` stays empty: the server does not know the jobs of HYDRA-UMC.

Off by default (`extensions.cell_status.enabled`, in the file of the service). The broker must let the credential of the bridge subscribe below the swarm prefix.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable

from hydra_umc_sdk.bridge_contract import CellState, MachineState
from hydra_umc_sdk.bridge_transport import MqttPublish
from hydra_umc_sdk.global_stop import STATE_UNKNOWN, STATE_READY, GlobalStopGuard, GlobalStopSettings

from .extensions import BridgeExtension, ExtensionConfigError, Publish, section_from_dict

_LOG = logging.getLogger(__name__)

NAME = "cell_status"
_ROS_TOPIC = re.compile(r"(/[A-Za-z0-9_]+)+")
_MQTT_PREFIX = re.compile(r"[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*")
_ROBOT = re.compile(r"robot_[A-Za-z0-9]{1,32}")


@dataclass(frozen=True)
class CellStatusSettings:
    enabled: bool = False
    # the prefix the server publishes under (its `mqtt.prefix`)
    swarm_prefix: str = "hydra/swarm"
    # where the ROS 2 topics are made: <ros_prefix>/<robot>/status
    ros_prefix: str = "/hydra_umc/cells"
    max_cells: int = 32
    # until the announcement of the global stop has been heard every cell is INHIBITED (a server that does not announce it needs this off)
    estop_required: bool = True
    # until the server has said it is online every cell is INHIBITED
    server_required: bool = True


def parse(section: object) -> CellStatusSettings:
    settings = section_from_dict(section, CellStatusSettings, NAME)
    if not _MQTT_PREFIX.fullmatch(settings.swarm_prefix):
        raise ExtensionConfigError("'extensions.cell_status.swarm_prefix' must be a plain MQTT topic prefix without wildcards, such as hydra/swarm")
    if not _ROS_TOPIC.fullmatch(settings.ros_prefix):
        raise ExtensionConfigError("'extensions.cell_status.ros_prefix' must be a ROS 2 topic name such as /hydra_umc/cells")
    if not 1 <= settings.max_cells <= 256:
        raise ExtensionConfigError("'extensions.cell_status.max_cells' must be between 1 and 256")
    return settings


@dataclass(frozen=True)
class CellRecord:
    cell_state: str
    machine_state: str
    job_id: str = ""


def machine_of(summary: object, stop_active: bool) -> MachineState:
    """The machine state of one robot summary of the server."""
    if not isinstance(summary, dict) or summary.get("online") is not True:
        return MachineState.OFFLINE
    if stop_active:
        return MachineState.SAFE_STOP
    playback = summary.get("playbackState")
    if isinstance(playback, dict):
        playing = playback.get("isPlaying") is True or playback.get("playing") is True
        paused = playback.get("isPaused") is True or playback.get("paused") is True
        if playing and paused:
            return MachineState.HOLDING
        if playing:
            return MachineState.RUNNING
    return MachineState.IDLE


class CellStatusFeed:
    """The inputs of the server and the records derived from them. Pure: it needs neither MQTT nor ROS 2."""

    def __init__(self, settings: CellStatusSettings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._robots: dict[str, object] = {}
        self._server_online: bool | None = None
        self._guard = GlobalStopGuard(GlobalStopSettings(True, f"{settings.swarm_prefix}/safety/estop", settings.estop_required))
        self.dropped_cells = 0

    def topics(self) -> tuple[str, str]:
        return f"{self._settings.swarm_prefix}/status", self._guard.topic

    def robot_of(self, topic: str) -> str | None:
        """The robot a state topic belongs to, or None when the topic is not `<prefix>/robot_<id>/state`."""
        prefix = f"{self._settings.swarm_prefix}/"
        if not topic.startswith(prefix) or not topic.endswith("/state"):
            return None
        robot = topic[len(prefix) : -len("/state")]
        return robot if _ROBOT.fullmatch(robot) else None

    def on_status(self, payload: bytes) -> None:
        text = payload.decode("utf-8", errors="replace").strip().lower()
        with self._lock:
            self._server_online = True if text == "online" else False if text == "offline" else None

    def on_estop(self, payload: bytes) -> None:
        self._guard.observe(payload)

    def on_robot(self, robot: str, payload: bytes) -> None:
        try:
            summary: object = json.loads(payload)
        except (ValueError, UnicodeDecodeError, RecursionError):
            summary = None  # unreadable: that cell is not usable, whatever it said before
        with self._lock:
            if robot not in self._robots and len(self._robots) >= self._settings.max_cells:
                self.dropped_cells += 1
                return
            self._robots[robot] = summary

    def records(self) -> dict[str, CellRecord]:
        """The record of every robot heard, from the inputs as they stand now."""
        guard_state = self._guard.state
        stop_active = guard_state not in (STATE_READY, STATE_UNKNOWN)
        with self._lock:
            robots = dict(self._robots)
            server = self._server_online
        out: dict[str, CellRecord] = {}
        for robot, summary in robots.items():
            machine = machine_of(summary, stop_active)
            if stop_active:
                cell = CellState.SAFE_STOP
            elif (guard_state == STATE_UNKNOWN and self._settings.estop_required) or server is False or (server is None and self._settings.server_required) or machine is MachineState.OFFLINE:
                cell = CellState.INHIBITED
            else:
                cell = CellState.READY
            out[robot] = CellRecord(cell.value, machine.value)
        return out


class Ros2CellStatusSink:
    """The publishers of `CellStatus`, one per cell, made when the cell is first heard. The only place that touches rclpy."""

    def __init__(self, node: Any, ros_prefix: str, message_type: Any, qos: Any) -> None:
        self._node = node
        self._prefix = ros_prefix
        self._type = message_type
        self._qos = qos
        self._publishers: dict[str, Any] = {}

    def publish(self, robot: str, record: CellRecord) -> None:
        publisher = self._publishers.get(robot)
        if publisher is None:
            publisher = self._publishers[robot] = self._node.create_publisher(self._type, f"{self._prefix}/{robot}/status", self._qos)
        publisher.publish(self._type(cell_state=record.cell_state, machine_state=record.machine_state, job_id=record.job_id))

    def close(self) -> None:
        for publisher in self._publishers.values():
            try:
                self._node.destroy_publisher(publisher)
            except Exception:  # noqa: BLE001 - the node may already be gone
                _LOG.debug("a cell publisher was already gone", exc_info=True)
        self._publishers.clear()


def ros_sink(node: Any, settings: CellStatusSettings) -> Ros2CellStatusSink:
    try:
        from hydra_umc_interfaces.msg import CellStatus  # type: ignore[import-untyped]
        from rclpy.qos import DurabilityPolicy, QoSProfile  # type: ignore[import-untyped]
    except ImportError as error:
        raise RuntimeError(f"the cell status extension needs rclpy and the hydra_umc_interfaces package (build interfaces/ with colcon and source it): {error}") from error
    return Ros2CellStatusSink(node, settings.ros_prefix, CellStatus, QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))


class CellStatusExtension(BridgeExtension):
    name = NAME

    def __init__(self, settings: CellStatusSettings, *, sink_factory: Callable[[Any, CellStatusSettings], Any] = ros_sink) -> None:
        self.settings = settings
        self.feed = CellStatusFeed(settings)
        self._sink_factory = sink_factory
        self._sink: Any = None
        self._published: dict[str, CellRecord] = {}
        self._publish_lock = threading.Lock()
        self._status_topic, self._estop_topic = self.feed.topics()

    def subscriptions(self) -> list[tuple[str, int]]:
        prefix = self.settings.swarm_prefix
        return [(self._status_topic, 1), (self._estop_topic, 1), (f"{prefix}/+/state", 1)]

    def claims(self, topic: str) -> bool:
        return topic in (self._status_topic, self._estop_topic) or self.feed.robot_of(topic) is not None

    def inline(self, topic: str) -> bool:
        return True

    def on_message(self, topic: str, payload: bytes, retained: bool) -> list[MqttPublish]:
        # a retained message is the state, not a replay: it is used (a feed is not a command)
        if topic == self._status_topic:
            self.feed.on_status(payload)
        elif topic == self._estop_topic:
            self.feed.on_estop(payload)
        else:
            robot = self.feed.robot_of(topic)
            if robot is None:
                return []
            self.feed.on_robot(robot, payload)  # an empty message (the server clearing a retained topic) is unreadable: that cell turns OFFLINE and INHIBITED
        self.refresh()
        return []

    def refresh(self) -> None:
        """Publish the cells whose record changed since they were last published. Never raises."""
        with self._publish_lock:
            sink = self._sink
            if sink is None:
                return
            for robot, record in self.feed.records().items():
                if self._published.get(robot) == record:
                    continue
                try:
                    sink.publish(robot, record)
                except Exception:  # noqa: BLE001 - tried again at the next change
                    _LOG.exception("could not publish the status of %s", robot)
                    continue
                self._published[robot] = record

    def start(self, node: Any, publish: Publish) -> None:
        self._sink = self._sink_factory(node, self.settings)
        self.refresh()  # the retained messages can arrive before the node is ready

    def stop(self) -> None:
        with self._publish_lock:
            sink, self._sink = self._sink, None
            self._published.clear()
        if sink is not None:
            sink.close()


def build(settings: CellStatusSettings, bridge: Any) -> CellStatusExtension | None:
    return CellStatusExtension(settings) if settings.enabled else None
