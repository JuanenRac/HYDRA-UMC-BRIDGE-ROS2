# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - A loop that survives its input, a node that is really spun, a state that goes stale
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Anybody on the broker can send anything, ROS 2 only delivers while a node is spun, and a publisher can die: none of it may end the loop or be believed for ever."""

import json
import sys
import threading
import types
import unittest
from unittest import mock

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict
from hydra_umc_bridge_ros2 import (
    MessageDispatcher,
    Ros2MqttBridge,
    Ros2SafeStopClient,
    SubscribeResult,
    bridge_state_to_mqtt,
    run_forever,
    spin_node_in_background,
)
from hydra_umc_bridge_ros2.mqtt_transport import AVAILABILITY_TOPIC, MAX_PAYLOAD_BYTES, TOPIC_PREFIX, state_payload


class FakeNode:
    pass


def message(phase=JobPhase.LOAD, claimed=MachineState.IDLE, key="key-1"):
    return json.dumps(job_to_dict(BridgeJob("job-1", key, "orchestrator", phase, claimed, {}))).encode("utf-8")


def bridge(cell=CellState.READY, observed="IDLE", **kwargs):
    # a bridge that has heard the state topic: without a reading of the machine every productive job is refused
    b = Ros2MqttBridge(FakeNode(), lambda: cell, **kwargs)  # type: ignore[arg-type]
    if observed is not None:
        b.start_observing()
        b.observe(observed)
    return b


def result(publishes):
    return json.loads(publishes[0].payload)


class NothingEndsTheLoopTests(unittest.TestCase):
    def test_a_deeply_nested_json_is_refused_not_raised(self):
        # ~60 kB of "[" used to end loop_forever() with a RecursionError.
        publishes = bridge().handle_message(f"{TOPIC_PREFIX}cmd/job", b"[" * 60000)
        self.assertEqual(publishes[0].topic, f"{TOPIC_PREFIX}cmd/job/result")
        self.assertFalse(result(publishes)["allowed"])

    def test_an_oversized_payload_is_refused_without_being_parsed(self):
        publishes = bridge().handle_message(f"{TOPIC_PREFIX}cmd/job", b" " * (MAX_PAYLOAD_BYTES + 1))
        self.assertFalse(result(publishes)["allowed"])
        self.assertIn("larger than", result(publishes)["reason"])

    def test_an_unexpected_failure_while_handling_a_job_is_an_answer_not_an_exception(self):
        b = bridge()
        with mock.patch.object(b._coordinator, "dispatch", side_effect=KeyError("boom")):
            publishes = b.handle_message(f"{TOPIC_PREFIX}cmd/job", message())
        self.assertFalse(result(publishes)["allowed"])
        self.assertIn("KeyError", result(publishes)["reason"])

    def test_a_failure_in_the_safe_stop_call_is_an_answer_not_an_exception(self):
        b = bridge()
        with mock.patch.object(b._safe_stop, "call", side_effect=RuntimeError("rclpy context is down")):
            publishes = b.handle_message(f"{TOPIC_PREFIX}cmd/safe_stop", b"")
        self.assertEqual(publishes[0].topic, f"{TOPIC_PREFIX}cmd/safe_stop/result")
        self.assertFalse(result(publishes)["allowed"])


class JobAnswerShapeTests(unittest.TestCase):
    def test_a_job_answer_carries_allowed_like_every_bridge_and_accepted_as_before(self):
        accepted = result(bridge().handle_message(f"{TOPIC_PREFIX}cmd/job", message()))
        self.assertTrue(accepted["allowed"])
        self.assertTrue(accepted["accepted"])
        refused = result(bridge(cell=CellState.FAULT).handle_message(f"{TOPIC_PREFIX}cmd/job", message()))
        self.assertFalse(refused["allowed"])
        self.assertFalse(refused["accepted"])

    def test_an_abort_answer_says_nothing_was_sent_and_names_the_real_stop_command(self):
        answer = result(bridge().handle_message(f"{TOPIC_PREFIX}cmd/job", message(JobPhase.ABORT)))
        self.assertTrue(answer["allowed"])
        self.assertEqual(answer["mode"], "plan-only")
        self.assertIn("sent nothing", answer["reason"])
        self.assertIn("cmd/safe_stop", answer["reason"])
        self.assertNotIn("forwarded to the authorised safety path", answer["reason"])


class StaleStateTests(unittest.TestCase):
    def test_a_state_older_than_the_limit_counts_as_offline(self):
        now = [100.0]
        b = bridge(observed_max_age_seconds=2.0, clock=lambda: now[0])
        b.start_observing()
        b.observe("IDLE")
        self.assertTrue(result(b.handle_message(f"{TOPIC_PREFIX}cmd/job", message()))["allowed"])
        now[0] += 2.5   # the publisher stopped republishing
        stale = result(b.handle_message(f"{TOPIC_PREFIX}cmd/job", message()))
        self.assertFalse(stale["allowed"])
        self.assertIn("OFFLINE", stale["reason"])
        b.observe("IDLE")   # it is heard again
        self.assertTrue(result(b.handle_message(f"{TOPIC_PREFIX}cmd/job", message()))["allowed"])

    def test_without_a_limit_the_last_state_is_kept(self):
        now = [100.0]
        b = bridge(clock=lambda: now[0])
        b.start_observing()
        b.observe("IDLE")
        now[0] += 10_000
        self.assertTrue(result(b.handle_message(f"{TOPIC_PREFIX}cmd/job", message()))["allowed"])


class StatePayloadTests(unittest.TestCase):
    def test_the_republished_state_is_normalized_and_stamped(self):
        payload = json.loads(state_payload(" idle ", 1234))
        self.assertEqual(payload, {"state": " idle ", "machine_state": "IDLE", "observed_at": 1234})

    def test_a_string_that_is_no_machine_state_is_published_as_such(self):
        self.assertIsNone(json.loads(state_payload("whatever", 1))["machine_state"])

    def test_the_subscription_republishes_each_message_retained_and_tells_if_it_subscribed(self):
        callbacks = []

        class Node:
            def create_subscription(self, msg_type, topic, callback, qos):
                callbacks.append(callback)
                return object()

        modules = {
            "std_msgs": types.ModuleType("std_msgs"),
            "std_msgs.msg": types.SimpleNamespace(String=object),
            "rclpy": types.ModuleType("rclpy"),
            "rclpy.qos": types.SimpleNamespace(
                DurabilityPolicy=types.SimpleNamespace(TRANSIENT_LOCAL=1), QoSProfile=lambda **kw: kw
            ),
        }
        published, observed = [], []
        with mock.patch.dict(sys.modules, modules):
            subscribed = bridge_state_to_mqtt(
                Node(), lambda *args: published.append(args), observed.append, clock=lambda: 5.0
            )
        self.assertTrue(subscribed.subscribed)
        callbacks[0](types.SimpleNamespace(data="RUNNING"))
        self.assertEqual(observed, ["RUNNING"])
        topic, payload, retain = published[0]
        self.assertEqual((topic, retain), (f"{TOPIC_PREFIX}state", True))
        self.assertEqual(json.loads(payload), {"state": "RUNNING", "machine_state": "RUNNING", "observed_at": 5000})

    def test_without_ros2_message_packages_it_reports_that_it_did_not_subscribe(self):
        if "rclpy" in sys.modules or "std_msgs" in sys.modules:
            self.skipTest("ROS 2 packages are importable here")
        subscribed = bridge_state_to_mqtt(FakeNode(), lambda *args: None)  # type: ignore[arg-type]
        self.assertFalse(subscribed.subscribed)


class _Future:
    def __init__(self, done=True, response=None):
        self._done = done
        self._response = response

    def done(self):
        return self._done

    def result(self):
        return self._response

    def exception(self):
        return None


class _Client:
    def __init__(self, future):
        self.future = future
        self.waits = 0
        self.calls = 0

    def wait_for_service(self, timeout_sec=None):
        self.waits += 1
        return True

    def call_async(self, request):
        self.calls += 1
        return self.future


class _ServiceNode:
    def __init__(self, client):
        self.client = client
        self.created = 0

    def create_client(self, srv_type, srv_name):
        self.created += 1
        return self.client


def _ros_modules(spin):
    class Trigger:
        class Request:
            pass

    rclpy = types.ModuleType("rclpy")
    rclpy.spin_until_future_complete = spin
    srv = types.ModuleType("std_srvs.srv")
    srv.Trigger = Trigger
    return {"rclpy": rclpy, "std_srvs": types.ModuleType("std_srvs"), "std_srvs.srv": srv}


class SafeStopClientTests(unittest.TestCase):
    def test_the_service_client_is_created_once_and_reused(self):
        node = _ServiceNode(_Client(_Future(response=types.SimpleNamespace(success=True, message="stopped"))))
        client = Ros2SafeStopClient()
        with mock.patch.dict(sys.modules, _ros_modules(lambda *a, **k: None)):
            for _ in range(3):
                self.assertTrue(client.call(node, "/hydra_umc/request_safe_stop").success)
        self.assertEqual(node.created, 1)
        self.assertEqual(node.client.calls, 3)

    def test_a_ready_service_is_not_waited_for(self):
        node = _ServiceNode(_Client(_Future(response=types.SimpleNamespace(success=True, message="ok"))))
        node.client.service_is_ready = lambda: True
        with mock.patch.dict(sys.modules, _ros_modules(lambda *a, **k: None)):
            Ros2SafeStopClient().call(node, "/hydra_umc/request_safe_stop")
        self.assertEqual(node.client.waits, 0)

    def test_a_node_that_is_already_spun_is_not_spun_a_second_time(self):
        def spin(*args, **kwargs):
            raise AssertionError("two executors on one node")

        node = _ServiceNode(_Client(_Future(response=types.SimpleNamespace(success=True, message="stopped"))))
        with mock.patch.dict(sys.modules, _ros_modules(spin)):
            answer = Ros2SafeStopClient(node_is_spinning=True).call(node, "/hydra_umc/request_safe_stop")
        self.assertTrue(answer.success)

    def test_a_spun_node_whose_service_never_answers_times_out(self):
        node = _ServiceNode(_Client(_Future(done=False)))
        with mock.patch.dict(sys.modules, _ros_modules(lambda *a, **k: None)):
            answer = Ros2SafeStopClient(node_is_spinning=True).call(node, "/hydra_umc/request_safe_stop", timeout_sec=0.05)
        self.assertTrue(answer.called)
        self.assertFalse(answer.success)
        self.assertIn("timed out", answer.reason)

    def test_a_node_that_cannot_make_a_client_gives_an_answer_not_an_exception(self):
        class BrokenNode:
            def create_client(self, srv_type, srv_name):
                raise RuntimeError("the rclpy context is not valid")

        with mock.patch.dict(sys.modules, _ros_modules(lambda *a, **k: None)):
            answer = Ros2SafeStopClient().call(BrokenNode(), "/hydra_umc/request_safe_stop")
        self.assertFalse(answer.success)
        self.assertIn("not valid", answer.reason)


class SpinnerTests(unittest.TestCase):
    def test_the_node_is_spun_on_its_own_thread_and_stopped_cleanly(self):
        spun = threading.Event()
        released = threading.Event()

        class Executor:
            def add_node(self, node):
                self.node = node

            def spin(self):
                spun.set()
                released.wait(2)

            def shutdown(self):
                released.set()

        executors = types.ModuleType("rclpy.executors")
        executors.SingleThreadedExecutor = Executor
        modules = {"rclpy": types.ModuleType("rclpy"), "rclpy.executors": executors}
        with mock.patch.dict(sys.modules, modules):
            spinner = spin_node_in_background(FakeNode())  # type: ignore[arg-type]
        self.assertTrue(spun.wait(2))
        spinner.stop()
        self.assertTrue(released.is_set())

    def test_without_rclpy_it_says_so(self):
        if "rclpy" in sys.modules:
            self.skipTest("rclpy is importable here")
        with self.assertRaises(RuntimeError):
            spin_node_in_background(FakeNode())  # type: ignore[arg-type]


class _PahoClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.will = None
        self.published = []
        _PahoClient.instances.append(self)

    def username_pw_set(self, *args, **kwargs):
        pass

    def tls_set(self, *args, **kwargs):
        pass

    def connect(self, *args, **kwargs):
        pass

    def subscribe(self, *args, **kwargs):
        pass

    def reconnect_delay_set(self, *args, **kwargs):
        pass

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))

    def loop_forever(self):
        return None


def _paho():
    client_module = types.ModuleType("paho.mqtt.client")
    client_module.Client = _PahoClient
    client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
    mqtt_package = types.ModuleType("paho.mqtt")
    mqtt_package.client = client_module
    paho = types.ModuleType("paho")
    paho.mqtt = mqtt_package
    return {"paho": paho, "paho.mqtt": mqtt_package, "paho.mqtt.client": client_module}


class RunForeverTests(unittest.TestCase):
    def setUp(self):
        _PahoClient.instances.clear()

    def test_it_leaves_a_last_will_and_announces_itself_when_connected(self):
        with mock.patch.dict(sys.modules, _paho()), mock.patch(
            "hydra_umc_bridge_ros2.mqtt_transport.spin_node_in_background"
        ), mock.patch(
            "hydra_umc_bridge_ros2.mqtt_transport.bridge_state_to_mqtt", return_value=SubscribeResult(True, "subscribed")
        ):
            run_forever(mock.MagicMock(), "127.0.0.1")
        client = _PahoClient.instances[-1]
        self.assertEqual(client.will, (AVAILABILITY_TOPIC, "offline", 1, True))
        client.on_connect(client, None, None, types.SimpleNamespace(is_failure=False))
        self.assertIn((AVAILABILITY_TOPIC, "online", True), client.published)

    def test_it_refuses_to_start_when_the_state_topic_cannot_be_observed(self):
        with mock.patch.dict(sys.modules, _paho()), mock.patch(
            "hydra_umc_bridge_ros2.mqtt_transport.spin_node_in_background"
        ), mock.patch(
            "hydra_umc_bridge_ros2.mqtt_transport.bridge_state_to_mqtt",
            return_value=SubscribeResult(False, "std_msgs/rclpy.qos is not available"),
        ):
            with self.assertRaises(RuntimeError) as context:
                run_forever(mock.MagicMock(), "127.0.0.1")
        self.assertIn("cannot be observed", str(context.exception))


class DispatcherTests(unittest.TestCase):
    def test_a_stop_does_not_wait_behind_a_slow_job(self):
        slow_job_started = threading.Event()
        release = threading.Event()
        answered = threading.Event()

        class Bridge:
            def handle_message(self, topic, payload, retained=False):
                if topic.endswith("cmd/job"):
                    slow_job_started.set()
                    release.wait(5)
                else:
                    answered.set()
                return []

        dispatcher = MessageDispatcher(Bridge())  # type: ignore[arg-type]
        client = mock.MagicMock()
        try:
            dispatcher.on_message(client, None, types.SimpleNamespace(topic=f"{TOPIC_PREFIX}cmd/job", payload=b"{}", retain=False))
            self.assertTrue(slow_job_started.wait(2))
            dispatcher.on_message(client, None, types.SimpleNamespace(topic=f"{TOPIC_PREFIX}cmd/safe_stop", payload=b"", retain=False))
            self.assertTrue(answered.wait(2), "the stop was queued behind the slow job")
        finally:
            release.set()
            dispatcher.close()

    def test_a_retained_message_is_dropped_before_it_is_queued(self):
        bridge_double = mock.MagicMock()
        dispatcher = MessageDispatcher(bridge_double)
        dispatcher.on_message(mock.MagicMock(), None, types.SimpleNamespace(topic=f"{TOPIC_PREFIX}cmd/job", payload=b"{}", retain=True))
        dispatcher.close()
        bridge_double.handle_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
