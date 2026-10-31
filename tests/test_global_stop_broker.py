# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - The global stop against a real MQTT broker
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The bridge follows the retained announcement of the global stop through a real HYDRA-UMC-MQTT-BROKER on a free local port (see broker_harness.py).

The tests skip themselves when node, paho-mqtt or the installed broker checkout are not there, so the ordinary run does not need a broker.
"""

import json
import os
import time
import unittest

import broker_harness as harness
from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict

from hydra_umc_bridge_ros2 import Ros2MqttBridge
from hydra_umc_bridge_ros2.global_stop import DEFAULT_TOPIC, GlobalStopSettings, follow_on_connect
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX, MessageDispatcher

SAFE_STOP = json.dumps({"schema_version": "1.0", "state": "SAFE_STOP", "source": "hydra-umc-server"})
READY = json.dumps({"schema_version": "1.0", "state": "READY", "source": "hydra-umc-server"})


class FakeNode:
    pass


def make_bridge(settings):
    return Ros2MqttBridge(FakeNode(), lambda: CellState.READY, global_stop=settings)  # type: ignore[arg-type]


def job(phase: JobPhase, number: int) -> bytes:
    return json.dumps(job_to_dict(BridgeJob(f"j-{number}", f"key-{phase.value}-{number}", "orchestrator", phase, MachineState.IDLE, {}))).encode()


def stopped_by_the_guard(body: dict | None) -> bool:
    return body is not None and "global stop" in str(body.get("reason", ""))


class GlobalStopOverBroker(unittest.TestCase):
    def setUp(self) -> None:
        reason = harness.skip_reason()
        if reason:
            self.skipTest(reason)
        self._closers = []
        self._count = 0
        self.broker = harness.BrokerProcess()
        self.broker.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for closer in reversed(self._closers):
            try:
                closer()
            except Exception:  # noqa: BLE001 - cleanup of a test must not hide its result
                pass
        self.broker.stop()

    def collector(self, name: str) -> harness.Collector:
        collector = harness.Collector(self.broker.port, f"it-{name}-{os.getpid()}")
        self._closers.append(collector.close)
        return collector

    def start_bridge(self, settings: GlobalStopSettings):
        from hydra_umc_sdk.bridge_transport import open_bridge_client

        bridge = make_bridge(settings)
        dispatcher = MessageDispatcher(bridge)
        client = open_bridge_client(
            "127.0.0.1",
            self.broker.port,
            client_id=f"it-stop-{os.getpid()}",
            topic_prefix=TOPIC_PREFIX,
            dispatcher=dispatcher,
            max_connect_attempts=5,
            connect_retry_delay_seconds=0.5,
            on_connected=follow_on_connect(bridge.global_stop),
        )
        client.loop_start()  # type: ignore[attr-defined]

        def close() -> None:
            client.loop_stop()  # type: ignore[attr-defined]
            client.disconnect()  # type: ignore[attr-defined]
            dispatcher.close(wait=False)

        self._closers.append(close)
        return bridge

    def wait_for(self, condition, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.1)
        return False

    def ask(self, asker: harness.Collector, phase: JobPhase = JobPhase.LOAD, qos: int = 1) -> dict | None:
        self._count += 1
        asker.drain()
        asker.publish(f"{TOPIC_PREFIX}cmd/job", job(phase, self._count), qos=qos)
        message = asker.get(10.0, f"{TOPIC_PREFIX}cmd/job/result")
        return json.loads(message[1]) if message else None

    def asker(self) -> harness.Collector:
        asker = self.collector("asker")
        asker.subscribe(f"{TOPIC_PREFIX}availability")
        message = asker.get(15.0, f"{TOPIC_PREFIX}availability")
        self.assertIsNotNone(message, "the bridge never announced itself")
        asker.subscribe(f"{TOPIC_PREFIX}cmd/job/result", 2)
        return asker

    def test_a_retained_stop_is_obeyed_at_connect_and_ready_ends_it(self) -> None:
        server = self.collector("server")
        server.publish(DEFAULT_TOPIC, SAFE_STOP, qos=1, retain=True)  # the stop sits on the broker before the bridge exists
        bridge = self.start_bridge(GlobalStopSettings(True))
        self.assertTrue(self.wait_for(lambda: bridge.global_stop.state == "SAFE_STOP"), "the retained stop never reached the bridge")
        asker = self.asker()
        refused = self.ask(asker)
        self.assertTrue(stopped_by_the_guard(refused), refused)
        # an abort still gets its answer from the gate
        aborted = self.ask(asker, JobPhase.ABORT)
        self.assertIsNotNone(aborted)
        self.assertFalse(stopped_by_the_guard(aborted), aborted)
        # READY ends it
        server.publish(DEFAULT_TOPIC, READY, qos=1, retain=True)
        self.assertTrue(self.wait_for(lambda: bridge.global_stop.state == "READY"))
        resumed = self.ask(asker)
        self.assertIsNotNone(resumed)
        self.assertFalse(stopped_by_the_guard(resumed), resumed)

    def test_a_live_stop_arrives_at_every_qos_and_refuses_at_once(self) -> None:
        bridge = self.start_bridge(GlobalStopSettings(True))
        asker = self.asker()
        server = self.collector("server")
        for qos in (0, 1, 2):
            with self.subTest(qos=qos):
                server.publish(DEFAULT_TOPIC, READY, qos=1, retain=True)
                self.assertTrue(self.wait_for(lambda: bridge.global_stop.state == "READY"))
                server.publish(DEFAULT_TOPIC, SAFE_STOP, qos=qos, retain=True)
                self.assertTrue(self.wait_for(lambda: bridge.global_stop.blocks()))
                self.assertTrue(stopped_by_the_guard(self.ask(asker)))

    def test_the_switch_off_ignores_a_retained_stop(self) -> None:
        server = self.collector("server")
        server.publish(DEFAULT_TOPIC, SAFE_STOP, qos=1, retain=True)
        bridge = self.start_bridge(GlobalStopSettings(False))
        asker = self.asker()
        answer = self.ask(asker)
        self.assertIsNotNone(answer)
        self.assertFalse(stopped_by_the_guard(answer), answer)
        self.assertEqual(bridge.global_stop.state, "UNKNOWN")

    def test_an_unreadable_announcement_fails_closed_through_the_broker(self) -> None:
        server = self.collector("server")
        server.publish(DEFAULT_TOPIC, "{ not json", qos=1, retain=True)
        bridge = self.start_bridge(GlobalStopSettings(True))
        self.assertTrue(self.wait_for(lambda: bridge.global_stop.state == "UNREADABLE"))
        self.assertTrue(stopped_by_the_guard(self.ask(self.asker())))

    def test_the_stop_survives_the_loss_of_the_broker(self) -> None:
        server = self.collector("server")
        server.publish(DEFAULT_TOPIC, SAFE_STOP, qos=1, retain=True)
        bridge = self.start_bridge(GlobalStopSettings(True))
        self.assertTrue(self.wait_for(lambda: bridge.global_stop.state == "SAFE_STOP"))
        port = self.broker.port
        self.broker.stop()
        time.sleep(1.0)
        self.assertTrue(bridge.global_stop.blocks(), "the stop must survive the loss of the broker")
        self.broker = harness.BrokerProcess(port)
        self.broker.start()
        self.assertTrue(bridge.global_stop.blocks())


if __name__ == "__main__":
    unittest.main()
