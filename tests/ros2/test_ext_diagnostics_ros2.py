# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Diagnostics extension against a real ROS 2 (container workflow, skipped where rclpy is missing)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A real rclpy node reads a real `diagnostic_msgs/DiagnosticArray` stream and the extension publishes the summary. Seeded stream, injected faults, silence, stop.

Evidence: a ROS 2 container with the publisher node of this test, never a robot, a driver of a real machine or a real installation.
"""

from __future__ import annotations

import json
import random
import threading
import time
import unittest

try:
    import rclpy
    from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
except ImportError:  # no ROS 2 on this host
    rclpy = None

from hydra_umc_bridge_ros2 import spin_node_in_background
from hydra_umc_bridge_ros2.ext_diagnostics import DIAGNOSTICS_TOPIC, DiagnosticsExtension, DiagnosticsSettings

NEEDS_ROS = unittest.skipIf(rclpy is None, "rclpy or diagnostic_msgs is not installed")
SEED = 20260


def wait_for(condition, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


def setUpModule() -> None:
    if rclpy is not None and not rclpy.ok():
        rclpy.init()


def tearDownModule() -> None:
    if rclpy is not None and rclpy.ok():
        rclpy.shutdown()


def make_status(name, level, message="", values=()):
    return DiagnosticStatus(level=level, name=name, message=message, hardware_id="sim", values=[KeyValue(key=k, value=v) for k, v in values])


class _Rig:
    """The bridge node with the extension, a node that plays the drivers, and what the extension published."""

    def __init__(self, **keywords) -> None:
        keywords.setdefault("publish_interval_s", 0.2)
        keywords.setdefault("stale_after_s", 30.0)
        self.lock = threading.Lock()
        self.published: list[tuple[str, dict, bool]] = []
        self.bridge_node = rclpy.create_node("hy_diag_bridge")
        self.driver_node = rclpy.create_node("hy_diag_driver")
        self.publisher = self.driver_node.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self.graph_before = self.graph()
        self.spinner = spin_node_in_background(self.bridge_node)
        self.extension = DiagnosticsExtension(DiagnosticsSettings(enabled=True, **keywords))
        self.extension.start(self.bridge_node, self.record)

    def graph(self) -> tuple:
        node, name, space = self.bridge_node, self.bridge_node.get_name(), self.bridge_node.get_namespace()
        return (
            sorted(topic for topic, _ in node.get_publisher_names_and_types_by_node(name, space)),
            sorted(topic for topic, _ in node.get_service_names_and_types_by_node(name, space)),
            sorted(topic for topic, _ in node.get_client_names_and_types_by_node(name, space)),
        )

    def record(self, topic: str, payload: str, retain: bool) -> None:
        with self.lock:
            self.published.append((topic, json.loads(payload), retain))

    def last(self) -> dict | None:
        with self.lock:
            return self.published[-1][1] if self.published else None

    def count(self) -> int:
        with self.lock:
            return len(self.published)

    def send(self, *statuses) -> None:
        message = DiagnosticArray()
        message.status = list(statuses)
        self.publisher.publish(message)

    def keep_sending(self, make_statuses, until) -> bool:
        """Publishes again every 100 ms until `until()` holds: discovery between two DDS participants takes a moment and the first messages can be lost."""
        end = time.monotonic() + 15
        while time.monotonic() < end:
            self.send(*make_statuses())
            if wait_for(until, 0.3):
                return True
        return False

    def close(self) -> None:
        self.extension.stop()
        self.spinner.stop()
        self.bridge_node.destroy_node()
        self.driver_node.destroy_node()


@NEEDS_ROS
class RealDiagnosticsTests(unittest.TestCase):
    def rig(self, **keywords) -> _Rig:
        rig = _Rig(**keywords)
        self.addCleanup(rig.close)
        return rig

    def test_a_seeded_stream_is_summarised_with_the_worst_level_and_published_retained(self) -> None:
        rig = self.rig()
        rng = random.Random(SEED)
        levels = {f"drv{index}": rng.choice([DiagnosticStatus.OK, DiagnosticStatus.WARN, DiagnosticStatus.ERROR]) for index in range(8)}
        worst = max(levels.values())
        make = lambda: [make_status(name, level, f"state of {name}") for name, level in levels.items()]  # noqa: E731
        done = lambda: (rig.last() or {}).get("counts") is not None and len((rig.last() or {}).get("statuses", [])) == 8  # noqa: E731
        self.assertTrue(rig.keep_sending(make, done), "the summary never held the eight drivers")
        summary = rig.last()
        assert summary is not None
        self.assertEqual(summary["health"], {0: "OK", 1: "WARN", 2: "ERROR"}[worst[0]])
        self.assertEqual(sum(summary["counts"].values()), 8)
        self.assertTrue(all(topic == DIAGNOSTICS_TOPIC and retain for topic, _body, retain in rig.published))

    def test_injected_faults_a_level_that_does_not_exist_too_many_names_and_a_huge_text(self) -> None:
        rig = self.rig(max_statuses=16, max_text_chars=40, include_values=True)
        faulty = [make_status("odd level", b"\x09", "x" * 5000, [("api_token", "do-not-publish"), ("speed", "3")])]
        flood = [make_status(f"flood{index}", DiagnosticStatus.OK) for index in range(200)]
        done = lambda: any(item["name"] == "odd level" for item in (rig.last() or {}).get("statuses", []))  # noqa: E731
        self.assertTrue(rig.keep_sending(lambda: faulty + flood, done))
        summary = rig.last()
        assert summary is not None
        self.assertLessEqual(len(summary["statuses"]), 16)
        self.assertGreater(summary["dropped_statuses"], 0)
        odd = next(item for item in summary["statuses"] if item["name"] == "odd level")
        self.assertEqual(odd["level"], "ERROR")
        self.assertEqual(len(odd["message"]), 40)
        values = {item["key"]: item["value"] for item in odd["values"]}
        self.assertEqual(values, {"api_token": "***", "speed": "3"})
        self.assertNotIn("do-not-publish", json.dumps(rig.published))

    def test_a_silent_driver_turns_the_health_to_stale(self) -> None:
        rig = self.rig(stale_after_s=1.0)
        self.assertTrue(rig.keep_sending(lambda: [make_status("drv", DiagnosticStatus.OK)], lambda: (rig.last() or {}).get("health") == "OK"))
        self.assertTrue(wait_for(lambda: (rig.last() or {}).get("health") == "STALE", 10.0), "a silent topic was still reported as healthy")
        self.assertIn("nothing heard", rig.last()["reason"])  # type: ignore[index]

    def test_the_extension_adds_no_publisher_service_or_client_to_the_node_and_stop_ends_the_feed(self) -> None:
        rig = self.rig()
        self.assertEqual(rig.graph(), rig.graph_before)
        self.assertTrue(rig.keep_sending(lambda: [make_status("drv", DiagnosticStatus.OK)], lambda: rig.count() > 0))
        rig.extension.stop()
        settled = rig.count()
        for _ in range(5):
            rig.send(make_status("drv", DiagnosticStatus.ERROR))
            time.sleep(0.1)
        time.sleep(0.5)
        self.assertEqual(rig.count(), settled)
        self.assertEqual(rig.last()["health"], "OK")  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
