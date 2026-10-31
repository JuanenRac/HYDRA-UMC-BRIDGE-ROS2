# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Harness for the tests that run against a real MQTT broker
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Starts a real HYDRA-UMC-MQTT-BROKER on a free local port of its own and drives a bridge against it.

Nothing here runs unless a broker can be started: the tests that use this module skip themselves when node, the `paho-mqtt` package or the broker
checkout (with its `node_modules`) are missing, so the ordinary test run, and the CI, never need a broker. Where the broker lives is read from
`HYDRA_UMC_TEST_BROKER_DIR` (default: the sibling folder `HYDRA-UMC-MQTT-BROKER`). The port is picked by the operating system, never 1883.

The scenarios (`BridgeOverBrokerCases`) are the same for every bridge that speaks the shared transport: the retained availability, a command answered at
QoS 0, 1 and 2, a retained command that is only a replay, a message published at each QoS by the dispatcher, the last will after an unclean loss of the link and
the way back after the broker itself goes down and comes up again. Each bridge's test file fills in `make_bridge`, the command to send and the answer to expect.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
import unittest
from pathlib import Path
from typing import Callable

_FORBIDDEN_PORTS = {1883, 8883, 8083, 9883}


def broker_dir() -> Path:
    override = os.environ.get("HYDRA_UMC_TEST_BROKER_DIR", "").strip()
    return Path(override) if override else Path(__file__).resolve().parents[2] / "HYDRA-UMC-MQTT-BROKER"


def skip_reason() -> str | None:
    """Why the broker tests cannot run here, or None when they can."""
    try:
        import paho.mqtt.client  # noqa: F401
    except ImportError:
        return "paho-mqtt is not installed"
    if shutil.which("node") is None:
        return "node is not installed"
    folder = broker_dir()
    if not (folder / "src" / "server.ts").is_file() or not (folder / "node_modules" / "tsx").is_dir() or not (folder / "node_modules" / "aedes").is_dir():
        return f"no installed HYDRA-UMC-MQTT-BROKER at {folder} (run `npm ci` there, or set HYDRA_UMC_TEST_BROKER_DIR)"
    return None


def free_port() -> int:
    while True:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port not in _FORBIDDEN_PORTS:
            return port


class BrokerProcess:
    """One broker process of our own; `stop()` and `start()` again on the same port simulate the broker going down and coming back."""

    def __init__(self, port: int | None = None) -> None:
        self.port = port or free_port()
        self._process: subprocess.Popen[bytes] | None = None

    def start(self, timeout: float = 30.0) -> None:
        environment = {key: value for key, value in os.environ.items() if not key.startswith(("MQTT_", "HYDRA_UMC_BROKER", "HYDRA_MQTT_")) and key != "PORT"}
        environment.update({"HYDRA_UMC_BROKER_PORT": str(self.port), "MQTT_BIND_HOST": "127.0.0.1", "NODE_ENV": "development"})
        self._process = subprocess.Popen(
            ["node", "--import", "tsx", "src/server.ts"],
            cwd=str(broker_dir()),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"the broker exited at once with code {self._process.returncode}")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.2)
        self.stop()
        raise RuntimeError("the broker did not listen in time")

    def stop(self) -> None:
        """Hard stop of our own process only (the broker does not get to publish anything)."""
        process, self._process = self._process, None
        if process is None:
            return
        process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


class Collector:
    """A plain paho client that records what it receives."""

    def __init__(self, port: int, client_id: str) -> None:
        import paho.mqtt.client as mqtt

        self.messages: "queue.Queue[tuple[str, bytes, int, bool]]" = queue.Queue()
        self._connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self.client.on_connect = lambda *_args: self._connected.set()
        self.client.on_message = lambda _c, _u, m: self.messages.put((m.topic, bytes(m.payload), m.qos, bool(m.retain)))
        self.client.connect("127.0.0.1", port)
        self.client.loop_start()
        if not self._connected.wait(10):
            raise RuntimeError("the collector could not connect")

    def subscribe(self, topic: str, qos: int = 2) -> None:
        self.client.subscribe(topic, qos)
        time.sleep(0.3)  # the SUBACK has no hook worth the code: a short pause is enough on a local broker

    def publish(self, topic: str, payload: bytes | str, qos: int = 0, retain: bool = False) -> None:
        info = self.client.publish(topic, payload, qos=qos, retain=retain)
        info.wait_for_publish(10)

    def get(self, timeout: float = 10.0, topic: str | None = None) -> tuple[str, bytes, int, bool] | None:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                return None
            if topic is None or message[0] == topic:
                return message

    def drain(self) -> None:
        while not self.messages.empty():
            self.messages.get_nowait()

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


