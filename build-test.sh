#!/usr/bin/env bash
# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Non-mutating build test
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"
printf '%s\n' '*********************************************************************' \
  '* HYDRA-UMC-BRIDGE-ROS2 - BUILD TEST (NO VERSION CHANGE)           *' \
  '* 1. Compile Python source.  2. Run deterministic safety tests.    *' \
  '*********************************************************************'
python3 tools/build_test.py
