# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Tests of the diagnostics extension (read only)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The /diagnostics feed towards HYDRA-UMC without ROS 2: the summary, its limits, the silence, the cleaning of texts and the proof that it cannot command anything."""

import json
import random
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from hydra_umc_sdk.bridge_contract import CellState

from hydra_umc_bridge_ros2 import Ros2MqttBridge, ext_diagnostics, service
from hydra_umc_bridge_ros2.extensions import ExtensionConfigError, build_extensions, parse_sections
from hydra_umc_bridge_ros2.ext_diagnostics import (
    DIAGNOSTICS_TOPIC,
    DiagnosticsAggregator,
    DiagnosticsExtension,
    DiagnosticsSettings,
    level_name,
    parse,
    summary_text,
)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def status(name, level=0, message="", hardware_id="", values=()):
    return SimpleNamespace(name=name, level=bytes([level]) if isinstance(level, int) else level, message=message, hardware_id=hardware_id, values=[SimpleNamespace(key=k, value=v) for k, v in values])


def array(*statuses):
    return SimpleNamespace(status=list(statuses))


class SettingsTests(unittest.TestCase):
    def test_it_is_off_by_default_and_the_defaults_are_conservative(self):
        settings = parse(None)
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.include_values)
        self.assertEqual(settings.topic, "/diagnostics")

    def test_wrong_values_are_errors_that_name_the_key(self):
        for raw in (
            {"topic": "diagnostics"},
            {"topic": "/a//b"},
            {"publish_interval_s": 0},
            {"stale_after_s": -1},
            {"publish_interval_s": float("inf")},
            {"max_statuses": 0},
            {"max_statuses": 5000},
            {"max_values": 0},
            {"max_text_chars": 0},
            {"name_prefixes": [""]},
        ):
            with self.subTest(raw=raw), self.assertRaises(ExtensionConfigError):
                parse(raw)

    def test_the_service_reads_the_section_and_builds_the_extension_only_when_enabled(self):
        parsed = parse_sections({"diagnostics": {"enabled": True, "max_statuses": 8}})
        self.assertEqual(parsed["diagnostics"].max_statuses, 8)
        bridge = Ros2MqttBridge(object(), lambda: CellState.READY)  # type: ignore[arg-type]
        build_extensions(parse_sections({"diagnostics": {}}), bridge)
        self.assertEqual(len(bridge.extensions), 0)
        build_extensions(parsed, bridge)
        self.assertEqual(bridge.extensions.names(), ["diagnostics"])
        config = service.ServiceConfig(extensions=parsed)
        self.assertIn("diagnostics", config.extensions)


class LevelTests(unittest.TestCase):
    def test_the_four_levels_in_every_form_and_anything_else_is_an_error(self):
        for raw, expected in ((b"\x00", "OK"), (b"\x01", "WARN"), (b"\x02", "ERROR"), (b"\x03", "STALE"), (0, "OK"), (1, "WARN"), (3, "STALE")):
            with self.subTest(raw=raw):
                self.assertEqual(level_name(raw), expected)
        for raw in (b"\x04", b"", b"\x00\x01", 4, -1, "OK", None, True, 1.0):
            with self.subTest(raw=raw):
                self.assertEqual(level_name(raw), "ERROR")


