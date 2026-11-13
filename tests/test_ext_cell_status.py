# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Tests of the cell status extension (read only)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The state of the cells of HYDRA-UMC towards ROS 2, without ROS 2: how the two states are worked out, what fails closed, the limits, the proof that it publishes nothing back."""

import json
import random
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from hydra_umc_sdk.bridge_contract import CellState, MachineState

from hydra_umc_bridge_ros2 import Ros2MqttBridge, ext_cell_status
from hydra_umc_bridge_ros2.extensions import ExtensionConfigError, build_extensions, parse_sections
from hydra_umc_bridge_ros2.ext_cell_status import (
    CellRecord,
    CellStatusExtension,
    CellStatusSettings,
    Ros2CellStatusSink,
    machine_of,
    parse,
)

P = "hydra/swarm"
ONLINE = b"online"
READY = json.dumps({"state": "READY"}).encode()
STOPPED = json.dumps({"state": "SAFE_STOP"}).encode()


def robot(online=True, playing=False, paused=False) -> bytes:
    return json.dumps({"id": 1, "online": online, "playbackState": {"isPlaying": playing, "isPaused": paused}}).encode()


class Sink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, CellRecord]] = []
        self.closed = False
        self.fail = False

    def publish(self, name, record) -> None:
        if self.fail:
            raise RuntimeError("context is down")
        self.calls.append((name, record))

    def close(self) -> None:
        self.closed = True


def make(**keywords):
    sink = Sink()
    extension = CellStatusExtension(CellStatusSettings(enabled=True, **keywords), sink_factory=lambda node, settings: sink)
    extension.start(object(), lambda *a: None)
    return extension, sink


def feed_all(extension, state=b"online", estop=READY, robot_payload=None):
    extension.on_message(f"{P}/status", state, True)
    extension.on_message(f"{P}/safety/estop", estop, True)
    extension.on_message(f"{P}/robot_1/state", robot_payload if robot_payload is not None else robot(), True)


class SettingsTests(unittest.TestCase):
    def test_off_by_default_and_the_defaults_fail_closed(self):
        settings = parse(None)
        self.assertEqual((settings.enabled, settings.estop_required, settings.server_required), (False, True, True))

    def test_wrong_values_are_errors(self):
        for raw in ({"swarm_prefix": "hydra/#"}, {"swarm_prefix": "/hydra"}, {"swarm_prefix": ""}, {"ros_prefix": "cells"}, {"ros_prefix": "/a b"}, {"max_cells": 0}, {"max_cells": 1000}):
            with self.subTest(raw=raw), self.assertRaises(ExtensionConfigError):
                parse(raw)

    def test_it_is_built_only_when_enabled(self):
        bridge = Ros2MqttBridge(object(), lambda: CellState.READY)  # type: ignore[arg-type]
        build_extensions(parse_sections({"cell_status": {}}), bridge)
        self.assertEqual(len(bridge.extensions), 0)
        build_extensions(parse_sections({"cell_status": {"enabled": True, "ros_prefix": "/plant/cells"}}), bridge)
        self.assertEqual(bridge.extensions.names(), ["cell_status"])


class MachineTests(unittest.TestCase):
    def test_the_machine_state_of_a_summary(self):
        cases = (
            ({"online": True, "playbackState": {"isPlaying": True, "isPaused": True}}, False, MachineState.HOLDING),
            ({"online": True, "playbackState": {"playing": True, "paused": True}}, False, MachineState.HOLDING),
            ({"online": True, "playbackState": {"isPlaying": True}}, False, MachineState.RUNNING),
            ({"online": True, "playbackState": {"isPlaying": False, "isPaused": True}}, False, MachineState.IDLE),
            ({"online": True, "playbackState": "weird"}, False, MachineState.IDLE),
            ({"online": True}, False, MachineState.IDLE),
            ({"online": True, "playbackState": {"isPlaying": True}}, True, MachineState.SAFE_STOP),
            ({"online": False}, False, MachineState.OFFLINE),
            ({"online": False}, True, MachineState.OFFLINE),
            ({"online": "yes"}, False, MachineState.OFFLINE),
            ({}, False, MachineState.OFFLINE),
            (None, False, MachineState.OFFLINE),
            ([], False, MachineState.OFFLINE),
        )
        for summary, stop, expected in cases:
            with self.subTest(summary=summary, stop=stop):
                self.assertIs(machine_of(summary, stop), expected)


