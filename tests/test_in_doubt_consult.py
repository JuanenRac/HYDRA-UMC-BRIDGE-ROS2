# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - A repeated job key whose first attempt left no answer
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A repeat whose first attempt left no answer is answered with what the system reports now (switch off by default) in the three entries of the bridge, and never run again; no reading keeps it refused. Simulated readings, evidence=simulator."""

import json
import os
import unittest
from unittest import mock

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_to_dict
from hydra_umc_sdk.job_intake import IdempotencyStore

import test_ext_fleet as fleet_tests
import test_ext_motion as motion_tests
from hydra_umc_bridge_ros2.in_doubt import IN_DOUBT_CONSULT_ENVIRONMENT, in_doubt_report
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX, Ros2MqttBridge

JOB_TOPIC = f"{TOPIC_PREFIX}cmd/job"


class FakeNode:
    pass


def message(key="key-1"):
    return json.dumps(job_to_dict(BridgeJob("job-1", key, "orchestrator", JobPhase.LOAD, MachineState.IDLE, {}))).encode("utf-8")


def in_doubt_store(key="key-1"):
    store = IdempotencyStore()
    assert store.claim(key)  # the first attempt claimed the key and never stored an answer
    return store


def bridge(store, observed="RUNNING", **options):
    b = Ros2MqttBridge(FakeNode(), lambda: CellState.READY, store, **options)  # type: ignore[arg-type]
    if observed is not None:
        b.start_observing()
        b.observe(observed)
    return b


class SwitchTests(unittest.TestCase):
    def test_off_by_default_a_job_in_doubt_is_refused_as_before(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(IN_DOUBT_CONSULT_ENVIRONMENT, None)
            b = bridge(in_doubt_store())
        body = json.loads(b.handle_message(JOB_TOPIC, message())[0].payload)
        self.assertFalse(b.in_doubt_consult)
        self.assertFalse(body["allowed"])
        self.assertIn("already handled", body["reason"])
        self.assertNotIn("in_doubt", body)

    def test_the_environment_switch_turns_it_on(self):
        with mock.patch.dict(os.environ, {IN_DOUBT_CONSULT_ENVIRONMENT: "1"}):
            b = bridge(in_doubt_store())
        self.assertTrue(json.loads(b.handle_message(JOB_TOPIC, message())[0].payload)["in_doubt"])

    def test_any_other_value_of_the_switch_keeps_it_off(self):
        for value in ("0", "", "true", "yes"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {IN_DOUBT_CONSULT_ENVIRONMENT: value}):
                self.assertFalse(bridge(in_doubt_store()).in_doubt_consult)


class JobEntryTests(unittest.TestCase):
    def test_the_repeat_is_answered_with_the_state_the_machine_reports_and_is_not_run_again(self):
        body = json.loads(bridge(in_doubt_store(), in_doubt_consult=True).handle_message(JOB_TOPIC, message())[0].payload)
        self.assertEqual((body["allowed"], body["accepted"], body["in_doubt"], body["machine_state"]), (False, False, True, "RUNNING"))
        self.assertIn("RUNNING", body["reason"])

    def test_an_idle_machine_does_not_turn_a_repeat_into_a_new_job(self):
        body = json.loads(bridge(in_doubt_store(), observed="IDLE", in_doubt_consult=True).handle_message(JOB_TOPIC, message())[0].payload)
        self.assertFalse(body["allowed"])
        self.assertTrue(body["in_doubt"])

    def test_a_machine_never_heard_leaves_the_repeat_refused(self):
        body = json.loads(bridge(in_doubt_store(), observed=None, in_doubt_consult=True).handle_message(JOB_TOPIC, message())[0].payload)
        self.assertFalse(body["allowed"])
        self.assertNotIn("in_doubt", body)

    def test_a_state_too_old_to_count_leaves_the_repeat_refused(self):
        now = [0.0]
        b = bridge(in_doubt_store(), observed=None, in_doubt_consult=True, clock=lambda: now[0], observed_max_age_seconds=2.0)
        b.start_observing()
        b.observe("RUNNING")
        now[0] = 60.0
        body = json.loads(b.handle_message(JOB_TOPIC, message())[0].payload)
        self.assertNotIn("in_doubt", body)

    def test_the_answer_is_stored_so_a_third_repeat_gets_the_same_one(self):
        b = bridge(in_doubt_store(), observed="HOLDING", in_doubt_consult=True)
        first = b.handle_message(JOB_TOPIC, message())[0]
        b.observe("IDLE")
        third = b.handle_message(JOB_TOPIC, message())[0]
        self.assertEqual(third.payload, first.payload)
        self.assertEqual(json.loads(first.payload)["machine_state"], "HOLDING")

    def test_a_key_with_a_stored_answer_gets_that_answer_not_a_reading(self):
        b = bridge(IdempotencyStore(), observed="IDLE", in_doubt_consult=True)
        first = b.handle_message(JOB_TOPIC, message())[0]
        second = b.handle_message(JOB_TOPIC, message())[0]
        self.assertEqual(second.payload, first.payload)
        self.assertNotIn("in_doubt", json.loads(second.payload))

    def test_a_new_key_is_decided_normally_with_the_switch_on(self):
        body = json.loads(bridge(in_doubt_store("other"), observed="IDLE", in_doubt_consult=True).handle_message(JOB_TOPIC, message("key-2"))[0].payload)
        self.assertTrue(body["accepted"])

    def test_without_a_store_nothing_is_in_doubt(self):
        body = json.loads(bridge(None, observed="IDLE", in_doubt_consult=True).handle_message(JOB_TOPIC, message())[0].payload)
        self.assertTrue(body["accepted"])


class ReportTests(unittest.TestCase):
    JOB = BridgeJob("job-1", "key-1", "orchestrator", JobPhase.LOAD, MachineState.IDLE, {})

    def test_no_state_and_no_other_reading_is_no_report(self):
        self.assertIsNone(in_doubt_report(self.JOB, None))

    def test_a_motion_alone_is_a_report(self):
        body = json.loads(in_doubt_report(self.JOB, None, reading=True, goal_running=True))
        self.assertTrue(body["goal_running"])
        self.assertNotIn("machine_state", body)
        self.assertIn("not run again", body["reason"])


class FleetEntryTests(unittest.TestCase):
    def rig(self, consult=True):
        with mock.patch.dict(os.environ, {IN_DOUBT_CONSULT_ENVIRONMENT: "1" if consult else "0"}):
            rig = fleet_tests.Rig(self)
        assert rig.extension.gateway is not None
        return rig

    def doubt(self, rig, key="k1"):
        assert rig.extension.gateway._store.claim(key)

    def test_off_by_default_the_repeat_is_refused_as_before(self):
        rig = self.rig(consult=False)
        rig.amr("RUNNING")
        self.doubt(rig)
        answer, forwards = rig.command(fleet_tests.signed(fleet_tests.job("k1")))
        self.assertFalse(answer["allowed"])
        self.assertNotIn("in_doubt", answer)
        self.assertEqual(forwards, [])

    def test_an_amr_repeat_is_answered_with_the_state_the_amr_bridge_published_and_nothing_is_forwarded(self):
        rig = self.rig()
        rig.amr("RUNNING")
        self.doubt(rig)
        answer, forwards = rig.command(fleet_tests.signed(fleet_tests.job("k1")))
        self.assertEqual((answer["allowed"], answer["in_doubt"], answer["forwarded"], answer["vehicle"], answer["machine_state"]), (False, True, False, "amr_1", "RUNNING"))
        self.assertEqual(forwards, [])

    def test_an_amr_never_heard_leaves_the_repeat_refused(self):
        rig = self.rig()
        self.doubt(rig)
        answer, forwards = rig.command(fleet_tests.signed(fleet_tests.job("k1")))
        self.assertFalse(answer["allowed"])
        self.assertNotIn("in_doubt", answer)
        self.assertEqual(forwards, [])

    def test_an_amr_whose_last_state_was_too_old_is_reported_as_offline_as_the_fleet_entry_holds_it(self):
        rig = self.rig()
        rig.amr("IDLE", 60_000)
        self.doubt(rig)
        answer, forwards = rig.command(fleet_tests.signed(fleet_tests.job("k1")))
        self.assertEqual((answer["in_doubt"], answer["machine_state"]), (True, "OFFLINE"))
        self.assertEqual(forwards, [])

    def test_a_ros_vehicle_repeat_is_answered_with_the_state_the_bridge_observed(self):
        rig = self.rig()
        rig.bridge.observe("HOLDING")
        self.doubt(rig)
        answer, forwards = rig.command(fleet_tests.signed(fleet_tests.job("k1", vehicle="arm_1", station=None)))
        self.assertEqual((answer["in_doubt"], answer["vehicle"], answer["machine_state"]), (True, "arm_1", "HOLDING"))
        self.assertEqual(forwards, [])

    def test_a_vehicle_that_is_not_in_the_list_leaves_the_repeat_refused(self):
        rig = self.rig()
        rig.amr("IDLE")
        self.doubt(rig)
        answer, forwards = rig.command(fleet_tests.signed(fleet_tests.job("k1", vehicle="ghost")))
        self.assertNotIn("in_doubt", answer)
        self.assertEqual(forwards, [])


class MotionEntryTests(unittest.TestCase):
    def rig(self, consult=True):
        with mock.patch.dict(os.environ, {IN_DOUBT_CONSULT_ENVIRONMENT: "1" if consult else "0"}):
            rig = motion_tests.Rig(self)
        assert rig.extension.gateway is not None
        return rig

    def test_off_by_default_the_repeat_is_refused_as_before(self):
        rig = self.rig(consult=False)
        assert rig.extension.gateway._store.claim("k-1")
        answer = rig.send(motion_tests.NAV_TOPIC, motion_tests.signed(motion_tests.NAV_TOPIC, motion_tests.make_job(key="k-1")))
        self.assertFalse(answer["allowed"])
        self.assertNotIn("in_doubt", answer)
        self.assertEqual(rig.nav.sent, [])

    def test_the_repeat_is_answered_with_the_state_and_no_goal_is_sent(self):
        rig = self.rig()
        assert rig.extension.gateway._store.claim("k-1")
        answer = rig.send(motion_tests.NAV_TOPIC, motion_tests.signed(motion_tests.NAV_TOPIC, motion_tests.make_job(key="k-1")))
        self.assertEqual((answer["allowed"], answer["in_doubt"], answer["forwarded"], answer["goal_running"], answer["machine_state"]), (False, True, False, False, "IDLE"))
        self.assertEqual(answer["goal_status"], "refused")
        self.assertEqual(rig.nav.sent, [])

    def test_a_goal_that_is_still_running_for_that_job_is_said_so_and_not_sent_again(self):
        rig = self.rig()
        rig.extension.gateway._store.remember = lambda *args, **keywords: None  # the bridge stops after sending the goal: no answer is stored
        first = rig.send(motion_tests.NAV_TOPIC, motion_tests.signed(motion_tests.NAV_TOPIC, motion_tests.make_job(key="k-1")))
        self.assertEqual(first["goal_status"], "sent")
        answer = rig.send(motion_tests.NAV_TOPIC, motion_tests.signed(motion_tests.NAV_TOPIC, motion_tests.make_job(key="k-1")))
        self.assertTrue(answer["in_doubt"])
        self.assertTrue(answer["goal_running"])
        self.assertEqual(len(rig.nav.sent), 1, "no second goal")

    def test_nothing_heard_and_nothing_running_leaves_the_repeat_refused(self):
        rig = self.rig()
        rig.bridge._observed = None
        assert rig.extension.gateway._store.claim("k-1")
        answer = rig.send(motion_tests.NAV_TOPIC, motion_tests.signed(motion_tests.NAV_TOPIC, motion_tests.make_job(key="k-1")))
        self.assertFalse(answer["allowed"])
        self.assertNotIn("in_doubt", answer)


if __name__ == "__main__":
    unittest.main()