class AggregatorTests(unittest.TestCase):
    def make(self, **keywords):
        self.clock = Clock()
        settings = DiagnosticsSettings(enabled=True, **keywords)
        return DiagnosticsAggregator(settings, self.clock, lambda: 1_700_000_000.0)

    def test_nothing_is_published_before_anything_was_heard_and_the_silence_is_short(self):
        aggregator = self.make()
        self.assertIsNone(aggregator.snapshot())
        self.clock.now += 31
        silent = aggregator.snapshot()
        assert silent is not None
        self.assertEqual((silent["health"], silent["statuses"]), ("STALE", []))
        self.assertIn("nothing heard", str(silent["reason"]))

    def test_the_worst_level_is_the_health_and_the_last_status_of_a_name_wins(self):
        aggregator = self.make()
        aggregator.ingest(array(status("motor", 0, "fine"), status("camera", 1, "warm")))
        aggregator.ingest(array(status("motor", 2, "stalled")))
        summary = aggregator.snapshot()
        assert summary is not None
        self.assertEqual(summary["health"], "ERROR")
        self.assertEqual(summary["counts"], {"OK": 0, "WARN": 1, "ERROR": 1, "STALE": 0})
        self.assertEqual([record["name"] for record in summary["statuses"]], ["camera", "motor"])  # type: ignore[union-attr]
        self.assertEqual(summary["observed_at"], 1_700_000_000_000)
        aggregator.ingest(array(status("motor", 0, "recovered")))
        again = aggregator.snapshot()
        assert again is not None
        self.assertEqual(again["health"], "WARN")

    def test_silence_turns_a_healthy_summary_into_stale(self):
        aggregator = self.make(stale_after_s=10.0)
        aggregator.ingest(array(status("motor", 0)))
        self.clock.now += 9
        self.assertEqual(aggregator.snapshot()["health"], "OK")  # type: ignore[index]
        self.clock.now += 2
        self.assertEqual(aggregator.snapshot()["health"], "STALE")  # type: ignore[index]
        aggregator.ingest(array(status("motor", 0)))
        self.assertEqual(aggregator.snapshot()["health"], "OK")  # type: ignore[index]

    def test_the_number_of_names_is_bounded_and_the_overflow_is_counted_not_kept(self):
        aggregator = self.make(max_statuses=3)
        aggregator.ingest(array(*[status(f"n{index}") for index in range(10)]))
        summary = aggregator.snapshot()
        assert summary is not None
        self.assertEqual(len(summary["statuses"]), 3)  # type: ignore[arg-type]
        self.assertEqual(summary["dropped_statuses"], 7)
        aggregator.ingest(array(status("n0", 1)))  # a name that is already kept is still updated
        self.assertEqual(aggregator.snapshot()["counts"]["WARN"], 1)  # type: ignore[index]

    def test_texts_are_cleaned_and_cut_and_empty_names_are_skipped(self):
        aggregator = self.make(max_text_chars=10)
        kept = aggregator.ingest(array(status("a\x00b\nc", message="x" * 50, hardware_id="h\x1b[31m"), status("", 2)))
        self.assertEqual(kept, 1)
        record = aggregator.snapshot()["statuses"][0]  # type: ignore[index]
        self.assertEqual(record["name"], "a b c")
        self.assertEqual(len(record["message"]), 10)
        self.assertNotIn("\x1b", record["hardware_id"])

    def test_values_are_left_out_unless_asked_and_a_secret_looking_key_is_hidden_even_then(self):
        data = array(status("m", values=[("speed", "3"), ("api_token", "abc"), ("Password", "p"), ("ssh_key", "k"), ("x", "y")]))
        off = self.make()
        off.ingest(data)
        self.assertNotIn("values", off.snapshot()["statuses"][0])  # type: ignore[index]
        on = self.make(include_values=True, max_values=4)
        on.ingest(data)
        values = on.snapshot()["statuses"][0]["values"]  # type: ignore[index]
        self.assertEqual(len(values), 4)
        self.assertEqual({item["key"]: item["value"] for item in values}, {"speed": "3", "api_token": "***", "Password": "***", "ssh_key": "***"})

    def test_prefixes_keep_only_the_names_that_start_with_one(self):
        aggregator = self.make(name_prefixes=("/arm", "/cell"))
        aggregator.ingest(array(status("/arm: joint"), status("/cell: door"), status("/camera: lens")))
        self.assertEqual([record["name"] for record in aggregator.snapshot()["statuses"]], ["/arm: joint", "/cell: door"])  # type: ignore[index]

    def test_a_malformed_message_keeps_nothing_and_raises_nothing(self):
        aggregator = self.make()
        for bad in (object(), SimpleNamespace(status=None), SimpleNamespace(status=5), SimpleNamespace(status=[object()])):
            with self.subTest(bad=bad):
                aggregator.ingest(bad)
        summary = aggregator.snapshot()
        assert summary is not None
        self.assertEqual(summary["statuses"], [])

    def test_a_seeded_random_stream_never_breaks_the_limits(self):
        rng = random.Random(20260)
        aggregator = self.make(max_statuses=16, max_values=3, max_text_chars=30, include_values=True)
        for _ in range(300):
            statuses = [
                status(
                    f"n{rng.randrange(40)}",
                    rng.choice([0, 1, 2, 3, 9, b"", b"\x00\x00"]),
                    "m" * rng.randrange(200),
                    values=[(f"k{index}", "v" * rng.randrange(100)) for index in range(rng.randrange(8))],
                )
                for _ in range(rng.randrange(1, 6))
            ]
            aggregator.ingest(array(*statuses))
            summary = aggregator.snapshot()
            assert summary is not None
            records = summary["statuses"]
            self.assertLessEqual(len(records), 16)  # type: ignore[arg-type]
            for record in records:  # type: ignore[attr-defined]
                self.assertLessEqual(len(record["message"]), 30)
                self.assertLessEqual(len(record["values"]), 3)
                self.assertIn(record["level"], ("OK", "WARN", "ERROR", "STALE"))
            json.loads(summary_text(summary))


