# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Transport limits and shared global stop guard tests
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The limits of the MQTT transport (off unless the settings switch them on) and the global stop guard that the bridge now takes from the shared SDK."""

import importlib.util
import os
import unittest
from unittest import mock

from hydra_umc_sdk import global_stop as sdk_global_stop
from hydra_umc_sdk.bridge_transport import BridgeHardening

from hydra_umc_bridge_ros2 import mqtt_transport as transport

PREFIX = "HYDRA_UMC_" + transport.GLOBAL_STOP_ENVIRONMENT_NAME + "_"


class Reached(Exception):
    """Raised by the fake client factory: the point where the bridge would connect."""


def run(hardening=None, **env):
    """What `run_forever` hands the dispatcher and the client factory, without a broker."""
    captured: dict = {}

    def fake_open(host, port, **keywords):
        captured.update(keywords)
        raise Reached

    environment = {key: value for key, value in os.environ.items() if not key.startswith(PREFIX)}
    environment.update({PREFIX + key: value for key, value in env.items()})
    with mock.patch.object(transport, "open_bridge_client", fake_open), mock.patch.dict(os.environ, environment, clear=True):
        with unittest.TestCase().assertRaises(Reached):
            transport.run_forever(object(), "broker.invalid", hardening=hardening)
    return captured


class LimitsAreOffByDefault(unittest.TestCase):
    def test_with_no_setting_nothing_is_limited_and_no_backoff_is_asked_for(self):
        captured = run()
        self.assertNotIn("connect_backoff_factor", captured)
        self.assertNotIn("connect_jitter", captured)
        self.assertIsNone(captured["dispatcher"]._lane._max_queue)
        self.assertIsNone(captured["dispatcher"]._max_payload_bytes)

    def test_a_switch_that_is_not_a_plain_yes_leaves_them_off(self):
        for value in ("0", "no", "off", "", "hardened"):
            with self.subTest(value=value):
                captured = run(HARDENED=value, MAX_QUEUE="3")
                self.assertIsNone(captured["dispatcher"]._lane._max_queue)


class LimitsFromTheEnvironment(unittest.TestCase):
    def test_the_switch_bounds_the_queue_and_shapes_the_first_connect(self):
        captured = run(HARDENED="1", MAX_QUEUE="5", MAX_PAYLOAD_BYTES="2048", CONNECT_JITTER="0.1")
        self.assertEqual(captured["dispatcher"]._lane._max_queue, 5)
        self.assertEqual(captured["dispatcher"]._max_payload_bytes, 2048)
        self.assertEqual((captured["connect_backoff_factor"], captured["connect_jitter"]), (2.0, 0.1))

    def test_the_stop_lane_is_never_bounded(self):
        self.assertIsNone(run(HARDENED="1", MAX_QUEUE="1")["dispatcher"]._stop_lane._max_queue)

    def test_a_wrong_value_stops_the_start_before_anything_connects(self):
        for name, value in (("MAX_QUEUE", "0"), ("MAX_QUEUE", "x"), ("CONNECT_JITTER", "9")):
            with self.subTest(name=name), mock.patch.object(transport, "open_bridge_client") as opened:
                with mock.patch.dict(os.environ, {PREFIX + "HARDENED": "1", PREFIX + name: value}):
                    with self.assertRaises(ValueError):
                        transport.run_forever(object(), "broker.invalid")
                opened.assert_not_called()

    def test_the_settings_given_to_the_function_win_over_the_environment(self):
        off = run(BridgeHardening(), HARDENED="1", MAX_QUEUE="5")
        self.assertIsNone(off["dispatcher"]._lane._max_queue)
        on = run(BridgeHardening(enabled=True, max_queue=7))
        self.assertEqual(on["dispatcher"]._lane._max_queue, 7)


class TheGuardIsTheOneOfTheSdk(unittest.TestCase):
    def test_the_bridge_uses_the_guard_of_the_sdk_and_carries_no_copy(self):
        self.assertIs(transport.GlobalStopGuard, sdk_global_stop.GlobalStopGuard)
        self.assertIs(transport.GlobalStopSettings, sdk_global_stop.GlobalStopSettings)
        self.assertIsNone(importlib.util.find_spec("hydra_umc_bridge_ros2.global_stop"))


if __name__ == "__main__":
    unittest.main()
