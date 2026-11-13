# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Tests of the fleet extension (one entry for ROS 2 vehicles and VDA 5050 AMRs)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The mixed-fleet entry without ROS 2: the chain every command passes, the signed job towards an AMR bridge, the state that gates it, the relay of its answer, the fleet-wide abort."""

import json
import os
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState, job_from_dict, job_to_dict
from hydra_umc_sdk.global_stop import GlobalStopGuard, GlobalStopSettings
from hydra_umc_sdk.signed_commands import sign_command, verify_command

from hydra_umc_bridge_ros2 import Ros2MqttBridge, ext_fleet
from hydra_umc_bridge_ros2.extensions import ExtensionConfigError, build_extensions, parse_sections
from hydra_umc_bridge_ros2.ext_fleet import JOB_VERB, RESULT_TOPIC, STATE_TOPIC, FleetSettings, Vehicle, build, load_fleet, machine_from_state, parse
from hydra_umc_bridge_ros2.mqtt_transport import TOPIC_PREFIX
from hydra_umc_bridge_ros2.rclpy_transport import SafeStopResult

KEY = b"0123456789abcdef0123456789abcdef-fleet-in"
AMR_KEY = b"fedcba9876543210fedcba9876543210-amr-out"
COMMAND = f"{TOPIC_PREFIX}{JOB_VERB}"
AMR = "hydra/bridges/amr/"
AMR_JOB = f"{AMR}cmd/job"
READY = b'{"state": "READY"}'
STOPPED = b'{"state": "SAFE_STOP"}'


class FakeSafeStop:
    def __init__(self) -> None:
        self.calls = 0

    def call(self, node, name, *, timeout_sec=2.0):
        self.calls += 1
        return SafeStopResult(True, True, "stopped")


class Files:
    def __init__(self, testcase: unittest.TestCase, fleet: dict | None = None) -> None:
        folder = tempfile.TemporaryDirectory()
        testcase.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.key = str(root / "in.key")
        self.amr_key = str(root / "amr.key")
        for path, content in ((self.key, KEY), (self.amr_key, AMR_KEY)):
            Path(path).write_bytes(content)
            os.chmod(path, 0o600)
        self.fleet = str(root / "fleet.json")
        default = {"vehicles": {"amr_1": {"kind": "vda5050", "stations": {"dock_a": "N12", "home": "N1"}}, "arm_1": {"kind": "ros2"}}}
        Path(self.fleet).write_text(json.dumps(fleet or default), encoding="utf-8")

    def settings(self, **keywords) -> FleetSettings:
        values = dict(enabled=True, fleet_file=self.fleet, command_key_file=self.key, amr_command_key_file=self.amr_key, state_interval_s=3600.0)
        values.update(keywords)
        return parse(values)


def job(key="k1", phase=JobPhase.PROCESS, vehicle: str | None = "amr_1", station: str | None = "dock_a", job_id=None) -> BridgeJob:
    parameters = {}
    if vehicle is not None:
        parameters["vehicle"] = vehicle
    if station is not None:
        parameters["station"] = station
    return BridgeJob(job_id or f"job-{key}", key, "orchestrator", phase, MachineState.IDLE, parameters)


def signed(item: BridgeJob, key: bytes = KEY, topic: str = COMMAND, **keywords) -> bytes:
    return json.dumps(sign_command(topic, job_to_dict(item), key, **keywords)).encode()


def amr_state(machine="IDLE", age_ms=0) -> bytes:
    return json.dumps({"observed_at": int(time.time() * 1000) - age_ms, "machine_state": machine}).encode()