class SummaryTextTests(unittest.TestCase):
    def test_a_summary_over_the_limit_loses_its_values_first_and_then_the_end_of_the_list(self):
        big = {"health": "OK", "statuses": [{"name": f"n{index}", "level": "OK", "values": [{"key": "k", "value": "v" * 500}] * 20} for index in range(30)]}
        text = summary_text(big)
        self.assertLessEqual(len(text), ext_diagnostics.MAX_SUMMARY_CHARS)
        body = json.loads(text)
        self.assertTrue(body["shortened"])
        self.assertNotIn("values", body["statuses"][0])
        huge = {"health": "OK", "statuses": [{"name": "n" * 1000, "level": "OK"} for _ in range(400)]}
        self.assertLessEqual(len(summary_text(huge)), ext_diagnostics.MAX_SUMMARY_CHARS)

    def test_a_small_summary_is_written_as_it_is(self):
        self.assertEqual(json.loads(summary_text({"health": "OK", "statuses": []})), {"health": "OK", "statuses": []})


class ExtensionTests(unittest.TestCase):
    def make(self, **keywords):
        self.clock = Clock()
        self.published: list[tuple[str, str, bool]] = []
        self.subscribed: list[tuple[object, str, object]] = []
        node = SimpleNamespace(destroyed=[], destroy_subscription=lambda subscription: node.destroyed.append(subscription))
        self.node = node

        def subscribe(node_, topic, callback):
            self.subscribed.append((node_, topic, callback))
            return "subscription"

        extension = DiagnosticsExtension(
            DiagnosticsSettings(enabled=True, **keywords), subscribe=subscribe, clock=self.clock, wall=lambda: 1_700_000_000.0, run_thread=False
        )
        extension.start(node, lambda topic, payload, retain: self.published.append((topic, payload, retain)))
        return extension

    def test_it_reads_one_topic_and_publishes_one_retained_summary(self):
        extension = self.make(topic="/diag_agg")
        self.assertEqual([(entry[0] is self.node, entry[1]) for entry in self.subscribed], [(True, "/diag_agg")])
        self.assertFalse(extension.tick())  # nothing heard yet
        self.subscribed[0][2](array(status("motor", 1, "warm")))
        self.assertTrue(extension.tick())
        topic, payload, retain = self.published[0]
        self.assertEqual((topic, retain), (DIAGNOSTICS_TOPIC, True))
        self.assertEqual(json.loads(payload)["health"], "WARN")

    def test_it_is_read_only_it_claims_no_topic_and_has_no_command_verb(self):
        extension = self.make()
        self.assertFalse(extension.claims(DIAGNOSTICS_TOPIC))
        self.assertFalse(extension.claims("hydra/bridges/ros2/cmd/job"))
        self.assertEqual((extension.subscriptions(), extension.moving_verbs, extension.job_verbs), ([], frozenset(), frozenset()))
        self.assertEqual(extension.on_message("hydra/bridges/ros2/cmd/job", b"{}", False), [])

    def test_a_message_of_diagnostics_never_changes_what_the_gate_says(self):
        bridge = Ros2MqttBridge(object(), lambda: CellState.READY)  # type: ignore[arg-type]
        bridge.start_observing()
        before = bridge._observed_state()
        extension = self.make()
        extension.on_array(array(status("motor", 2, "stalled"), status("brake", 2, "stuck")))
        self.assertIs(bridge._observed_state(), before)

    def test_a_failing_publication_is_logged_and_tried_again(self):
        extension = self.make()
        extension.on_array(array(status("motor")))
        calls: list[int] = []

        def broken(topic, payload, retain):
            calls.append(1)
            raise OSError("link down")

        extension._publish = broken
        with self.assertLogs(ext_diagnostics.__name__, "ERROR"):
            self.assertFalse(extension.tick())
        extension._publish = lambda *a: None
        self.assertTrue(extension.tick())

    def test_stop_destroys_the_subscription_once_and_is_safe_to_repeat_or_without_start(self):
        extension = self.make()
        extension.stop()
        extension.stop()
        self.assertEqual(self.node.destroyed, ["subscription"])
        self.assertFalse(extension.tick())
        DiagnosticsExtension(DiagnosticsSettings(enabled=True), run_thread=False).stop()

    def test_a_node_that_is_already_gone_does_not_break_the_stop(self):
        extension = self.make()

        def gone(subscription):
            raise RuntimeError("node destroyed")

        self.node.destroy_subscription = gone
        extension.stop()

    def test_the_thread_publishes_every_interval_until_stopped(self):
        published: list[str] = []
        extension = DiagnosticsExtension(
            DiagnosticsSettings(enabled=True, publish_interval_s=0.01), subscribe=lambda n, t, c: object(), wall=lambda: 1.0
        )
        extension.on_array(array(status("motor")))
        extension.start(SimpleNamespace(destroy_subscription=lambda s: None), lambda topic, payload, retain: published.append(payload))
        import time

        deadline = time.monotonic() + 3
        while len(published) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        extension.stop()
        count = len(published)
        time.sleep(0.05)
        self.assertGreaterEqual(count, 2)
        self.assertEqual(len(published), count)


