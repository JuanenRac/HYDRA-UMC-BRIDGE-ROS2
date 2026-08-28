#!/usr/bin/env python3
# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Non-mutating build verification
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Compile and test without changing version or CHANGELOG."""

from __future__ import annotations

import os
import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sdk_root = Path(os.environ.get("HYDRA_UMC_SDK_ROOT", ROOT.parent / "HYDRA-UMC-SDK"))
sdk_source = sdk_root / "clients" / "python" / "src"
os.environ["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(sdk_source)))
for source in (ROOT / "src").rglob("*.py"):
    py_compile.compile(str(source), doraise=True)
subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, env=os.environ.copy(), check=True)
print("BUILD_TEST=PASS versioning=unchanged changelog=unchanged")