class Rig:
    def __init__(self, testcase: unittest.TestCase, fleet: dict | None = None, global_stop=None, **keywords) -> None:
        self.files = Files(testcase, fleet)
        self.guard = global_stop or GlobalStopGuard(GlobalStopSettings(True))
        self.guard.observe(READY)
        self.safe_stop = FakeSafeStop()
        self.cell = CellState.READY
        self.bridge = Ros2MqttBridge(SimpleNamespace(), lambda: self.cell, global_stop=self.guard, safe_stop_client=self.safe_stop)  # type: ignore[arg-type]
        self.bridge.start_observing()
        self.bridge.observe("IDLE")
        self.published: list[tuple[str, dict, bool]] = []
        extension = build(self.files.settings(**keywords), self.bridge)
        assert extension is not None
        self.extension = extension
        self.bridge.extensions.add(extension)
        extension.start(SimpleNamespace(), lambda topic, payload, retain: self.published.append((topic, json.loads(payload), retain)))
        testcase.addCleanup(extension.stop)

    def amr(self, machine="IDLE", age_ms=0) -> None:
        self.bridge.handle_message(f"{AMR}state", amr_state(machine, age_ms), retained=True)

    def command(self, payload: bytes):
        out = self.bridge.handle_message(COMMAND, payload)
        return json.loads(out[0].payload), out[1:]