class DerivationTests(unittest.TestCase):
    def test_everything_known_and_clear_is_ready_and_idle(self):
        extension, sink = make()
        feed_all(extension)
        self.assertEqual(sink.calls[-1], ("robot_1", CellRecord("READY", "IDLE")))

    def test_running_and_paused(self):
        extension, sink = make()
        feed_all(extension, robot_payload=robot(playing=True))
        self.assertEqual(sink.calls[-1][1], CellRecord("READY", "RUNNING"))
        extension.on_message(f"{P}/robot_1/state", robot(playing=True, paused=True), True)
        self.assertEqual(sink.calls[-1][1], CellRecord("READY", "HOLDING"))

    def test_nothing_is_ready_until_the_server_and_the_global_stop_have_been_heard(self):
        extension, sink = make()
        extension.on_message(f"{P}/robot_1/state", robot(), True)
        self.assertEqual(sink.calls[-1][1], CellRecord("INHIBITED", "IDLE"))
        extension.on_message(f"{P}/status", ONLINE, True)
        self.assertEqual(sink.calls[-1][1].cell_state, "INHIBITED")  # the stop is still unknown
        extension.on_message(f"{P}/safety/estop", READY, True)
        self.assertEqual(sink.calls[-1][1], CellRecord("READY", "IDLE"))

    def test_a_server_that_does_not_announce_the_stop_can_be_allowed(self):
        extension, sink = make(estop_required=False, server_required=False)
        extension.on_message(f"{P}/robot_1/state", robot(), True)
        self.assertEqual(sink.calls[-1][1], CellRecord("READY", "IDLE"))
        extension.on_message(f"{P}/status", b"offline", True)
        self.assertEqual(sink.calls[-1][1].cell_state, "INHIBITED")  # but a server that says it is offline still inhibits

    def test_the_global_stop_makes_every_online_cell_safe_stop_and_ready_brings_it_back(self):
        extension, sink = make()
        feed_all(extension, robot_payload=robot(playing=True))
        extension.on_message(f"{P}/robot_2/state", robot(online=False), True)
        extension.on_message(f"{P}/safety/estop", STOPPED, True)
        latest = {name: record for name, record in sink.calls}
        self.assertEqual(latest["robot_1"], CellRecord("SAFE_STOP", "SAFE_STOP"))
        self.assertEqual(latest["robot_2"], CellRecord("SAFE_STOP", "OFFLINE"))
        extension.on_message(f"{P}/safety/estop", READY, True)
        latest = {name: record for name, record in sink.calls}
        self.assertEqual(latest["robot_1"], CellRecord("READY", "RUNNING"))
        self.assertEqual(latest["robot_2"], CellRecord("INHIBITED", "OFFLINE"))

    def test_an_unreadable_stop_announcement_is_a_stop(self):
        extension, sink = make()
        feed_all(extension, estop=b"{not json")
        self.assertEqual(sink.calls[-1][1].cell_state, "SAFE_STOP")

    def test_a_server_that_goes_offline_inhibits_and_an_unreadable_status_is_not_online(self):
        extension, sink = make()
        feed_all(extension)
        extension.on_message(f"{P}/status", b"offline", False)
        self.assertEqual(sink.calls[-1][1].cell_state, "INHIBITED")
        extension.on_message(f"{P}/status", b"\xff\xfe", False)
        self.assertEqual(sink.calls[-1][1].cell_state, "INHIBITED")
        extension.on_message(f"{P}/status", b"ONLINE ", False)
        self.assertEqual(sink.calls[-1][1].cell_state, "READY")

    def test_an_unreadable_robot_message_or_one_cleared_by_the_server_makes_the_cell_offline_not_last_good(self):
        extension, sink = make()
        feed_all(extension)
        for bad in (b"", b"{broken", b"[1]", b"null", b"\xff", b"[" * 100000):
            with self.subTest(bad=bad[:10]):
                extension.on_message(f"{P}/robot_1/state", robot(), True)
                extension.on_message(f"{P}/robot_1/state", bad, True)
                self.assertEqual(sink.calls[-1][1], CellRecord("INHIBITED", "OFFLINE"))

    def test_an_unchanged_record_is_not_published_again(self):
        extension, sink = make()
        feed_all(extension)
        count = len(sink.calls)
        extension.on_message(f"{P}/robot_1/state", robot(), False)
        extension.on_message(f"{P}/status", ONLINE, False)
        self.assertEqual(len(sink.calls), count)

    def test_the_number_of_cells_is_bounded(self):
        extension, sink = make(max_cells=3)
        extension.on_message(f"{P}/status", ONLINE, True)
        extension.on_message(f"{P}/safety/estop", READY, True)
        for index in range(10):
            extension.on_message(f"{P}/robot_{index}/state", robot(), True)
        self.assertEqual(sorted({name for name, _ in sink.calls}), ["robot_0", "robot_1", "robot_2"])
        self.assertEqual(extension.feed.dropped_cells, 7)
        extension.on_message(f"{P}/robot_1/state", robot(playing=True), True)  # a cell that is kept still updates
        self.assertEqual(sink.calls[-1], ("robot_1", CellRecord("READY", "RUNNING")))

    def test_messages_before_the_node_is_ready_are_published_at_the_start(self):
        sink = Sink()
        extension = CellStatusExtension(CellStatusSettings(enabled=True), sink_factory=lambda node, settings: sink)
        feed_all(extension)
        self.assertEqual(sink.calls, [])
        extension.start(object(), lambda *a: None)
        self.assertEqual(sink.calls, [("robot_1", CellRecord("READY", "IDLE"))])

    def test_a_sink_that_fails_is_logged_and_the_record_is_tried_again(self):
        extension, sink = make()
        sink.fail = True
        with self.assertLogs(ext_cell_status.__name__, "ERROR"):
            feed_all(extension)
        sink.fail = False
        extension.refresh()
        self.assertEqual(sink.calls[-1], ("robot_1", CellRecord("READY", "IDLE")))

    def test_stop_closes_the_sink_and_later_messages_publish_nothing(self):
        extension, sink = make()
        feed_all(extension)
        extension.stop()
        self.assertTrue(sink.closed)
        count = len(sink.calls)
        extension.on_message(f"{P}/robot_1/state", robot(playing=True), True)
        self.assertEqual(len(sink.calls), count)
        extension.stop()
        CellStatusExtension(CellStatusSettings(enabled=True)).stop()


