# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - A job key whose first attempt left no answer
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""The one switch and the one shape of answer for a repeated job key whose first attempt left no answer.

A key that was claimed but never got an answer stored (the bridge stopped between the claim and the answer) is refused by default. With
`HYDRA_UMC_ROS2_IN_DOUBT_CONSULT=1` (off by default) the repeat is answered with what the system reports now: the state of the machine as the ROS 2 state topic
gave it, the state of the vehicle for the fleet entry, and whether the motion of that job is still running for the motion entry. It is a reading, never a decision:
the job is not evaluated and nothing is sent again, and when the system reports nothing the repeat stays refused.
"""

from __future__ import annotations

import json
import os

from hydra_umc_sdk.bridge_contract import BridgeJob, MachineState

IN_DOUBT_CONSULT_ENVIRONMENT = "HYDRA_UMC_ROS2_IN_DOUBT_CONSULT"


def in_doubt_consult_from_environment() -> bool:
    return os.environ.get(IN_DOUBT_CONSULT_ENVIRONMENT, "").strip() == "1"


def in_doubt_policy(consult: bool) -> str:
    """The `in_doubt_policy` of the shared intake for this switch."""
    return "consult" if consult else "refuse"


def in_doubt_report(job: BridgeJob, state: MachineState | None, *, reading: bool = False, **extra: object) -> str | None:
    """The stored answer for a repeat in doubt, or None when there is no reading: no `state` and nothing else (`reading`) that the system reported."""
    if state is None and not reading:
        return None
    report: dict[str, object] = {"allowed": False, "in_doubt": True, "job_id": job.job_id}
    if state is not None:
        report["machine_state"] = state.value
    report.update(extra)
    now = f"the system now reports {state.value}" if state is not None else "the system now reports its motion"
    report["reason"] = f"the first attempt of this job left no answer; {now} and the job is not run again"
    return json.dumps(report)