class ParseTests(unittest.TestCase):
    def test_off_by_default_and_a_disabled_section_asks_for_nothing(self):
        self.assertFalse(parse(None).enabled)

    def test_an_enabled_section_needs_its_files_and_both_keys(self):
        files = Files(self)
        base = {"enabled": True, "fleet_file": files.fleet, "command_key_file": files.key, "amr_command_key_file": files.amr_key}
        parse(base)
        for change, word in (
            ({"fleet_file": ""}, "fleet_file"),
            ({"fleet_file": "/no/such.json"}, "cannot read"),
            ({"command_key_file": ""}, "signed"),
            ({"amr_command_key_file": ""}, "amr_command_key_file"),
            ({"command_max_skew_ms": -1}, "milliseconds"),
            ({"command_max_expires_ms": 0}, "milliseconds"),
            ({"amr_state_max_age_s": 0}, "amr_state_max_age_s"),
            ({"state_interval_s": 99999}, "state_interval_s"),
            ({"state_interval_s": float("nan")}, "state_interval_s"),
        ):
            with self.subTest(change=change), self.assertRaises(ExtensionConfigError) as context:
                parse({**base, **change})
            self.assertIn(word, str(context.exception))

    def test_a_fleet_of_ros2_vehicles_needs_no_amr_key(self):
        files = Files(self, {"vehicles": {"arm_1": {"kind": "ros2"}}})
        parse({"enabled": True, "fleet_file": files.fleet, "command_key_file": files.key})

    def test_the_fleet_file_is_checked_one_vehicle_at_a_time(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        good = {"kind": "vda5050", "stations": {"a": "N1"}}
        cases = (
            [],
            {"vehicles": {}},
            {"vehicles": {"bad name": {"kind": "ros2"}}},
            {"vehicles": {"x": {"kind": "drone"}}},
            {"vehicles": {"x": "ros2"}},
            {"vehicles": {"x": {"kind": "ros2", "stations": {}}}},
            {"vehicles": {"x": {"kind": "ros2", "topic_prefix": "a/"}}},
            {"vehicles": {"x": {"kind": "vda5050", "speed": 3}}},
            {"vehicles": {"x": {"kind": "vda5050", "stations": []}}},
            {"vehicles": {"x": {"kind": "vda5050", "stations": {"a b": "N1"}}}},
            {"vehicles": {"x": {"kind": "vda5050", "stations": {"a": 5}}}},
            {"vehicles": {"x": {"kind": "vda5050", "stations": {"a": "bad node!"}}}},
            {"vehicles": {"x": {"kind": "vda5050", "topic_prefix": "hydra/#/"}}},
            {"vehicles": {"x": {"kind": "vda5050", "topic_prefix": "hydra/amr"}}},
            {"vehicles": {"x": good, "y": good}},
            {"vehicles": {f"v{index}": {"kind": "ros2"} for index in range(65)}},
            {"vehicles": {"x": {"kind": "vda5050", "stations": {f"s{index}": "N" for index in range(300)}}}},
        )
        for index, body in enumerate(cases):
            path = Path(folder.name, f"f{index}.json")
            path.write_text(json.dumps(body), encoding="utf-8")
            with self.subTest(body=str(body)[:70]), self.assertRaises(ExtensionConfigError):
                load_fleet(str(path))
        path = Path(folder.name, "broken.json")
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(ExtensionConfigError):
            load_fleet(str(path))

    def test_two_amr_bridges_can_have_their_own_prefixes(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name, "fleet.json")
        path.write_text(json.dumps({"vehicles": {"a": {"kind": "vda5050"}, "b": {"kind": "vda5050", "topic_prefix": "hydra/bridges/amr2/"}}}), encoding="utf-8")
        fleet = load_fleet(str(path))
        self.assertEqual((fleet["a"].topic_prefix, fleet["b"].topic_prefix), (AMR, "hydra/bridges/amr2/"))

    def test_the_service_reads_the_section(self):
        files = Files(self)
        parsed = parse_sections({"fleet": {"enabled": True, "fleet_file": files.fleet, "command_key_file": files.key, "amr_command_key_file": files.amr_key}})
        self.assertTrue(parsed["fleet"].enabled)  # type: ignore[attr-defined]


class BuildTests(unittest.TestCase):
    def bridge(self, enabled=True):
        return Ros2MqttBridge(SimpleNamespace(), lambda: CellState.READY, global_stop=GlobalStopSettings(enabled))  # type: ignore[arg-type]

    def test_nothing_is_built_while_it_is_off(self):
        bridge = self.bridge()
        build_extensions(parse_sections({"fleet": {}}), bridge)
        self.assertEqual(len(bridge.extensions), 0)

    def test_it_refuses_to_start_when_the_bridge_does_not_follow_the_global_stop(self):
        files = Files(self)
        with self.assertRaises(ExtensionConfigError) as context:
            build(files.settings(), self.bridge(False))
        self.assertIn("GLOBAL_STOP", str(context.exception))
        self.assertIsNotNone(build(files.settings(require_global_stop=False), self.bridge(False)))

    def test_it_refuses_to_start_with_an_unusable_key_and_never_prints_it(self):
        files = Files(self)
        Path(files.key).write_bytes(b"short")
        with self.assertRaises(ExtensionConfigError) as context:
            build(files.settings(), self.bridge())
        self.assertIn("key", str(context.exception))
        Path(files.key).write_bytes(KEY)
        Path(files.amr_key).write_bytes(b"short")
        with self.assertRaises(ExtensionConfigError):
            build(files.settings(), self.bridge())
        self.assertNotIn(AMR_KEY.decode(), str(context.exception))

    def test_a_missing_amr_key_stops_the_start_even_when_the_settings_were_not_parsed(self):
        files = Files(self)
        settings = FleetSettings(enabled=True, fleet_file=files.fleet, command_key_file=files.key)
        with self.assertRaises(ExtensionConfigError):
            build(settings, self.bridge())


class MachineTests(unittest.TestCase):
    def test_the_machine_state_of_a_state_message_fails_closed(self):
        now = time.time() * 1000
        fresh = lambda **kw: json.dumps({"observed_at": now, "machine_state": "IDLE", **kw}).encode()  # noqa: E731
        self.assertEqual(machine_from_state(fresh(), now, 15000), MachineState.IDLE)
        self.assertEqual(machine_from_state(fresh(machine_state="RUNNING"), now, 15000), MachineState.RUNNING)
        for bad in (
            fresh(machine_state="FLYING"),
            fresh(machine_state=None),
            json.dumps({"observed_at": now - 60000, "machine_state": "IDLE"}).encode(),
            json.dumps({"observed_at": now + 60000, "machine_state": "IDLE"}).encode(),
            json.dumps({"machine_state": "IDLE"}).encode(),
            json.dumps({"observed_at": "now", "machine_state": "IDLE"}).encode(),
            json.dumps({"observed_at": True, "machine_state": "IDLE"}).encode(),
            b"[1]",
            b"{broken",
            b"\xff",
            b"[" * 100000,
        ):
            with self.subTest(bad=bad[:30]):
                self.assertEqual(machine_from_state(bad, now, 15000), MachineState.OFFLINE)


class AmrForwardTests(unittest.TestCase):
    def test_a_movement_job_becomes_a_signed_job_for_the_amr_bridge_with_the_node_of_the_station(self):
        rig = Rig(self)
        rig.amr()
        answer, forwards = rig.command(signed(job()))
        self.assertTrue(answer["allowed"], answer)
        self.assertEqual((answer["vehicle"], answer["kind"], answer["mode"]), ("amr_1", "vda5050", "send"))
        self.assertEqual(len(forwards), 1)
        sent = forwards[0]
        self.assertEqual((sent.topic, sent.qos, sent.retain), (AMR_JOB, 1, False))
        body = json.loads(sent.payload)
        self.assertTrue(verify_command(AMR_JOB, body, AMR_KEY, now_ms=int(time.time() * 1000)).ok)
        forwarded = job_from_dict({name: value for name, value in body.items() if name not in ("issued_at_ms", "expires_in_ms", "signature")})
        self.assertEqual((forwarded.phase, forwarded.job_id, forwarded.idempotency_key, dict(forwarded.parameters)), (JobPhase.PROCESS, "job-k1", "amr_1:k1", {"goal_node": "N12"}))

    def test_a_job_that_is_not_a_movement_carries_no_node_and_needs_no_station(self):
        rig = Rig(self)
        rig.amr()
        answer, forwards = rig.command(signed(job("k2", JobPhase.LOAD, station=None)))
        self.assertTrue(answer["allowed"], answer)
        self.assertEqual(json.loads(forwards[0].payload)["parameters"], {})

    def test_the_station_must_be_in_the_list_of_the_vehicle_for_a_movement(self):
        rig = Rig(self)
        rig.amr()
        for index, station in enumerate(("elsewhere", None, "", "DOCK_A", "../x")):
            with self.subTest(station=station):
                answer, forwards = rig.command(signed(job(f"s{index}", station=station)))
                self.assertFalse(answer["allowed"])
                self.assertEqual(forwards, [])

    def test_the_state_the_amr_bridge_published_decides_not_the_one_written_in_the_job(self):
        rig = Rig(self)
        cases = (("never heard", None), ("stale", ("IDLE", 60_000)), ("running", ("RUNNING", 0)), ("fault", ("FAULT", 0)), ("unknown", ("BOGUS", 0)))
        for index, (name, state) in enumerate(cases):
            with self.subTest(name):
                if state is not None:
                    rig.amr(*state)
                answer, forwards = rig.command(signed(job(f"m{index}")))
                self.assertFalse(answer["allowed"], answer)
                self.assertEqual(forwards, [])
        rig.amr("IDLE")
        self.assertTrue(rig.command(signed(job("fresh")))[0]["allowed"])

    def test_a_state_that_ages_out_is_offline_again(self):
        rig = Rig(self)
        rig.amr()
        gateway = rig.extension.gateway
        assert gateway is not None
        gateway._clock = lambda: time.time() + 3600
        self.assertEqual(gateway.amr_machine(AMR), MachineState.OFFLINE)
        self.assertFalse(rig.command(signed(job("late")))[0]["allowed"])

    def test_a_cell_that_is_not_ready_sends_nothing(self):
        rig = Rig(self)
        rig.amr()
        rig.cell = CellState.INHIBITED
        answer, forwards = rig.command(signed(job()))
        self.assertIn("INHIBITED", answer["reason"])
        self.assertEqual(forwards, [])

    def test_an_unsigned_forged_altered_expired_or_future_command_forwards_nothing(self):
        rig = Rig(self)
        rig.amr()
        now = int(time.time() * 1000)
        good = json.loads(signed(job("g")))
        cases = {
            "unsigned": json.dumps(job_to_dict(job("a"))).encode(),
            "wrong key": signed(job("b"), key=b"x" * 40),
            "another topic": signed(job("c"), topic=AMR_JOB),
            "altered": json.dumps(dict(good, parameters={"vehicle": "amr_1", "station": "home"})).encode(),
            "expired": signed(job("d"), now_ms=now - 60_000, expires_in_ms=1000),
            "future": signed(job("e"), now_ms=now + 600_000),
            "too long": signed(job("f"), expires_in_ms=3_600_000),
            "not json": b"{no",
            "not an object": b"[1]",
        }
        for name, payload in cases.items():
            with self.subTest(name):
                answer, forwards = rig.command(payload)
                self.assertFalse(answer["allowed"], answer)
                self.assertEqual(forwards, [])

    def test_an_unknown_vehicle_or_none_is_refused_for_a_job(self):
        rig = Rig(self)
        rig.amr()
        for index, vehicle in enumerate(("ghost", None, "*", "AMR_1")):
            with self.subTest(vehicle=vehicle):
                answer, forwards = rig.command(signed(job(f"v{index}", vehicle=vehicle)))
                self.assertFalse(answer["allowed"])
                self.assertEqual(forwards, [])

    def test_a_repeated_key_gets_the_stored_answer_and_nothing_is_forwarded_twice(self):
        rig = Rig(self)
        rig.amr()
        first, forwards = rig.command(signed(job("same")))
        second, again = rig.command(signed(job("same")))
        self.assertEqual((second, len(forwards), again), (first, 1, []))


class RosVehicleTests(unittest.TestCase):
    def test_a_ros2_vehicle_is_served_by_the_job_entry_of_this_bridge(self):
        rig = Rig(self)
        answer, forwards = rig.command(signed(job("r1", vehicle="arm_1", station=None)))
        self.assertTrue(answer["allowed"], answer)
        self.assertEqual((answer["kind"], answer["mode"], forwards), ("ros2", "plan-only", []))

    def test_the_gate_of_the_bridge_applies_to_it(self):
        rig = Rig(self)
        rig.bridge.observe("RUNNING")
        answer, _ = rig.command(signed(job("r2", vehicle="arm_1", station=None)))
        self.assertFalse(answer["allowed"])
        self.assertIn("RUNNING", answer["reason"])

    def test_an_abort_for_it_calls_the_safe_stop(self):
        rig = Rig(self)
        answer, _ = rig.command(signed(job("r3", JobPhase.ABORT, vehicle="arm_1", station=None)))
        self.assertTrue(answer["allowed"], answer)
        self.assertEqual((answer["executed"], answer["mode"], rig.safe_stop.calls), (True, "send", 1))


class AbortTests(unittest.TestCase):
    def test_an_abort_for_every_vehicle_cancels_the_amr_and_stops_the_ros_side(self):
        for vehicle in ("*", None):
            with self.subTest(vehicle=vehicle):
                rig = Rig(self)
                rig.amr("RUNNING")  # an abort needs no IDLE vehicle
                answer, forwards = rig.command(signed(job("ab", JobPhase.ABORT, vehicle=vehicle, station=None)))
                self.assertTrue(answer["allowed"], answer)
                self.assertEqual(answer["vehicle"], "*")
                self.assertEqual(set(answer["vehicles"]), {"amr_1", "arm_1"})
                self.assertEqual(rig.safe_stop.calls, 1)
                self.assertEqual(len(forwards), 1)
                body = json.loads(forwards[0].payload)
                self.assertEqual((forwards[0].topic, body["phase"], body["parameters"]), (AMR_JOB, "ABORT", {}))
                self.assertTrue(verify_command(AMR_JOB, body, AMR_KEY, now_ms=int(time.time() * 1000)).ok)

    def test_an_abort_for_one_vehicle_touches_only_that_one(self):
        rig = Rig(self)
        _, forwards = rig.command(signed(job("a1", JobPhase.ABORT, vehicle="amr_1", station=None)))
        self.assertEqual((len(forwards), rig.safe_stop.calls), (1, 0))
        _, forwards = rig.command(signed(job("a2", JobPhase.ABORT, vehicle="arm_1", station=None)))
        self.assertEqual((len(forwards), rig.safe_stop.calls), (0, 1))
        answer, _ = rig.command(signed(job("a3", JobPhase.ABORT, vehicle="ghost", station=None)))
        self.assertFalse(answer["allowed"])

    def test_while_the_server_says_stop_a_job_is_refused_and_an_abort_goes_through(self):
        guard = GlobalStopGuard(GlobalStopSettings(True))
        rig = Rig(self, global_stop=guard)
        rig.amr()
        guard.observe(STOPPED)
        answer, forwards = rig.command(signed(job("g1")))
        self.assertFalse(answer["allowed"])
        self.assertIn("global stop", answer["reason"])
        self.assertEqual(forwards, [])
        answer, forwards = rig.command(signed(job("g2", JobPhase.ABORT, vehicle="*", station=None)))
        self.assertTrue(answer["allowed"])
        self.assertEqual(len(forwards), 1)
        guard.observe(READY)
        self.assertTrue(rig.command(signed(job("g3")))[0]["allowed"])


class RelayTests(unittest.TestCase):
    def result(self, rig, job_id, allowed=True, retained=False):
        payload = json.dumps({"allowed": allowed, "reason": "published", "job_id": job_id}).encode()
        return rig.bridge.handle_message(f"{AMR_JOB}/result", payload, retained=retained)

    def test_the_answer_of_the_amr_bridge_to_a_forwarded_job_is_relayed_once(self):
        rig = Rig(self)
        rig.amr()
        rig.command(signed(job("rel")))
        out = self.result(rig, "job-rel")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].topic, RESULT_TOPIC)
        self.assertEqual(json.loads(out[0].payload), {"vehicle": "amr_1", "job_id": "job-rel", "kind": "vda5050", "allowed": True, "reason": "published", "amr_answer": True})
        self.assertEqual(self.result(rig, "job-rel"), [])

    def test_an_answer_to_something_else_is_not_ours(self):
        rig = Rig(self)
        rig.amr()
        rig.command(signed(job("rel")))
        self.assertEqual(self.result(rig, "someone-elses-job"), [])
        self.assertEqual(self.result(rig, "job-rel", retained=True), [])
        for junk in (b"{broken", b"[1]", b"\xff", json.dumps({"job_id": 5}).encode(), b"[" * 100000):
            self.assertEqual(rig.bridge.handle_message(f"{AMR_JOB}/result", junk), [])

    def test_the_list_of_jobs_waiting_for_an_answer_is_bounded(self):
        rig = Rig(self)
        rig.amr()
        for index in range(80):
            rig.command(signed(job(f"p{index}")))
        gateway = rig.extension.gateway
        assert gateway is not None
        self.assertLessEqual(len(gateway._pending), ext_fleet.MAX_PENDING)
        self.assertEqual(self.result(rig, "job-p0"), [])  # the oldest was dropped
        self.assertEqual(len(self.result(rig, "job-p79")), 1)