class BridgeOverBrokerCases(unittest.TestCase):
    """The scenarios; a subclass sets the class attributes and `make_dispatcher_and_handler`.

    RESULT_SUFFIX         where the answer is published when it is not `cmd/<verb>/result` (the CNC status goes to `state`)
    PREFIX                the bridge's topic prefix, with the trailing slash
    COMMAND_VERB          the verb to send (`status`, `job`...); the answer comes on `<PREFIX>cmd/<verb>/result`
    command_payload()     the bytes of that command
    answer_is_right(body) True when the decoded JSON answer is the one the bridge must give
    make_bridge()         a fresh bridge object (the real one, with fakes behind it)
    make_dispatcher(b)    the repo's own MessageDispatcher around that bridge
    """

    PREFIX = ""
    last_answer_qos: int | None = None
    COMMAND_VERB = "status"
    RESULT_SUFFIX = ""  # where the answer comes; empty means `cmd/<verb>/result`

    @property
    def result_topic(self) -> str:
        return f"{self.PREFIX}{self.RESULT_SUFFIX or f'cmd/{self.COMMAND_VERB}/result'}"

    def command_payload(self) -> bytes:
        return b""

    def answer_is_right(self, body: dict) -> bool:
        return isinstance(body, dict)

    def make_bridge(self) -> object:
        raise NotImplementedError

    def make_dispatcher(self, bridge: object) -> object:
        raise NotImplementedError

    # -- plumbing ------------------------------------------------------------------------------------------------
    def setUp(self) -> None:
        reason = skip_reason()
        if reason:
            self.skipTest(reason)
        self._closers: list[Callable[[], None]] = []
        self.broker = BrokerProcess()
        self.broker.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for closer in reversed(self._closers):
            try:
                closer()
            except Exception:  # noqa: BLE001 - cleanup of a test must not hide its result
                pass
        self.broker.stop()

    def collector(self, name: str) -> Collector:
        collector = Collector(self.broker.port, f"it-{name}-{os.getpid()}")
        self._closers.append(collector.close)
        return collector

    def start_bridge(self, bridge: object | None = None, client_id: str = "it-bridge") -> tuple[object, object]:
        """Connects the repo's bridge through the shared `open_bridge_client`; returns (paho client, dispatcher)."""
        from hydra_umc_sdk.bridge_transport import open_bridge_client

        dispatcher = self.make_dispatcher(bridge if bridge is not None else self.make_bridge())
        client = open_bridge_client(
            "127.0.0.1",
            self.broker.port,
            client_id=f"{client_id}-{os.getpid()}",
            topic_prefix=self.PREFIX,
            dispatcher=dispatcher,  # type: ignore[arg-type]
            max_connect_attempts=5,
            connect_retry_delay_seconds=0.5,
        )
        client.loop_start()  # type: ignore[attr-defined]

        def close() -> None:
            client.loop_stop()  # type: ignore[attr-defined]
            client.disconnect()  # type: ignore[attr-defined]
            dispatcher.close(wait=False)  # type: ignore[attr-defined]

        self._closers.append(close)
        return client, dispatcher

    def wait_online(self, watcher: Collector, timeout: float = 15.0) -> tuple[str, bytes, int, bool]:
        message = watcher.get(timeout, f"{self.PREFIX}availability")
        self.assertIsNotNone(message, "the bridge never announced itself")
        assert message is not None
        return message

    def ask(self, asker: Collector, qos: int, timeout: float = 10.0) -> dict | None:
        asker.drain()
        asker.publish(f"{self.PREFIX}cmd/{self.COMMAND_VERB}", self.command_payload(), qos=qos)
        message = asker.get(timeout, self.result_topic)
        self.last_answer_qos = message[2] if message else None
        return json.loads(message[1]) if message else None

    # -- scenarios -----------------------------------------------------------------------------------------------
    def test_availability_is_online_retained_for_a_late_subscriber(self) -> None:
        self.start_bridge()
        first = self.collector("first")
        first.subscribe(f"{self.PREFIX}availability")
        self.wait_online(first)  # the bridge is announced; whoever comes now is late
        late = self.collector("late")
        late.subscribe(f"{self.PREFIX}availability")
        topic, payload, qos, retained = self.wait_online(late)
        self.assertEqual(payload, b"online")
        self.assertTrue(retained, "a late subscriber must receive the retained flag")
        self.assertEqual(qos, 1)

    def test_command_is_answered_at_every_qos(self) -> None:
        self.start_bridge()
        asker = self.collector("asker")
        asker.subscribe(f"{self.PREFIX}availability")
        self.wait_online(asker)
        asker.subscribe(self.result_topic, 2)
        for qos in (0, 1, 2):
            with self.subTest(qos=qos):
                body = self.ask(asker, qos)
                self.assertIsNotNone(body, f"no answer to a command published at QoS {qos}")
                self.assertTrue(self.answer_is_right(body), body)  # type: ignore[arg-type]

    def test_retained_command_replayed_on_subscribe_is_never_answered(self) -> None:
        # a retained command sits on the broker before the bridge (re)subscribes: the broker replays it with the retain flag, and that is no request
        stale = self.collector("stale")
        stale.subscribe(self.result_topic)
        stale.publish(f"{self.PREFIX}cmd/{self.COMMAND_VERB}", self.command_payload(), qos=1, retain=True)
        self.start_bridge()
        self.wait_online_via(stale)
        self.assertIsNone(stale.get(2.0, self.result_topic), "a retained command was answered as if it were live")

    def wait_online_via(self, watcher: Collector) -> None:
        watcher.subscribe(f"{self.PREFIX}availability")
        self.wait_online(watcher)

    def test_dispatcher_publishes_at_the_qos_the_message_asks_for(self) -> None:
        from hydra_umc_sdk.bridge_transport import MqttPublish

        class Echo:
            def handle_message(self, topic: str, payload: bytes, *, retained: bool = False) -> list[MqttPublish]:
                if not topic.endswith("/cmd/qosprobe"):
                    return []
                wanted = int(payload.decode("ascii"))
                return [MqttPublish(f"{topic}/result", f"qos{wanted}", retain=False, qos=wanted)]

        self.start_bridge(Echo())
        listener = self.collector("qos")
        listener.subscribe(f"{self.PREFIX}availability")
        self.wait_online(listener)
        listener.subscribe(f"{self.PREFIX}cmd/qosprobe/result", 2)
        for wanted in (0, 1, 2):
            with self.subTest(qos=wanted):
                listener.drain()
                listener.publish(f"{self.PREFIX}cmd/qosprobe", str(wanted), qos=1)
                message = listener.get(10.0, f"{self.PREFIX}cmd/qosprobe/result")
                self.assertIsNotNone(message)
                assert message is not None
                self.assertEqual(message[1], f"qos{wanted}".encode())
                self.assertEqual(message[2], wanted, "the broker delivered the message at another QoS than the bridge published it")

    def test_retained_publish_of_the_dispatcher_reaches_a_late_subscriber(self) -> None:
        from hydra_umc_sdk.bridge_transport import MqttPublish

        class State:
            def handle_message(self, topic: str, payload: bytes, *, retained: bool = False) -> list[MqttPublish]:
                if topic.endswith("/cmd/stateprobe"):
                    return [MqttPublish(f"{topic.rsplit('/cmd/', 1)[0]}/state", '{"v":1}', retain=True, qos=1)]
                return []

        self.start_bridge(State())
        trigger = self.collector("trigger")
        trigger.subscribe(f"{self.PREFIX}availability")
        self.wait_online(trigger)
        trigger.publish(f"{self.PREFIX}cmd/stateprobe", b"", qos=1)
        time.sleep(1.0)
        late = self.collector("latestate")
        late.subscribe(f"{self.PREFIX}state", 2)
        message = late.get(10.0, f"{self.PREFIX}state")
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message[1], b'{"v":1}')
        self.assertTrue(message[3])

    def test_last_will_turns_availability_offline_when_the_link_dies(self) -> None:
        client, _dispatcher = self.start_bridge()
        watcher = self.collector("will")
        watcher.subscribe(f"{self.PREFIX}availability")
        self.assertEqual(self.wait_online(watcher)[1], b"online")
        # an unclean loss: the socket goes away with no DISCONNECT, as when the process or the network dies
        client.loop_stop()  # type: ignore[attr-defined]
        client._sock.close()  # type: ignore[attr-defined]
        message = watcher.get(15.0, f"{self.PREFIX}availability")
        self.assertIsNotNone(message, "the broker never published the last will")
        assert message is not None
        self.assertEqual(message[1], b"offline")
        self.assertEqual(message[2], 1)
        late = self.collector("willlate")
        late.subscribe(f"{self.PREFIX}availability")
        retained = late.get(10.0, f"{self.PREFIX}availability")
        assert retained is not None
        self.assertEqual((retained[1], retained[3]), (b"offline", True), "the last will must stay retained")

    def test_bridge_comes_back_after_the_broker_goes_down_and_up(self) -> None:
        self.start_bridge()
        before = self.collector("before")
        before.subscribe(f"{self.PREFIX}availability")
        self.wait_online(before)
        before.close()
        self.broker.stop()
        time.sleep(2.0)
        self.broker.start()
        # the broker keeps nothing across a restart: the bridge must reconnect on its own, subscribe again and announce itself again
        after = self.collector("after")
        after.subscribe(f"{self.PREFIX}availability")
        message = after.get(60.0, f"{self.PREFIX}availability")
        self.assertIsNotNone(message, "the bridge did not announce itself after the broker came back")
        assert message is not None
        self.assertEqual(message[1], b"online")
        after.subscribe(self.result_topic, 2)
        for qos in (0, 1, 2):
            with self.subTest(qos=qos):
                body = self.ask(after, qos)
                self.assertIsNotNone(body, f"no answer at QoS {qos} after the broker came back")
                self.assertTrue(self.answer_is_right(body), body)  # type: ignore[arg-type]