class TopicTests(unittest.TestCase):
    def test_it_claims_only_the_swarm_topics_of_the_server(self):
        extension, _ = make(swarm_prefix="plant/swarm")
        for topic in ("plant/swarm/status", "plant/swarm/safety/estop", "plant/swarm/robot_7/state", "plant/swarm/robot_abc/state"):
            self.assertTrue(extension.claims(topic), topic)
            self.assertTrue(extension.inline(topic))
        for topic in (
            "hydra/swarm/status",
            "plant/swarm/robot_7/joints",
            "plant/swarm/robot_/state",
            "plant/swarm/robot_1/cmd/state",
            "plant/swarm/robot_7_8/state",
            "plant/swarm/robot_" + "9" * 40 + "/state",
            "plant/swarm/safety/state",
            "hydra/bridges/ros2/cmd/job",
        ):
            self.assertFalse(extension.claims(topic), topic)
        self.assertEqual(extension.subscriptions(), [("plant/swarm/status", 1), ("plant/swarm/safety/estop", 1), ("plant/swarm/+/state", 1)])

    def test_an_unclaimed_robot_topic_is_ignored(self):
        extension, sink = make()
        self.assertEqual(extension.on_message(f"{P}/robot_1/joints", b"{}", True), [])
        self.assertEqual(sink.calls, [])