class RosSubscribeTests(unittest.TestCase):
    def test_without_the_message_package_the_start_fails_and_says_why(self):
        with mock.patch.dict(sys.modules, {"diagnostic_msgs": None, "diagnostic_msgs.msg": None}):
            with self.assertRaises(RuntimeError) as context:
                ext_diagnostics.ros_subscribe(object(), "/diagnostics", lambda message: None)
        self.assertIn("diagnostic_msgs", str(context.exception))

    def test_with_the_message_package_it_is_a_plain_subscription_of_depth_ten(self):
        calls: list[tuple] = []
        module = types.ModuleType("diagnostic_msgs.msg")
        module.DiagnosticArray = "ARRAY"  # type: ignore[attr-defined]
        package = types.ModuleType("diagnostic_msgs")
        package.msg = module  # type: ignore[attr-defined]
        node = SimpleNamespace(create_subscription=lambda *args: calls.append(args) or "sub")
        with mock.patch.dict(sys.modules, {"diagnostic_msgs": package, "diagnostic_msgs.msg": module}):
            result = ext_diagnostics.ros_subscribe(node, "/diagnostics", print)
        self.assertEqual(result, "sub")
        self.assertEqual(calls, [("ARRAY", "/diagnostics", print, 10)])


if __name__ == "__main__":
    unittest.main()