class StateTests(unittest.TestCase):
    def test_the_state_of_the_fleet_is_published_retained_with_the_machine_of_each_vehicle(self):
        rig = Rig(self)
        rig.amr("HOLDING")
        rig.bridge.observe("RUNNING")
        self.assertTrue(rig.extension.tick())
        topic, body, retain = rig.published[-1]
        self.assertEqual((topic, retain), (STATE_TOPIC, True))
        self.assertEqual(body["vehicles"], {"amr_1": {"kind": "vda5050", "machine_state": "HOLDING"}, "arm_1": {"kind": "ros2", "machine_state": "RUNNING"}})

    def test_a_vehicle_that_was_never_heard_is_offline_not_missing(self):
        rig = Rig(self)
        rig.bridge._observed = None
        rig.extension.tick()
        self.assertEqual({name: item["machine_state"] for name, item in rig.published[-1][1]["vehicles"].items()}, {"amr_1": "OFFLINE", "arm_1": "OFFLINE"})

    def test_a_failing_publication_is_logged_and_the_stop_is_safe_to_repeat(self):
        rig = Rig(self)

        def broken(topic, payload, retain):
            raise OSError("link down")

        rig.extension._publish = broken
        with self.assertLogs(ext_fleet.__name__, "ERROR"):
            self.assertFalse(rig.extension.tick())
        rig.extension.stop()
        rig.extension.stop()
        self.assertFalse(rig.extension.tick())

    def test_the_thread_publishes_every_interval_until_stopped(self):
        rig = Rig(self, state_interval_s=0.01)
        deadline = time.monotonic() + 3
        while len(rig.published) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        rig.extension.stop()
        count = len(rig.published)
        time.sleep(0.05)
        self.assertGreaterEqual(count, 2)
        self.assertEqual(len(rig.published), count)