class ReadOnlyTests(unittest.TestCase):
    def test_it_publishes_nothing_on_mqtt_and_declares_no_command_verb(self):
        published: list[tuple] = []
        extension = CellStatusExtension(CellStatusSettings(enabled=True), sink_factory=lambda n, s: Sink())
        extension.start(object(), lambda *a: published.append(a))
        for topic, payload in ((f"{P}/status", ONLINE), (f"{P}/safety/estop", STOPPED), (f"{P}/robot_1/state", robot())):
            self.assertEqual(extension.on_message(topic, payload, True), [])
        self.assertEqual(published, [])
        self.assertEqual((extension.moving_verbs, extension.job_verbs), (frozenset(), frozenset()))

    def test_a_seeded_random_stream_always_gives_known_values_and_never_ready_during_a_stop(self):
        rng = random.Random(20260)
        extension, sink = make(max_cells=8)
        stop = False
        for _ in range(500):
            kind = rng.randrange(4)
            if kind == 0:
                extension.on_message(f"{P}/status", rng.choice([b"online", b"offline", b"?", b""]), True)
            elif kind == 1:
                stop = rng.random() < 0.4
                extension.on_message(f"{P}/safety/estop", STOPPED if stop else READY, True)
            else:
                extension.on_message(f"{P}/robot_{rng.randrange(12)}/state", rng.choice([robot(rng.random() < 0.8, rng.random() < 0.5, rng.random() < 0.5), b"{", b""]), True)
            for record in extension.feed.records().values():
                self.assertIn(record.cell_state, {state.value for state in CellState})
                self.assertIn(record.machine_state, {state.value for state in MachineState})
                if stop:
                    self.assertEqual(record.cell_state, "SAFE_STOP")
        self.assertLessEqual(len({name for name, _ in sink.calls}), 8)


class SinkTests(unittest.TestCase):
    def test_one_publisher_per_cell_made_on_first_use_and_destroyed_at_close(self):
        created: list[tuple] = []
        sent: list[dict] = []

        class Message:
            def __init__(self, **fields):
                sent.append(fields)

        node = SimpleNamespace(
            create_publisher=lambda *args: created.append(args) or SimpleNamespace(publish=lambda message: None),
            destroy_publisher=mock.Mock(side_effect=[None, RuntimeError("gone")]),
        )
        sink = Ros2CellStatusSink(node, "/hydra_umc/cells", Message, "QOS")
        sink.publish("robot_1", CellRecord("READY", "IDLE"))
        sink.publish("robot_1", CellRecord("READY", "RUNNING"))
        sink.publish("robot_2", CellRecord("INHIBITED", "OFFLINE"))
        self.assertEqual([(args[1], args[2]) for args in created], [("/hydra_umc/cells/robot_1/status", "QOS"), ("/hydra_umc/cells/robot_2/status", "QOS")])
        self.assertEqual(sent[1], {"cell_state": "READY", "machine_state": "RUNNING", "job_id": ""})
        sink.close()
        self.assertEqual(node.destroy_publisher.call_count, 2)

    def test_without_the_interface_package_the_start_fails_and_says_why(self):
        with mock.patch.dict(sys.modules, {"hydra_umc_interfaces": None, "hydra_umc_interfaces.msg": None}):
            with self.assertRaises(RuntimeError) as context:
                ext_cell_status.ros_sink(object(), CellStatusSettings())
        self.assertIn("hydra_umc_interfaces", str(context.exception))

    def test_with_the_packages_the_qos_keeps_the_last_state_for_a_late_node(self):
        message = types.ModuleType("hydra_umc_interfaces.msg")
        message.CellStatus = "TYPE"  # type: ignore[attr-defined]
        package = types.ModuleType("hydra_umc_interfaces")
        package.msg = message  # type: ignore[attr-defined]
        qos = types.ModuleType("rclpy.qos")
        qos.DurabilityPolicy = SimpleNamespace(TRANSIENT_LOCAL="TL")  # type: ignore[attr-defined]
        qos.QoSProfile = lambda **kw: kw  # type: ignore[attr-defined]
        rclpy = types.ModuleType("rclpy")
        rclpy.qos = qos  # type: ignore[attr-defined]
        modules = {"hydra_umc_interfaces": package, "hydra_umc_interfaces.msg": message, "rclpy": rclpy, "rclpy.qos": qos}
        with mock.patch.dict(sys.modules, modules):
            sink = ext_cell_status.ros_sink(object(), CellStatusSettings(ros_prefix="/x/y"))
        self.assertEqual((sink._type, sink._prefix, sink._qos), ("TYPE", "/x/y", {"depth": 1, "durability": "TL"}))


if __name__ == "__main__":
    unittest.main()
