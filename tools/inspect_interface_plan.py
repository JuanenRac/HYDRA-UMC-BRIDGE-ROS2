#!/usr/bin/env python3
# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Read-only ROS 2 interface-plan inspector
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Print the static ROS 2 plan without importing rclpy or contacting DDS."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SDK_ROOT = Path(os.environ.get("HYDRA_UMC_SDK_ROOT", ROOT.parent / "HYDRA-UMC-SDK"))
sys.path[:0] = [str(ROOT / "src"), str(SDK_ROOT / "clients" / "python" / "src")]

from hydra_umc_bridge_ros2 import Ros2Coordinator  # noqa: E402


def main() -> int:
    """Serialize the static, non-runtime interface plan."""

    print(json.dumps(Ros2Coordinator().interface_plan().to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
