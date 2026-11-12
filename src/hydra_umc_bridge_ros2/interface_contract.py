# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - ROS 2 interface contract reader
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Read and check the `.msg`, `.srv` and `.action` files of the bridge without ROS 2.

`interfaces/hydra_umc_interfaces` is the ROS 2 package with the types the plan
names (`inspect_service`, `job_action` and the cell status). A ROS 2 build is
not needed to know whether those files agree with the rest of the bridge:
this module parses the interface definition language (fields, constants and
the `---` separators), then checks that

* every type is a built-in ROS 2 type and every name is lower snake case
  (constants upper case), without repeats;
* a message has no separator, a service one and an action two;
* the string constants for the phases and the states are exactly the values of
  the SDK (`JobPhase`, `CellState`, `MachineState`), so the contract cannot
  drift from the gate that decides about a job;
* the goal of the action carries what a `BridgeJob` carries, and no field of
  the contract can hold a motion (no pose, trajectory, velocity or joint).

It never imports `rclpy` and never contacts a graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from hydra_umc_sdk.bridge_contract import BridgeJob, CellState, JobPhase, MachineState

PACKAGE = "hydra_umc_interfaces"

# Types a field may have: the built-in primitives of the ROS 2 interface language, as unbounded arrays too.
PRIMITIVES = frozenset(
    {
        "bool", "byte", "char", "float32", "float64", "int8", "uint8", "int16", "uint16",
        "int32", "uint32", "int64", "uint64", "string",
    }
)
FIELD = re.compile(r"^([a-z0-9]+)(\[\])?\s+([a-z][a-z0-9]*(?:_[a-z0-9]+)*)$")
CONSTANT = re.compile(r'^(string)\s+([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*)="([^"]*)"$')
SEPARATORS = {"msg": 0, "srv": 1, "action": 2}
# a field of this kind would be a motion, and this bridge carries phases only
MOTION_WORDS = ("pose", "trajectory", "velocity", "joint", "twist", "waypoint", "position", "torque")

# the plan names (coordinator.Ros2Coordinator) and the ROS 2 type behind each; the state topic stays std_msgs/String
INTERFACE_TYPES: Mapping[str, str] = {
    "inspect_service": f"{PACKAGE}/srv/InspectCell",
    "job_action": f"{PACKAGE}/action/ExecuteCellJob",
}


class InterfaceContractError(ValueError):
    """The interface files disagree with the bridge."""


@dataclass(frozen=True)
class Field:
    type: str
    name: str
    array: bool = False


@dataclass(frozen=True)
class Section:
    fields: tuple[Field, ...]
    constants: Mapping[str, str]


@dataclass(frozen=True)
class Interface:
    kind: str
    name: str
    sections: tuple[Section, ...]

    @property
    def type_name(self) -> str:
        return f"{PACKAGE}/{self.kind}/{self.name}"

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type_name,
            "sections": [
                {
                    "constants": dict(section.constants),
                    "fields": [f"{f.type}{'[]' if f.array else ''} {f.name}" for f in section.fields],
                }
                for section in self.sections
            ],
        }


