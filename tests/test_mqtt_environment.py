# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - The MQTT credentials and TLS of the service environment reach the client
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
import os
import sys
import types
import unittest
from unittest import mock


class FakeClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.credentials = None
        self.tls = None
        self.connected_to = None
        FakeClient.instances.append(self)

    def username_pw_set(self, username, password=None):
        self.credentials = (username, password)

    def tls_set(self, ca_certs=None, **kwargs):
        self.tls = ca_certs

    def connect(self, host, port=1883, *args, **kwargs):
        self.connected_to = (host, port)

    def subscribe(self, *args, **kwargs):
        pass

    def reconnect_delay_set(self, *args, **kwargs):
        pass

    def will_set(self, *args, **kwargs):
        pass

    def loop_forever(self):
        return None

    def loop_start(self):
        return None

    def publish(self, *args, **kwargs):
        pass


def fake_paho():
    paho = types.ModuleType("paho")
    mqtt_package = types.ModuleType("paho.mqtt")
    client_module = types.ModuleType("paho.mqtt.client")
    client_module.Client = FakeClient
    client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
    paho.mqtt = mqtt_package
    mqtt_package.client = client_module
    return {"paho": paho, "paho.mqtt": mqtt_package, "paho.mqtt.client": client_module}


def connect(**kwargs):
    from hydra_umc_bridge_ros2 import SubscribeResult, run_forever

    # no ROS 2 here: the node is not spun and the state topic is taken as subscribed
    with mock.patch("hydra_umc_bridge_ros2.mqtt_transport.spin_node_in_background"), mock.patch(
        "hydra_umc_bridge_ros2.mqtt_transport.bridge_state_to_mqtt", return_value=SubscribeResult(True, "subscribed")
    ):
        run_forever(mock.MagicMock(), "127.0.0.1", **kwargs)


class MqttEnvironmentTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances.clear()

    def run_with(self, env, **kwargs):
        with mock.patch.dict(sys.modules, fake_paho()), mock.patch.dict(os.environ, env, clear=False):
            for name in ("HYDRA_MQTT_USERNAME", "HYDRA_MQTT_PASSWORD", "HYDRA_MQTT_PASSWORD_FILE", "HYDRA_MQTT_TLS_CA"):
                if name not in env:
                    os.environ.pop(name, None)
            connect(**kwargs)
        return FakeClient.instances[-1]

    def test_the_credentials_and_the_ca_of_the_environment_are_used(self):
        client = self.run_with({"HYDRA_MQTT_USERNAME": "hydra-umc-bridge-ros2", "HYDRA_MQTT_PASSWORD": "from-env", "HYDRA_MQTT_TLS_CA": "/etc/hydra/ca.pem"})
        self.assertEqual(client.credentials, ("hydra-umc-bridge-ros2", "from-env"))
        self.assertEqual(client.tls, "/etc/hydra/ca.pem")

    def test_an_explicit_user_name_wins_over_the_environment(self):
        client = self.run_with({"HYDRA_MQTT_USERNAME": "env-user", "HYDRA_MQTT_PASSWORD": "env-pw"}, username="given", password="given-pw")
        self.assertEqual(client.credentials, ("given", "given-pw"))

    def test_nothing_in_the_environment_is_an_anonymous_plain_connection(self):
        client = self.run_with({})
        self.assertIsNone(client.credentials)
        self.assertIsNone(client.tls)

    def test_a_password_in_the_environment_without_a_user_name_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_with({"HYDRA_MQTT_PASSWORD": "lonely"})


if __name__ == "__main__":
    unittest.main()
