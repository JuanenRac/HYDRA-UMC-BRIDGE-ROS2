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

import sys
import types
import unittest
from unittest.mock import patch

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


class _FakeFuture:
    """Matches rclpy's own real Future interface just enough to drive
    Ros2SafeStopClient.call()'s own real control flow - not a real ROS 2
    message/executor, see the fake-module helper below for why that's
    the honest line to draw here."""

    def __init__(self, response=None, exc=None, never_done=False):
        self._response = response
        self._exc = exc
        self._never_done = never_done

    def done(self):
        return not self._never_done

    def result(self):
        return self._response

    def exception(self):
        return self._exc


class _FakeTriggerResponse:
    def __init__(self, success, message):
        self.success = success
        self.message = message


class _FakeClient:
    def __init__(self, *, available=True, future=None):
        self._available = available
        self._future = future
        self.async_call_count = 0

    def wait_for_service(self, timeout_sec=None):
        return self._available

    def call_async(self, request):
        self.async_call_count += 1
        return self._future


class _FakeServiceNode:
    def __init__(self, client):
        self._client = client

    def create_client(self, srv_type, srv_name):
        return self._client


def _fake_ros2_modules(spin_effect):
    """Builds minimal fake `rclpy`/`std_srvs.srv` modules so
    Ros2SafeStopClient.call()'s own lazy `import rclpy` / `from
    std_srvs.srv import Trigger` succeed without a real ROS 2 install.

    This tests the REAL control-flow logic ROS-01's fix added - calling
    call_async(), driving spin_until_future_complete(), and handling
    timeout/exception/success - not real ROS 2 message wire
    serialization or a real executor, which genuinely does need a real
    ROS 2 distribution (see this file's own module docstring on why the
    rest of this suite doesn't fake rclpy at the message-type level).
    `spin_effect(node, future, timeout_sec)` stands in for
    rclpy.spin_until_future_complete's own real side effect (or lack of
    one, for the timeout case).
    """
    fake_rclpy = types.ModuleType("rclpy")
    fake_rclpy.spin_until_future_complete = spin_effect  # type: ignore[attr-defined]

    fake_std_srvs = types.ModuleType("std_srvs")
    fake_std_srvs_srv = types.ModuleType("std_srvs.srv")

    class _FakeTrigger:
        class Request:
            pass

    fake_std_srvs_srv.Trigger = _FakeTrigger  # type: ignore[attr-defined]
    return {
        "rclpy": fake_rclpy,
        "std_srvs": fake_std_srvs,
        "std_srvs.srv": fake_std_srvs_srv,
    }


class Ros2SafeStopClientRealControlFlowTests(unittest.TestCase):
    """ROS-01 regression (found in an ecosystem-wide software-improvements
    audit): exercises the real call_async()/spin_until_future_complete()
    control flow via fake-but-structurally-real ROS 2 modules, proving
    the actual fix rather than just reading rclpy's own documentation."""

    def test_a_response_that_never_completes_times_out_instead_of_hanging(self):
        future = _FakeFuture(never_done=True)
        client = _FakeClient(future=future)
        node = _FakeServiceNode(client)

        def spin_effect(node_arg, future_arg, timeout_sec=None):
            # The real rclpy contract: spin_until_future_complete returns
            # normally on timeout WITHOUT marking the future done - the
            # caller must check future.done() itself, exactly what this
            # fix added.
            self.assertIs(future_arg, future)
            self.assertEqual(timeout_sec, 0.5)

        with patch.dict(sys.modules, _fake_ros2_modules(spin_effect)):
            result = Ros2SafeStopClient().call(node, "/hydra_umc/request_safe_stop", timeout_sec=0.5)
        self.assertTrue(result.called)
        self.assertFalse(result.success)
        self.assertIn("timed out", result.reason)
        self.assertEqual(client.async_call_count, 1)

    def test_a_real_response_reports_the_actual_success_and_message(self):
        future = _FakeFuture(response=_FakeTriggerResponse(True, "e-stop cleared"))
        client = _FakeClient(future=future)
        node = _FakeServiceNode(client)

        with patch.dict(sys.modules, _fake_ros2_modules(lambda *a, **k: None)):
            result = Ros2SafeStopClient().call(node, "/hydra_umc/request_safe_stop")
        self.assertTrue(result.called)
        self.assertTrue(result.success)
        self.assertEqual(result.reason, "e-stop cleared")

    def test_a_future_exception_is_reported_not_raised(self):
        future = _FakeFuture(exc=RuntimeError("service crashed mid-call"))
        client = _FakeClient(future=future)
        node = _FakeServiceNode(client)

        with patch.dict(sys.modules, _fake_ros2_modules(lambda *a, **k: None)):
            result = Ros2SafeStopClient().call(node, "/hydra_umc/request_safe_stop")
        self.assertTrue(result.called)
        self.assertFalse(result.success)
        self.assertIn("service crashed mid-call", result.reason)

    def test_service_unavailable_never_reaches_call_async(self):
        client = _FakeClient(available=False, future=_FakeFuture())
        node = _FakeServiceNode(client)

        with patch.dict(sys.modules, _fake_ros2_modules(lambda *a, **k: None)):
            result = Ros2SafeStopClient().call(node, "/hydra_umc/request_safe_stop")
        self.assertFalse(result.called)
        self.assertEqual(client.async_call_count, 0)


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