class TopicTests(unittest.TestCase):
    def test_what_it_claims_and_subscribes_to(self):
        rig = Rig(self)
        extension = rig.extension
        self.assertEqual(sorted(extension.subscriptions()), [(f"{AMR}cmd/job/result", 1), (f"{AMR}state", 1)])
        self.assertTrue(extension.claims(COMMAND) and not extension.inline(COMMAND))
        for topic in (f"{AMR}state", f"{AMR_JOB}/result"):
            self.assertTrue(extension.claims(topic) and extension.inline(topic))
        for topic in (AMR_JOB, f"{TOPIC_PREFIX}cmd/job", "hydra/bridges/amr2/state"):
            self.assertFalse(extension.claims(topic), topic)
        self.assertEqual((extension.job_verbs, extension.moving_verbs), (frozenset({JOB_VERB}), frozenset()))

    def test_a_messages_before_the_start_are_kept(self):
        files = Files(self)
        bridge = Ros2MqttBridge(SimpleNamespace(), lambda: CellState.READY, global_stop=GlobalStopSettings(True))  # type: ignore[arg-type]
        bridge.start_observing()
        extension = build(files.settings(), bridge)
        assert extension is not None
        bridge.extensions.add(extension)
        bridge.handle_message(f"{AMR}state", amr_state("IDLE"), retained=True)
        self.assertEqual(extension.gateway.amr_machine(AMR), MachineState.IDLE)  # type: ignore[union-attr]
        extension.stop()


