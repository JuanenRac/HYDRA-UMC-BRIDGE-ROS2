# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Integration with a real MQTT broker
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""This bridge's transport against a real HYDRA-UMC-MQTT-BROKER on a free local port (see broker_harness.py).

The tests skip themselves when node, paho-mqtt or the installed broker checkout are not there, so the ordinary run does not need a broker.
"""

import unittest

import broker_harness as harness
from hydra_umc_sdk.bridge_contract import CellState

from hydra_umc_bridge_ros2 import Ros2MqttBridge
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX, MessageDispatcher


class FakeNode:
    pass


class Ros2OverBroker(harness.BridgeOverBrokerCases):
    PREFIX = TOPIC_PREFIX
    COMMAND_VERB = "job"

    def make_bridge(self) -> object:
        return Ros2MqttBridge(FakeNode(), lambda: CellState.READY)  # type: ignore[arg-type]

    def make_dispatcher(self, bridge: object) -> object:
        return MessageDispatcher(bridge)

    def command_payload(self) -> bytes:
        return b"{this is not json"

    def answer_is_right(self, body: dict) -> bool:
        return body.get("allowed") is False and "malformed" in str(body.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
