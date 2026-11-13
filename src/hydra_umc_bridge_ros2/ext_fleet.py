# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Extension: one entry for a mixed fleet (ROS 2 vehicles and VDA 5050 AMRs)
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""One job topic, `hydra/bridges/ros2/cmd/fleet_job`, for a fleet that mixes ROS 2 vehicles with AGVs/AMRs that speak VDA 5050 through HYDRA-UMC-BRIDGE-AMR. This extension can move something. Off by default.

A job names a `vehicle` and, for a movement, a `station`; both are names that the operator wrote in a file (`fleet_file`). No coordinates and no node ids travel in a command.

* A `ros2` vehicle is served by this bridge itself: the job is handed to the `cmd/job` of the bridge (its gate, its idempotency, the safe-stop call of an `ABORT`).
* A `vda5050` vehicle is served by the AMR bridge: the extension gates the job with the machine state THAT bridge published (`<prefix>state`, `machine_state`; older than `amr_state_max_age_s`, missing or
  unknown is `OFFLINE`), signs a new job with the key of the AMR bridge and publishes it on `<prefix>cmd/job` at QoS 1. The AMR bridge then gates it again with what the vehicle itself reported, and its answer
  on `<prefix>cmd/job/result` is relayed on `cmd/fleet_job/result`. A movement phase (`PREPARE`, `PROCESS`, `COMPLETE`) carries the `goal_node` of the station; `LOAD` and `UNLOAD` carry nothing.
* An `ABORT` for one vehicle, or for `*` (every vehicle), is forwarded to each: a cancel order to the AMR bridge, the safe-stop call to the ROS 2 side.

What a command passes before anything is forwarded, in this order: the switch and a bridge that follows the global stop of the server (`require_global_stop`); a signature of the SDK signed commands (no switch
turns it off); the idempotency of the key; a known vehicle; for a `vda5050` vehicle the gate of the SDK with the cell state of the bridge and the machine state of the vehicle, and a station of the list. The extension also
publishes `fleet/state` (retained): the machine state of every vehicle as seen by this bridge. Simulation only so far.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from hydra_umc_sdk.bridge_contract import BridgeJob, JobPhase, MachineState, evaluate_job, job_to_dict
from hydra_umc_sdk.bridge_transport import MqttPublish
from hydra_umc_sdk.job_intake import IdempotencyStore, JobIntake, read_job_message
from hydra_umc_sdk.signed_commands import CommandSigningError, CommandVerifier, SignedCommandSettings, load_key, sign_command

from .extensions import BridgeExtension, ExtensionConfigError, Publish, section_from_dict

_LOG = logging.getLogger(__name__)

NAME = "fleet"
TOPIC_PREFIX = "hydra/bridges/ros2/"
JOB_VERB = "cmd/fleet_job"
RESULT_TOPIC = f"{TOPIC_PREFIX}{JOB_VERB}/result"
STATE_TOPIC = f"{TOPIC_PREFIX}fleet/state"
DEFAULT_AMR_PREFIX = "hydra/bridges/amr/"
_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_NODE = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")
_MQTT_PREFIX = re.compile(r"[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*/")
MOVEMENT_PHASES = frozenset({JobPhase.PREPARE, JobPhase.PROCESS, JobPhase.COMPLETE})
MAX_VEHICLES = 64
MAX_STATIONS = 256
MAX_PENDING = 64
KINDS = ("ros2", "vda5050")


@dataclass(frozen=True)
class FleetSettings:
    enabled: bool = False
    # JSON file: {"vehicles": {"amr_1": {"kind": "vda5050", "stations": {"dock_a": "N12"}}, "arm_1": {"kind": "ros2"}}}
    fleet_file: str = ""
    # key of the signed commands this bridge ASKS for on cmd/fleet_job
    command_key_file: str = ""
    command_max_skew_ms: int = 5000
    command_max_expires_ms: int = 30000
    # key this bridge SIGNS with when it forwards a job to an AMR bridge (that bridge must ask for signatures)
    amr_command_key_file: str = ""
    # how old the state of an AMR bridge may be before the vehicle counts as OFFLINE (its state interval is 5 s by default)
    amr_state_max_age_s: float = 15.0
    require_global_stop: bool = True
    state_interval_s: float = 5.0


