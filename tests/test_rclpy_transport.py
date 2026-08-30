# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Real, partial rclpy transport tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Tests the real, honest failure/degradation paths of rclpy_transport.py.

ROS 2 (rclpy/std_srvs/std_msgs) is a full distribution install, genuinely
not present in most development environments (including this one) - unlike
this ecosystem's other lazily-imported transport dependencies (pyserial,
paho-mqtt, pymavlink, bosdyn-client, gpiod), rclpy is not something these
tests can fake around at the message-type level without misrepresenting
what is and isn't actually verified. These tests prove the real, honest
thing that IS true on a host without ROS 2: every entry point degrades
cleanly to a clear, reported failure instead of a bare, unhandled
ImportError.
"""

import unittest

from hydra_umc_bridge_ros2 import (
    Ros2SafeStopClient,
    Ros2StateSubscriber,
    create_ros2_node,
)


def _rclpy_installed() -> bool:
    try:
        import rclpy  # noqa: F401

        return True
    except ImportError:
        return False


class FakeNode:
    """Only used if rclpy happens to be installed - never exercised otherwise."""

    def create_client(self, srv_type, srv_name):
        raise AssertionError("not reachable in this environment's test run")

    def create_subscription(self, msg_type, topic, callback, qos_profile):
        raise AssertionError("not reachable in this environment's test run")


class CreateRos2NodeTests(unittest.TestCase):
    def test_missing_rclpy_raises_a_clear_runtime_error_not_an_import_error(self):
        if _rclpy_installed():
            self.skipTest("rclpy is installed in this environment - nothing to prove here")
        with self.assertRaises(RuntimeError) as context:
            create_ros2_node("hydra_umc_bridge_ros2_test")
        self.assertIn("rclpy is not installed", str(context.exception))


class Ros2SafeStopClientTests(unittest.TestCase):
    def test_missing_std_srvs_degrades_to_a_reported_failure_not_a_crash(self):
        if _rclpy_installed():
            self.skipTest("rclpy is installed in this environment - nothing to prove here")
        result = Ros2SafeStopClient().call(FakeNode(), "/hydra_umc/request_safe_stop")
        self.assertFalse(result.called)
        self.assertFalse(result.success)
        self.assertIn("std_srvs is not available", result.reason)


class Ros2StateSubscriberTests(unittest.TestCase):
    def test_missing_std_msgs_degrades_to_a_reported_failure_not_a_crash(self):
        if _rclpy_installed():
            self.skipTest("rclpy is installed in this environment - nothing to prove here")
        result = Ros2StateSubscriber().subscribe(FakeNode(), "/hydra_umc/machine_state", on_state=lambda state: None)
        self.assertFalse(result.subscribed)
        self.assertIn("std_msgs", result.reason)
        self.assertIsNone(result.subscription)


if __name__ == "__main__":
    unittest.main()
