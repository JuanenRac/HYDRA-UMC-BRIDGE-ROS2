# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Tests of the extension hook
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The hook for optional extensions: the section parser, the host, the way the bridge routes a claimed topic, the global stop on a moving verb, and the start and stop in `run_forever`."""

import json
import types
import unittest
from dataclasses import dataclass
from unittest import mock

from hydra_umc_sdk.bridge_contract import CellState
from hydra_umc_sdk.bridge_transport import MqttPublish
from hydra_umc_sdk.global_stop import GlobalStopGuard, GlobalStopSettings

from hydra_umc_bridge_ros2 import Ros2MqttBridge, extensions, service
from hydra_umc_bridge_ros2 import mqtt_transport as transport
from hydra_umc_bridge_ros2.extensions import BridgeExtension, ExtensionConfigError, ExtensionHost, section_from_dict, topic_matches
from hydra_umc_bridge_ros2.mqtt_transport import MAX_PAYLOAD_BYTES, TOPIC_PREFIX, MessageDispatcher

STOPPED = json.dumps({"state": "SAFE_STOP"}).encode()
READY = json.dumps({"state": "READY"}).encode()


class FakeNode:
    pass


@dataclass(frozen=True)
class Sample:
    enabled: bool = False
    count: int = 3
    ratio: float = 0.5
    label: str = "a"
    names: tuple[str, ...] = ()


class Recorder(BridgeExtension):
    """Claims `cmd/probe` (a moving command) and `feed/in` (an inline feed)."""

    name = "recorder"
    moving_verbs = frozenset({"cmd/probe"})

    def __init__(self) -> None:
        self.seen: list[tuple[str, bytes, bool]] = []
        self.started: list[object] = []
        self.stopped = 0
        self.fail = False

    def subscriptions(self):
        return [("plant/feed/#", 1)]

    def claims(self, topic):
        return topic in (f"{TOPIC_PREFIX}cmd/probe", "plant/feed/in")

    def inline(self, topic):
        return topic == "plant/feed/in"

    def on_message(self, topic, payload, retained):
        if self.fail:
            raise RuntimeError("boom")
        self.seen.append((topic, payload, retained))
        return [MqttPublish(f"{TOPIC_PREFIX}cmd/probe/result", json.dumps({"allowed": True}))]

    def start(self, node, publish):
        self.started.append((node, publish))

    def stop(self):
        self.stopped += 1


def make_bridge(settings=None):
    bridge = Ros2MqttBridge(FakeNode(), lambda: CellState.READY, global_stop=settings or GlobalStopSettings())  # type: ignore[arg-type]
    recorder = Recorder()
    bridge.extensions.add(recorder)
    return bridge, recorder


class SectionTests(unittest.TestCase):
    def test_the_defaults_come_from_the_class_and_none_gives_them(self):
        self.assertEqual(section_from_dict(None, Sample, "x"), Sample())
        self.assertEqual(section_from_dict({}, Sample, "x"), Sample())

    def test_values_of_the_right_type_are_taken(self):
        got = section_from_dict({"enabled": True, "count": 4, "ratio": 2, "label": "b", "names": ["p", "q"]}, Sample, "x")
        self.assertEqual(got, Sample(True, 4, 2.0, "b", ("p", "q")))
        self.assertIsInstance(got.ratio, float)

    def test_a_mistyped_key_or_value_is_an_error_and_says_where(self):
        for raw, word in (
            ({"enable": True}, "enable"),
            ({"enabled": "yes"}, "enabled"),
            ({"count": True}, "count"),
            ({"count": 1.5}, "count"),
            ({"ratio": "1"}, "ratio"),
            ({"names": "p"}, "names"),
            ({"names": ["p", 1]}, "names"),
            ([], "JSON object"),
        ):
            with self.subTest(raw=raw), self.assertRaises(ExtensionConfigError) as context:
                section_from_dict(raw, Sample, "x")
            self.assertIn(word, str(context.exception))

    def test_an_unknown_extension_and_a_bad_name_are_errors(self):
        for name in ("nothing_here", "../x", "Upper", "", "a b"):
            with self.subTest(name=name), self.assertRaises(ExtensionConfigError):
                extensions.load_module(name)
        with self.assertRaises(ExtensionConfigError):
            extensions.parse_sections([])
        self.assertEqual(extensions.parse_sections(None), {})

    def test_the_service_reports_a_bad_section_as_a_configuration_error(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "bridge.json")
            path.write_text(json.dumps({"extensions": {"nothing_here": {}}}), encoding="utf-8")
            with self.assertRaises(service.ConfigError):
                service.load_service_config(path)
            path.write_text(json.dumps({"extensions": {}}), encoding="utf-8")
            self.assertEqual(service.load_service_config(path).extensions, {})
        self.assertEqual(service.ServiceConfig().extensions, {})


class TopicTests(unittest.TestCase):
    def test_filters_match_like_mqtt(self):
        for flt, topic, expected in (
            ("a/b", "a/b", True),
            ("a/+/c", "a/x/c", True),
            ("a/+/c", "a/x/d", False),
            ("a/#", "a/x/y", True),
            ("a/#", "a", True),
            ("a/+", "a/x/y", False),
            ("a/b/c", "a/b", False),
            ("#", "a/b", True),
        ):
            with self.subTest(filter=flt, topic=topic):
                self.assertEqual(topic_matches(flt, topic), expected)


class HostTests(unittest.TestCase):
    def test_the_host_collects_what_its_extensions_declare(self):
        host = ExtensionHost()
        recorder = Recorder()
        host.add(recorder)
        self.assertEqual((len(host), host.names()), (1, ["recorder"]))
        self.assertEqual(host.subscriptions(), [("plant/feed/#", 1)])
        self.assertEqual(host.moving_verbs(), frozenset({"cmd/probe"}))
        self.assertEqual(host.job_verbs(), frozenset())
        self.assertIs(host.claiming("plant/feed/in"), recorder)
        self.assertIsNone(host.claiming("plant/other"))
        with self.assertRaises(ValueError):
            host.add(Recorder())

    def test_the_base_class_does_nothing(self):
        base = BridgeExtension()
        self.assertEqual((base.subscriptions(), base.claims("x"), base.inline("x"), base.on_message("x", b"", False)), ([], False, False, []))
        self.assertIsNone(base.start(object(), lambda *a: None))
        self.assertIsNone(base.stop())

    def test_a_failing_start_stops_the_ones_started_and_goes_on(self):
        host = ExtensionHost()
        first, second = Recorder(), Recorder()
        second.name = "second"

        def broken(node, publish):
            raise RuntimeError("no ROS message package")

        second.start = broken  # type: ignore[method-assign]
        host.add(first)
        host.add(second)
        with self.assertRaises(RuntimeError):
            host.start(object(), lambda *a: None)
        self.assertEqual(first.stopped, 1)

    def test_one_extension_that_cannot_stop_does_not_keep_the_others_running(self):
        host = ExtensionHost()
        first, second = Recorder(), Recorder()
        second.name = "second"

        def broken():
            raise RuntimeError("stuck")

        second.stop = broken  # type: ignore[method-assign]
        host.add(first)
        host.add(second)
        with self.assertLogs(extensions.__name__, "ERROR"):
            host.stop()
        self.assertEqual(first.stopped, 1)


class BridgeRoutingTests(unittest.TestCase):
    def test_a_claimed_command_reaches_the_extension_and_the_others_are_ignored_as_before(self):
        bridge, recorder = make_bridge()
        out = bridge.handle_message(f"{TOPIC_PREFIX}cmd/probe", b"{}")
        self.assertEqual(json.loads(out[0].payload), {"allowed": True})
        self.assertEqual(recorder.seen, [(f"{TOPIC_PREFIX}cmd/probe", b"{}", False)])
        self.assertEqual(bridge.handle_message(f"{TOPIC_PREFIX}cmd/unknown", b"{}"), [])

    def test_a_retained_replay_of_a_command_reaches_the_extension_marked_retained_and_gets_no_answer_of_the_bridge(self):
        bridge, recorder = make_bridge()
        bridge.handle_message(f"{TOPIC_PREFIX}cmd/probe", b"{}", retained=True)
        self.assertEqual(recorder.seen[0][2], True)

    def test_a_moving_verb_is_refused_while_the_global_stop_is_active(self):
        guard = GlobalStopGuard(GlobalStopSettings(True))
        guard.observe(STOPPED)
        bridge, recorder = make_bridge(guard)
        out = bridge.handle_message(f"{TOPIC_PREFIX}cmd/probe", b"{}")
        self.assertFalse(json.loads(out[0].payload)["allowed"])
        self.assertIn("global stop", json.loads(out[0].payload)["reason"])
        self.assertEqual(recorder.seen, [])
        guard.observe(READY)
        self.assertTrue(json.loads(bridge.handle_message(f"{TOPIC_PREFIX}cmd/probe", b"{}")[0].payload)["allowed"])

    def test_a_feed_is_not_refused_by_the_global_stop(self):
        guard = GlobalStopGuard(GlobalStopSettings(True))
        guard.observe(STOPPED)
        bridge, recorder = make_bridge(guard)
        bridge.handle_message("plant/feed/in", b"{}", retained=True)
        self.assertEqual(len(recorder.seen), 1)

    def test_a_payload_that_is_too_large_is_refused_for_a_command_and_ignored_for_a_feed(self):
        bridge, recorder = make_bridge()
        out = bridge.handle_message(f"{TOPIC_PREFIX}cmd/probe", b"x" * (MAX_PAYLOAD_BYTES + 1))
        self.assertIn("larger", json.loads(out[0].payload)["reason"])
        self.assertEqual(bridge.handle_message("plant/feed/in", b"x" * (MAX_PAYLOAD_BYTES + 1)), [])
        self.assertEqual(recorder.seen, [])

    def test_an_extension_that_raises_never_ends_the_loop(self):
        bridge, recorder = make_bridge()
        recorder.fail = True
        with self.assertLogs(transport.__name__, "ERROR"):
            out = bridge.handle_message(f"{TOPIC_PREFIX}cmd/probe", b"{}")
            feed = bridge.handle_message("plant/feed/in", b"{}")
        self.assertIn("RuntimeError", json.loads(out[0].payload)["reason"])
        self.assertEqual(feed, [])


class FakeClient:
    def __init__(self):
        self.published: list[tuple[str, str, int, bool]] = []
        self.subscribed: list[tuple[str, int]] = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))


def message(topic, payload=b"{}", retain=False):
    return types.SimpleNamespace(topic=topic, payload=payload, retain=retain)


class DispatcherTests(unittest.TestCase):
    def test_an_inline_feed_is_handled_at_once_even_when_retained_and_its_answer_is_published(self):
        bridge, recorder = make_bridge()
        dispatcher = MessageDispatcher(bridge)
        self.addCleanup(dispatcher.close)
        client = FakeClient()
        dispatcher.on_message(client, None, message("plant/feed/in", retain=True))
        self.assertEqual(recorder.seen, [("plant/feed/in", b"{}", True)])
        self.assertEqual(len(client.published), 1)

    def test_a_command_goes_through_the_lane_and_a_retained_one_is_dropped_as_before(self):
        bridge, recorder = make_bridge()
        dispatcher = MessageDispatcher(bridge)
        client = FakeClient()
        dispatcher.on_message(client, None, message(f"{TOPIC_PREFIX}cmd/probe", retain=True))
        dispatcher.on_message(client, None, message(f"{TOPIC_PREFIX}cmd/probe"))
        dispatcher.close(wait=True)
        self.assertEqual([item[2] for item in recorder.seen], [False])

    def test_the_announcement_of_the_global_stop_reaches_the_guard_and_the_extension_that_claims_it(self):
        guard = GlobalStopGuard(GlobalStopSettings(True))
        bridge, recorder = make_bridge(guard)

        class Both(Recorder):
            name = "both"

            def claims(self, topic):
                return topic == guard.topic

            def inline(self, topic):
                return True

        bridge.extensions.add(Both())
        dispatcher = MessageDispatcher(bridge)
        self.addCleanup(dispatcher.close)
        dispatcher.on_message(FakeClient(), None, message(guard.topic, STOPPED, retain=True))
        self.assertEqual(guard.state, "SAFE_STOP")
        both = next(item for item in bridge.extensions if item.name == "both")
        self.assertEqual(len(both.seen), 1)  # type: ignore[attr-defined]

    def test_a_handler_that_raises_does_not_end_the_network_loop(self):
        bridge, recorder = make_bridge()
        recorder.fail = True
        dispatcher = MessageDispatcher(bridge)
        self.addCleanup(dispatcher.close)
        with self.assertLogs(transport.__name__, "ERROR"):
            dispatcher.on_message(FakeClient(), None, message("plant/feed/in"))


class RunForeverTests(unittest.TestCase):
    def run_it(self, bridge):
        captured: dict = {}
        client = FakeClient()
        client.loop_forever = lambda: None  # type: ignore[attr-defined]

        def fake_open(host, port, **keywords):
            captured.update(keywords)
            return client

        with mock.patch.object(transport, "open_bridge_client", fake_open), mock.patch.object(transport, "spin_node_in_background") as spin, mock.patch.object(
            transport, "bridge_state_to_mqtt", return_value=transport.SubscribeResult(True, "subscribed")
        ):
            transport.run_forever(bridge, "broker.invalid")
        return captured, client, spin

    def test_the_extensions_are_started_after_the_node_spins_and_stopped_at_the_end(self):
        bridge, recorder = make_bridge()
        captured, client, spin = self.run_it(bridge)
        self.assertEqual(len(recorder.started), 1)
        node, publish = recorder.started[0]
        self.assertIs(node, bridge.node)
        publish("t", "p", True)
        self.assertEqual(client.published[-1], ("t", "p", 0, True))
        self.assertEqual(recorder.stopped, 1)
        captured["on_connected"](client)
        self.assertEqual(client.subscribed, [("plant/feed/#", 1)])

    def test_the_connect_callback_follows_the_global_stop_and_subscribes_the_extensions(self):
        guard = GlobalStopGuard(GlobalStopSettings(True))
        bridge, recorder = make_bridge(guard)
        captured, client, spin = self.run_it(bridge)
        captured["on_connected"](client)
        self.assertEqual(client.subscribed, [(guard.topic, 1), ("plant/feed/#", 1)])

    def test_with_no_extension_the_connect_callback_is_what_it_was(self):
        bridge = Ros2MqttBridge(FakeNode(), lambda: CellState.READY, global_stop=GlobalStopSettings())  # type: ignore[arg-type]
        captured, client, spin = self.run_it(bridge)
        self.assertIsNone(captured["on_connected"])

    def test_an_extension_that_cannot_start_stops_the_service(self):
        bridge, recorder = make_bridge()

        def broken(node, publish):
            raise RuntimeError("diagnostic_msgs is not installed")

        recorder.start = broken  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            self.run_it(bridge)
        self.assertEqual(recorder.stopped, 1)  # stopping is safe on an extension that never started


if __name__ == "__main__":
    unittest.main()