@dataclass(frozen=True)
class Vehicle:
    kind: str
    stations: tuple[tuple[str, str], ...] = ()
    topic_prefix: str = DEFAULT_AMR_PREFIX


def load_fleet(path: str) -> dict[str, Vehicle]:
    """`{"vehicles": {"amr_1": {"kind": "vda5050", "stations": {"dock_a": "N12"}, "topic_prefix": "hydra/bridges/amr/"}, "arm_1": {"kind": "ros2"}}}`."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ExtensionConfigError(f"cannot read {path}: {error}") from error
    vehicles = raw.get("vehicles") if isinstance(raw, dict) else None
    if not isinstance(vehicles, dict) or not vehicles or len(vehicles) > MAX_VEHICLES:
        raise ExtensionConfigError(f"{path} must be a JSON object with a 'vehicles' object of 1 to {MAX_VEHICLES} vehicles")
    out: dict[str, Vehicle] = {}
    prefixes: set[str] = set()
    for name, entry in vehicles.items():
        if not _NAME.fullmatch(name) or not isinstance(entry, dict) or entry.get("kind") not in KINDS:
            raise ExtensionConfigError(f"vehicle '{name}' of {path} must be named with letters, digits, _ . - and have a kind of {' or '.join(KINDS)}")
        unknown = sorted(set(entry) - {"kind", "stations", "topic_prefix"})
        if unknown:
            raise ExtensionConfigError(f"unknown keys in vehicle '{name}' of {path}: {', '.join(unknown)}")
        if entry["kind"] == "ros2":
            if "stations" in entry or "topic_prefix" in entry:
                raise ExtensionConfigError(f"vehicle '{name}' of {path} is a ros2 vehicle: it takes no stations and no topic_prefix")
            out[name] = Vehicle("ros2")
            continue
        stations = entry.get("stations", {})
        prefix = entry.get("topic_prefix", DEFAULT_AMR_PREFIX)
        if not isinstance(stations, dict) or len(stations) > MAX_STATIONS:
            raise ExtensionConfigError(f"the stations of vehicle '{name}' of {path} must be an object of at most {MAX_STATIONS} entries")
        for station, node in stations.items():
            if not _NAME.fullmatch(station) or not isinstance(node, str) or not _NODE.fullmatch(node):
                raise ExtensionConfigError(f"station '{station}' of vehicle '{name}' of {path} must map a name to a node id of letters, digits and _ . : -")
        if not isinstance(prefix, str) or not _MQTT_PREFIX.fullmatch(prefix):
            raise ExtensionConfigError(f"topic_prefix of vehicle '{name}' of {path} must be a plain MQTT prefix that ends with /")
        if prefix in prefixes:
            raise ExtensionConfigError(f"two vehicles of {path} use the topic prefix {prefix}: one AMR bridge serves one vehicle")
        prefixes.add(prefix)
        out[name] = Vehicle("vda5050", tuple(stations.items()), prefix)
    return out


def parse(section: object) -> FleetSettings:
    settings = section_from_dict(section, FleetSettings, NAME)
    if not settings.enabled:
        return settings
    if not settings.fleet_file:
        raise ExtensionConfigError("'extensions.fleet.fleet_file' is needed")
    if not settings.command_key_file:
        raise ExtensionConfigError("'extensions.fleet.command_key_file' is needed: a fleet command must be signed")
    if settings.command_max_skew_ms < 0 or settings.command_max_expires_ms < 1:
        raise ExtensionConfigError("'extensions.fleet.command_max_skew_ms' and 'command_max_expires_ms' must be whole numbers of milliseconds (the expiry above 0)")
    for key in ("amr_state_max_age_s", "state_interval_s"):
        value = getattr(settings, key)
        if not math.isfinite(value) or not 0 < value <= 3600:
            raise ExtensionConfigError(f"'extensions.fleet.{key}' must be a number of seconds above 0 and at most 3600")
    fleet = load_fleet(settings.fleet_file)
    if any(vehicle.kind == "vda5050" for vehicle in fleet.values()) and not settings.amr_command_key_file:
        raise ExtensionConfigError("'extensions.fleet.amr_command_key_file' is needed: a job forwarded to an AMR bridge is signed")
    return settings


def machine_from_state(payload: bytes, now_ms: float, max_age_ms: float) -> MachineState:
    """The machine state in a `state` message of an AMR bridge; missing, unreadable, unknown or old is OFFLINE (fail closed)."""
    try:
        body = json.loads(payload)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return MachineState.OFFLINE
    if not isinstance(body, dict):
        return MachineState.OFFLINE
    observed = body.get("observed_at")
    if isinstance(observed, bool) or not isinstance(observed, (int, float)) or not math.isfinite(observed) or now_ms - observed > max_age_ms or observed - now_ms > max_age_ms:
        return MachineState.OFFLINE
    try:
        return MachineState(body.get("machine_state"))
    except ValueError:
        return MachineState.OFFLINE


class FleetGateway:
    """The decision about one fleet command. Pure: the bridge, the clock and the keys are given."""

    def __init__(
        self,
        fleet: dict[str, Vehicle],
        intake: JobIntake,
        store: IdempotencyStore | None,
        bridge: Any,
        amr_key: bytes | None,
        settings: FleetSettings,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._fleet = fleet
        self._intake = intake
        self._store = store
        self._bridge = bridge
        self._amr_key = amr_key
        self._settings = settings
        self._clock = clock
        self._lock = threading.Lock()
        self._amr_state: dict[str, tuple[MachineState, float]] = {}
        self._pending: dict[str, str] = {}  # "<prefix>|<job id>" of a job sent to an AMR bridge -> the vehicle

    # ----- the other direction: what the AMR bridges say
    def on_amr_state(self, prefix: str, payload: bytes) -> None:
        state = machine_from_state(payload, self._clock() * 1000, self._settings.amr_state_max_age_s * 1000)
        with self._lock:
            self._amr_state[prefix] = (state, self._clock())

    def amr_machine(self, prefix: str) -> MachineState:
        """The state of the vehicle behind `prefix`, as of now: never heard, or heard longer ago than the limit, is OFFLINE."""
        with self._lock:
            seen = self._amr_state.get(prefix)
        if seen is None or self._clock() - seen[1] > self._settings.amr_state_max_age_s:
            return MachineState.OFFLINE
        return seen[0]

    def on_amr_result(self, prefix: str, payload: bytes) -> list[MqttPublish]:
        """The answer of an AMR bridge to a job this extension forwarded, relayed; an answer to anything else is not ours and is dropped."""
        try:
            body = json.loads(payload)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return []
        job_id = body.get("job_id") if isinstance(body, dict) else None
        with self._lock:
            owner = self._pending.pop(f"{prefix}|{job_id}", None) if isinstance(job_id, str) else None
        if owner is None:
            return []
        relay = {"vehicle": owner, "job_id": job_id, "kind": "vda5050", "allowed": bool(body.get("allowed")), "reason": str(body.get("reason", ""))[:300], "amr_answer": True}
        return [MqttPublish(RESULT_TOPIC, json.dumps(relay))]

    def machines(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for name, vehicle in self._fleet.items():
            if vehicle.kind == "vda5050":
                machine = self.amr_machine(vehicle.topic_prefix)
            else:
                observed = self._bridge._observed_state()
                machine = observed if observed is not None else MachineState.OFFLINE
            out[name] = {"kind": vehicle.kind, "machine_state": machine.value}
        return out

    # ----- the command
    def handle(self, topic: str, payload: bytes, retained: bool = False) -> list[MqttPublish]:
        message = read_job_message(self._intake, topic, payload, retained=retained)
        if message.ignore:
            return []
        if message.refusal is not None:
            return [self._answer(dict(message.refusal))]
        if message.repeat_answer is not None:
            return [MqttPublish(RESULT_TOPIC, message.repeat_answer)]
        job = message.job
        assert job is not None
        body, forwards = self._decide(job)
        if self._store is not None:
            self._store.remember(job.idempotency_key, json.dumps(body))
        return [self._answer(body), *forwards]

    @staticmethod
    def _answer(body: dict[str, object]) -> MqttPublish:
        return MqttPublish(RESULT_TOPIC, json.dumps(body))

    def _decide(self, job: BridgeJob) -> tuple[dict[str, object], list[MqttPublish]]:
        wanted = job.parameters.get("vehicle", "")
        if job.phase is JobPhase.ABORT and wanted in ("", "*"):
            return self._abort_all(job)
        vehicle = self._fleet.get(wanted)
        if vehicle is None:
            return self._refuse(job, wanted, "the vehicle is not in the list of the configuration"), []
        if job.phase is JobPhase.ABORT or vehicle.kind == "ros2":
            return self._forward(job, wanted, vehicle, station=None)
        decision = evaluate_job(job, self._bridge._cell_state(), self.amr_machine(vehicle.topic_prefix))
        if not decision.allowed:
            return self._refuse(job, wanted, decision.reason), []
        station = job.parameters.get("station", "")
        if job.phase in MOVEMENT_PHASES and dict(vehicle.stations).get(station) is None:
            return self._refuse(job, wanted, "the station is not in the list of the vehicle"), []
        return self._forward(job, wanted, vehicle, station=station)

    def _refuse(self, job: BridgeJob, vehicle: str, reason: str) -> dict[str, object]:
        return {"allowed": False, "reason": reason, "job_id": job.job_id, "vehicle": vehicle}

    def _forward(self, job: BridgeJob, name: str, vehicle: Vehicle, station: str | None) -> tuple[dict[str, object], list[MqttPublish]]:
        key = f"{name}:{job.idempotency_key}"[:128]
        if vehicle.kind == "ros2":
            forwarded = BridgeJob(job.job_id, key, job.source, job.phase, job.machine_state, {})
            reply = self._bridge.handle_message(f"{TOPIC_PREFIX}cmd/job", json.dumps(job_to_dict(forwarded)).encode())
            inner = json.loads(reply[0].payload) if reply else {}
            allowed = bool(inner.get("allowed"))
            body: dict[str, object] = {"allowed": allowed, "reason": str(inner.get("reason", "")), "job_id": job.job_id, "vehicle": name, "kind": "ros2", "mode": inner.get("mode", "plan-only")}
            if "executed" in inner:
                body["executed"] = bool(inner["executed"])
            return body, []
        parameters: dict[str, str] = {}
        if job.phase in MOVEMENT_PHASES and station is not None:
            parameters["goal_node"] = dict(vehicle.stations)[station]
        forwarded = BridgeJob(job.job_id, key, job.source, job.phase, MachineState.IDLE, parameters)
        topic = f"{vehicle.topic_prefix}cmd/job"
        assert self._amr_key is not None
        signed = sign_command(topic, job_to_dict(forwarded), self._amr_key)
        with self._lock:
            while len(self._pending) >= MAX_PENDING:
                self._pending.pop(next(iter(self._pending)))
            self._pending[f"{vehicle.topic_prefix}|{job.job_id}"] = name
        body = {"allowed": True, "reason": "forwarded to the AMR bridge, which gates it again", "job_id": job.job_id, "vehicle": name, "kind": "vda5050", "mode": "send"}
        return body, [MqttPublish(topic, json.dumps(signed), False, 1)]

    def _abort_all(self, job: BridgeJob) -> tuple[dict[str, object], list[MqttPublish]]:
        results: dict[str, object] = {}
        forwards: list[MqttPublish] = []
        for name, vehicle in self._fleet.items():
            body, sent = self._forward(job, name, vehicle, station=None)
            results[name] = {"kind": vehicle.kind, "allowed": body["allowed"], "reason": body["reason"]}
            forwards.extend(sent)
        return {"allowed": True, "reason": "abort forwarded to every vehicle", "job_id": job.job_id, "vehicle": "*", "mode": "send", "vehicles": results}, forwards


class FleetExtension(BridgeExtension):
    name = NAME
    job_verbs = frozenset({JOB_VERB})

    def __init__(self, settings: FleetSettings, bridge: Any, verifier: CommandVerifier, amr_key: bytes | None, fleet: dict[str, Vehicle]) -> None:
        self.settings = settings
        self._bridge = bridge
        self._verifier = verifier
        self._amr_key = amr_key
        self.fleet = fleet
        self.gateway: FleetGateway | None = None
        self._own_store: IdempotencyStore | None = None
        self._publish: Publish | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._command_topic = f"{TOPIC_PREFIX}{JOB_VERB}"
        self._state_topics: dict[str, str] = {}
        self._result_topics: dict[str, str] = {}
        for vehicle in fleet.values():
            if vehicle.kind == "vda5050":
                self._state_topics[f"{vehicle.topic_prefix}state"] = vehicle.topic_prefix
                self._result_topics[f"{vehicle.topic_prefix}cmd/job/result"] = vehicle.topic_prefix

    def subscriptions(self) -> list[tuple[str, int]]:
        return [(topic, 1) for topic in (*self._state_topics, *self._result_topics)]

    def claims(self, topic: str) -> bool:
        return topic == self._command_topic or topic in self._state_topics or topic in self._result_topics

    def inline(self, topic: str) -> bool:
        return topic in self._state_topics or topic in self._result_topics

    def _ensure_gateway(self) -> FleetGateway:
        if self.gateway is None:
            bridge = self._bridge
            store = getattr(bridge, "_idempotency", None)
            if store is None:
                store = self._own_store = IdempotencyStore()
            self.gateway = FleetGateway(self.fleet, JobIntake(store, self._verifier), store, bridge, self._amr_key, self.settings)
        return self.gateway

    def on_message(self, topic: str, payload: bytes, retained: bool) -> list[MqttPublish]:
        gateway = self._ensure_gateway()
        if topic in self._state_topics:
            gateway.on_amr_state(self._state_topics[topic], payload)
            return []
        if topic in self._result_topics:
            return [] if retained else gateway.on_amr_result(self._result_topics[topic], payload)
        return gateway.handle(topic, payload, retained)

    def tick(self) -> bool:
        """Publish the state of the fleet now (the thread calls this once per interval). Never raises."""
        publish = self._publish
        if publish is None:
            return False
        try:
            body = {"observed_at": int(time.time() * 1000), "vehicles": self._ensure_gateway().machines()}
            publish(STATE_TOPIC, json.dumps(body), True)
            return True
        except Exception:  # noqa: BLE001 - tried again at the next tick
            _LOG.exception("could not publish the state of the fleet")
            return False

    def _loop(self) -> None:
        while not self._stop.wait(self.settings.state_interval_s):
            self.tick()

    def start(self, node: Any, publish: Publish) -> None:
        self._publish = publish
        self._ensure_gateway()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hydra-ros2-fleet", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
            self._thread = None
        self._publish = None
        self.gateway = None
        if self._own_store is not None:
            self._own_store.close()
            self._own_store = None


def build(settings: FleetSettings, bridge: Any) -> FleetExtension | None:
    if not settings.enabled:
        return None
    guard = getattr(bridge, "global_stop", None)
    if settings.require_global_stop and not getattr(guard, "enabled", False):
        raise ExtensionConfigError("the fleet extension needs the bridge to follow the global stop of the server: set HYDRA_UMC_ROS2_GLOBAL_STOP=1 (or 'require_global_stop' to false if the installation has another way)")
    fleet = load_fleet(settings.fleet_file)
    try:
        verifier = CommandVerifier(SignedCommandSettings(True, settings.command_key_file, settings.command_max_skew_ms, settings.command_max_expires_ms))
        amr_key = load_key(settings.amr_command_key_file) if settings.amr_command_key_file else None
    except CommandSigningError as error:
        raise ExtensionConfigError(f"a command key cannot be used: {error}") from error
    if amr_key is None and any(vehicle.kind == "vda5050" for vehicle in fleet.values()):
        raise ExtensionConfigError("'extensions.fleet.amr_command_key_file' is needed: a job forwarded to an AMR bridge is signed")
    return FleetExtension(settings, bridge, verifier, amr_key, fleet)
