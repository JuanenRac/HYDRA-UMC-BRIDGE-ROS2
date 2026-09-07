#!/usr/bin/env python3
# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Read-only ROS 2 interface-plan inspector
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Print or verify the static ROS 2 plan without contacting DDS."""

from __future__ import annotations

import json
import os
import sys
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SDK_ROOT = Path(os.environ.get("HYDRA_UMC_SDK_ROOT", ROOT.parent / "HYDRA-UMC-SDK"))
sys.path[:0] = [str(ROOT / "src"), str(SDK_ROOT / "clients" / "python" / "src")]

from hydra_umc_bridge_ros2 import Ros2Coordinator  # noqa: E402


def main() -> int:
    """Serialize the static, non-runtime interface plan."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-fixture", action="store_true", help="verify the published v1.1 fixture")
    args = parser.parse_args()
    plan = Ros2Coordinator().interface_plan().to_dict()
    if args.verify_fixture:
        fixture = ROOT / "tests" / "fixtures" / "interface-plan-v1.1.json"
        expected = json.loads(fixture.read_text(encoding="utf-8"))
        if expected != plan:
            print("ROS2_INTERFACE_PLAN=FAIL fixture differs from coordinator output", file=sys.stderr)
            return 1
        print("ROS2_INTERFACE_PLAN=PASS schema=1.1 fixture=interface-plan-v1.1.json")
        return 0
    print(json.dumps(plan, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