class SimulatedAmrBridge:
    """What the AMR bridge does with a job on its `cmd/job`, with the real checks of the SDK: signature, then the gate on the state the vehicle reported."""

    def __init__(self, machine: MachineState) -> None:
        self.machine = machine
        self.accepted: list[BridgeJob] = []

    def receive(self, publish) -> None:
        body = json.loads(publish.payload)
        if not verify_command(publish.topic, body, AMR_KEY, now_ms=int(time.time() * 1000)).ok:
            return
        item = job_from_dict({name: value for name, value in body.items() if name not in ("issued_at_ms", "expires_in_ms", "signature")})
        if item.phase is JobPhase.ABORT or (self.machine is MachineState.IDLE and item.parameters.get("goal_node")):
            self.accepted.append(item)


class FuzzTests(unittest.TestCase):
    def test_a_seeded_stream_reaches_the_amr_bridge_only_when_every_condition_holds(self):
        rng = random.Random(20260)
        guard = GlobalStopGuard(GlobalStopSettings(True))
        rig = Rig(self, global_stop=guard)
        simulated = SimulatedAmrBridge(MachineState.IDLE)
        expected = 0
        for index in range(400):
            machine = rng.choice(["IDLE", "IDLE", "RUNNING", "FAULT"])
            age = rng.choice([0, 0, 60_000])
            rig.amr(machine, age)
            simulated.machine = MachineState(machine)
            stopped = rng.random() < 0.2
            guard.observe(STOPPED if stopped else READY)
            rig.cell = rng.choice([CellState.READY, CellState.READY, CellState.INHIBITED])
            valid = rng.random() < 0.7
            listed = rng.random() < 0.8
            vehicle = rng.choice(["amr_1", "amr_1", "ghost"])
            payload = signed(job(f"z{index}", vehicle=vehicle, station="dock_a" if listed else "nowhere"), KEY if valid else b"y" * 40)
            _, forwards = rig.command(payload)
            should = valid and machine == "IDLE" and age == 0 and not stopped and rig.cell is CellState.READY and listed and vehicle == "amr_1"
            self.assertEqual(len(forwards), 1 if should else 0, (index, valid, machine, age, stopped, rig.cell, listed, vehicle))
            for forward in forwards:
                simulated.receive(forward)
            expected += 1 if should else 0
        self.assertEqual(len(simulated.accepted), expected)
        self.assertGreater(expected, 5)


class VehicleTests(unittest.TestCase):
    def test_a_vehicle_is_a_plain_record(self):
        self.assertEqual(Vehicle("ros2"), Vehicle("ros2", (), AMR))


if __name__ == "__main__":
    unittest.main()
