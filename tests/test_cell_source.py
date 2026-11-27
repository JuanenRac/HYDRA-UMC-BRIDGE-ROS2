"""Where the cell state of the service comes from, and why a bridge that moves does not assume READY."""

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hydra_umc_sdk.bridge_contract import CellState

from hydra_umc_bridge_ros2 import service

PREFIX = "HYDRA_UMC_ROS2_CELL_STATE_"


class Node:
    def declare_parameter(self, _name, default):
        return SimpleNamespace(value=default)


def build(environment, motion=False):
    config = service.load_service_config(None)
    if motion:
        config = replace(config, extensions={"motion": SimpleNamespace(enabled=True)})
    clean = {key: value for key, value in os.environ.items() if not key.startswith(PREFIX)}
    with mock.patch.dict(os.environ, {**clean, **environment}, clear=True), mock.patch.object(service, "build_extensions"):
        return service.build_bridge(config, lambda _name: Node())


class CellSourceTests(unittest.TestCase):
    def test_without_a_source_the_cell_is_ready_unless_the_bridge_moves(self):
        self.assertIs(build({})._cell_state(), CellState.READY)
        self.assertIs(build({}, motion=True)._cell_state(), CellState.INHIBITED)
        self.assertEqual(build({}).cell_source, "none")

    def test_a_source_decides_and_a_faulted_machine_still_wins(self):
        bridge = build({PREFIX + "SOURCE": "fixed", PREFIX + "VALUE": "inhibited"})
        self.assertIs(bridge._cell_state(), CellState.INHIBITED)
        self.assertEqual(bridge.cell_source, "fixed")
        ready = build({PREFIX + "SOURCE": "fixed", PREFIX + "VALUE": "READY"}, motion=True)
        self.assertIs(ready._cell_state(), CellState.READY)
        ready.observe("FAULT")
        self.assertIs(ready._cell_state(), CellState.FAULT)

    def test_a_missing_state_file_is_a_safe_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge = build({PREFIX + "SOURCE": "file", PREFIX + "PATH": str(Path(folder) / "absent")})
            self.assertIs(bridge._cell_state(), CellState.SAFE_STOP)

    def test_a_source_that_cannot_be_used_stops_the_start(self):
        for environment in ({PREFIX + "SOURCE": "file"}, {PREFIX + "SOURCE": "nowhere"}, {PREFIX + "SOURCE": "safety_zones", PREFIX + "PATH": "state.json"}):
            with self.assertRaises(service.ConfigError):
                build(environment)

    def test_the_answer_to_a_job_names_the_source(self):
        bridge = build({}, motion=True)
        job = {"job_id": "j1", "idempotency_key": "k1", "source": "s", "target": "t", "operation": "move", "phase": "load", "machine_state": "IDLE", "parameters": {}}
        answer = json.loads(bridge._handle_job(json.dumps(job).encode()).payload)
        self.assertEqual(answer.get("cell_source", "none"), "none")


if __name__ == "__main__":
    unittest.main()