def parse(kind: str, name: str, text: str) -> Interface:
    """Parse one definition; raise `InterfaceContractError` on anything outside the small language this bridge allows."""

    if kind not in SEPARATORS:
        raise InterfaceContractError(f"unknown interface kind {kind!r}")
    if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", name):
        raise InterfaceContractError(f"{kind}/{name}: the type name must be CamelCase")
    sections: list[tuple[list[Field], dict[str, str]]] = [([], {})]
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        where = f"{kind}/{name} line {number}"
        if line == "---":
            sections.append(([], {}))
            continue
        if "#" in line:
            raise InterfaceContractError(f"{where}: put a comment on its own line")
        constant = CONSTANT.match(line)
        if constant:
            if constant.group(2) in sections[-1][1]:
                raise InterfaceContractError(f"{where}: constant {constant.group(2)} is repeated")
            sections[-1][1][constant.group(2)] = constant.group(3)
            continue
        field = FIELD.match(line)
        if not field or field.group(1) not in PRIMITIVES:
            raise InterfaceContractError(f"{where}: {line!r} is not a field of a built-in type")
        if any(f.name == field.group(3) for f in sections[-1][0]):
            raise InterfaceContractError(f"{where}: field {field.group(3)} is repeated")
        sections[-1][0].append(Field(field.group(1), field.group(3), bool(field.group(2))))
    if len(sections) - 1 != SEPARATORS[kind]:
        raise InterfaceContractError(f"{kind}/{name}: expected {SEPARATORS[kind]} separator(s), found {len(sections) - 1}")
    built = tuple(Section(tuple(fields), dict(constants)) for fields, constants in sections)
    for section in built:
        for declared in section.fields:
            for word in MOTION_WORDS:
                if word in declared.name:
                    raise InterfaceContractError(f"{kind}/{name}: field {declared.name} would carry a motion ({word})")
    return Interface(kind, name, built)


def load_package(root: Path) -> dict[str, Interface]:
    """Read every definition of the package folder, keyed by its ROS 2 type name."""

    found: dict[str, Interface] = {}
    for kind in SEPARATORS:
        folder = root / kind
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob(f"*.{kind}")):
            interface = parse(kind, path.stem, path.read_text(encoding="utf-8"))
            found[interface.type_name] = interface
    return found


def _constants(interface: Interface) -> Mapping[str, str]:
    merged: dict[str, str] = {}
    for section in interface.sections:
        merged.update(section.constants)
    return merged


def check(package: Mapping[str, Interface]) -> None:
    """Check the whole package against the plan and the SDK; raise on the first disagreement."""

    for plan_name, type_name in INTERFACE_TYPES.items():
        if type_name not in package:
            raise InterfaceContractError(f"the plan name {plan_name} needs {type_name}, which the package does not define")
    status = package.get(f"{PACKAGE}/msg/CellStatus")
    if status is None:
        raise InterfaceContractError(f"{PACKAGE}/msg/CellStatus is missing")
    goal = package[INTERFACE_TYPES["job_action"]]
    # an ABORT is the safe-stop service, never a goal of the action
    expected = {
        f"PHASE_{phase.value}": phase.value for phase in JobPhase if phase is not JobPhase.ABORT
    }
    if _constants(goal) != expected:
        raise InterfaceContractError("the phase constants of ExecuteCellJob differ from the JobPhase values of the SDK (ABORT excluded)")
    expected_status = {f"CELL_{state.value}": state.value for state in CellState}
    expected_status.update({f"MACHINE_{state.value}": state.value for state in MachineState})
    if _constants(status) != expected_status:
        raise InterfaceContractError("the state constants of CellStatus differ from CellState and MachineState of the SDK")
    carried = {f.name for f in goal.sections[0].fields}
    job_fields = set(BridgeJob.__dataclass_fields__)  # type: ignore[attr-defined]
    wanted = {"job_id", "idempotency_key", "source", "phase"}
    if not wanted <= job_fields:
        raise InterfaceContractError("the SDK BridgeJob no longer has the fields this contract carries")
    if wanted - carried:
        raise InterfaceContractError(f"the goal of ExecuteCellJob lacks {sorted(wanted - carried)} of a BridgeJob")
    if {"parameter_keys", "parameter_values"} - carried:
        raise InterfaceContractError("the goal of ExecuteCellJob must carry the parameters as two arrays of strings")
    if "machine_state" in carried:
        raise InterfaceContractError("a goal must not claim the state of the machine: it is read, never told")


def contract(root: Path) -> dict[str, object]:
    """Load, check and serialize the package as the published fixture does."""

    package = load_package(root)
    check(package)
    return {
        "schema_version": "1.0",
        "package": PACKAGE,
        "plan_types": dict(INTERFACE_TYPES),
        "interfaces": {name: package[name].to_dict() for name in sorted(package)},
    }
