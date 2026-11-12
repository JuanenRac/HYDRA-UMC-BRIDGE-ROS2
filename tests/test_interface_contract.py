# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Tests of the .msg/.srv/.action contract
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from hydra_umc_bridge_ros2.coordinator import Ros2Coordinator
from hydra_umc_bridge_ros2.interface_contract import (
    INTERFACE_TYPES,
    InterfaceContractError,
    check,
    contract,
    load_package,
    parse,
)

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "interfaces" / "hydra_umc_interfaces"


class ParserTests(unittest.TestCase):
    def test_a_message_has_fields_and_constants_and_no_separator(self):
        interface = parse("msg", "Demo", '# note\nstring KIND="x"\nbool ok\nstring[] names\n')
        section = interface.sections[0]
        self.assertEqual(dict(section.constants), {"KIND": "x"})
        self.assertEqual([(f.type, f.name, f.array) for f in section.fields], [("bool", "ok", False), ("string", "names", True)])
        self.assertEqual(interface.type_name, "hydra_umc_interfaces/msg/Demo")

    def test_the_separators_must_match_the_kind(self):
        with self.assertRaises(InterfaceContractError):
            parse("srv", "Demo", "bool ok\n")
        with self.assertRaises(InterfaceContractError):
            parse("action", "Demo", "bool ok\n---\nbool done\n")
        with self.assertRaises(InterfaceContractError):
            parse("msg", "Demo", "bool ok\n---\nbool done\n")
        self.assertEqual(len(parse("action", "Demo", "bool a\n---\nbool b\n---\nbool c\n").sections), 3)

    def test_only_builtin_types_and_snake_case_names(self):
        for bad in ("geometry_msgs/Pose pose", "string Name", "string two words", "float32[3] fixed", "string name # trailing"):
            with self.assertRaises(InterfaceContractError, msg=bad):
                parse("msg", "Demo", bad + "\n")

    def test_a_repeated_field_or_constant_is_refused(self):
        with self.assertRaises(InterfaceContractError):
            parse("msg", "Demo", "bool ok\nstring ok\n")
        with self.assertRaises(InterfaceContractError):
            parse("msg", "Demo", 'string A="1"\nstring A="2"\n')

    def test_the_type_name_must_be_camel_case_and_the_kind_known(self):
        with self.assertRaises(InterfaceContractError):
            parse("msg", "demo", "bool ok\n")
        with self.assertRaises(InterfaceContractError):
            parse("topic", "Demo", "bool ok\n")

    def test_a_field_that_would_carry_a_motion_is_refused(self):
        for name in ("target_pose", "joint_angles", "velocity", "trajectory_points", "torque_limit", "waypoint_list", "twist_z", "position_mm"):
            with self.assertRaises(InterfaceContractError, msg=name):
                parse("msg", "Demo", f"string {name}\n")


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.package = load_package(PACKAGE)

    def test_the_package_defines_the_types_the_plan_names(self):
        for type_name in INTERFACE_TYPES.values():
            self.assertIn(type_name, self.package)
        self.assertEqual(sorted(self.package), [
            "hydra_umc_interfaces/action/ExecuteCellJob",
            "hydra_umc_interfaces/msg/CellStatus",
            "hydra_umc_interfaces/srv/InspectCell",
        ])
        check(self.package)

    def test_the_plan_names_come_from_the_coordinator(self):
        plan = Ros2Coordinator().interface_plan().to_dict()
        self.assertEqual(set(INTERFACE_TYPES), {"inspect_service", "job_action"})
        self.assertTrue(all(plan[name].startswith("/hydra_umc/") for name in INTERFACE_TYPES))

    def test_the_published_fixture_is_what_the_files_say(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "interface-contract-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(contract(PACKAGE), fixture)

    def test_the_action_has_a_goal_a_result_and_feedback(self):
        action = self.package["hydra_umc_interfaces/action/ExecuteCellJob"]
        self.assertEqual([len(section.fields) > 0 for section in action.sections], [True, True, True])

    def test_the_ros_package_files_exist_and_agree_on_the_files(self):
        cmake = (PACKAGE / "CMakeLists.txt").read_text(encoding="utf-8")
        for type_name in self.package:
            _, kind, name = type_name.split("/")
            self.assertIn(f'"{kind}/{name}.{kind}"', cmake)
        self.assertIn("<name>hydra_umc_interfaces</name>", (PACKAGE / "package.xml").read_text(encoding="utf-8"))

    def test_the_files_of_the_package_use_plain_line_feeds(self):
        for path in PACKAGE.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"\r", path.read_bytes(), str(path))


class DriftTests(unittest.TestCase):
    """A copy of the package edited the way a careless change would be: the check must say so."""

    def copy(self, directory: str) -> Path:
        target = Path(directory) / "hydra_umc_interfaces"
        shutil.copytree(PACKAGE, target)
        return target

    def edit(self, path: Path, old: str, new: str) -> None:
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")

    def test_a_changed_phase_value_is_a_drift_from_the_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy(directory)
            self.edit(root / "action" / "ExecuteCellJob.action", 'PHASE_LOAD="LOAD"', 'PHASE_LOAD="LOADING"')
            with self.assertRaisesRegex(InterfaceContractError, "phase constants"):
                contract(root)

    def test_an_abort_phase_must_not_be_a_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy(directory)
            self.edit(root / "action" / "ExecuteCellJob.action", 'string PHASE_COMPLETE="COMPLETE"\n', 'string PHASE_COMPLETE="COMPLETE"\nstring PHASE_ABORT="ABORT"\n')
            with self.assertRaisesRegex(InterfaceContractError, "phase constants"):
                contract(root)

    def test_a_missing_state_constant_is_a_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy(directory)
            self.edit(root / "msg" / "CellStatus.msg", 'string MACHINE_STOPPED="STOPPED"\n', "")
            with self.assertRaisesRegex(InterfaceContractError, "state constants"):
                contract(root)

    def test_a_goal_that_claims_the_machine_state_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy(directory)
            self.edit(root / "action" / "ExecuteCellJob.action", "string phase\n", "string phase\nstring machine_state\n")
            with self.assertRaisesRegex(InterfaceContractError, "read, never told"):
                contract(root)

    def test_a_goal_without_the_idempotency_key_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy(directory)
            self.edit(root / "action" / "ExecuteCellJob.action", "string idempotency_key\n", "")
            with self.assertRaisesRegex(InterfaceContractError, "idempotency_key"):
                contract(root)

    def test_a_goal_without_the_parameters_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy(directory)
            self.edit(root / "action" / "ExecuteCellJob.action", "string[] parameter_values\n", "")
            with self.assertRaisesRegex(InterfaceContractError, "parameters"):
                contract(root)

    def test_a_missing_service_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy(directory)
            (root / "srv" / "InspectCell.srv").unlink()
            with self.assertRaisesRegex(InterfaceContractError, "inspect_service"):
                contract(root)

    def test_a_missing_status_message_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy(directory)
            (root / "msg" / "CellStatus.msg").unlink()
            with self.assertRaisesRegex(InterfaceContractError, "CellStatus"):
                contract(root)

    def test_an_empty_folder_has_no_interfaces(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_package(Path(directory)), {})


if __name__ == "__main__":
    unittest.main()
