#!/usr/bin/env bash
# =============================================================================
# HYDRA-UMC-BRIDGE-ROS2 - Incremental build workflow
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"
trap '[ -t 0 ] && read -r -p "Press Enter to close..." _' EXIT
printf '\n*******************************************************************************\n'
printf '%s\n' '* HYDRA-UMC-BRIDGE-ROS2 - build.sh' '* Mode      : INCREMENTAL BUILD' '* Author    : JuanenRac (Electro Hobby 3D)' '* Email     : electrohobby3d@gmail.com' '* Copyright : (C) 2026 JuanenRac' '* License   : GPL-3.0-or-later - see LICENSE' '* ------------------------------------------------------------------------- *' '* 1. Validate source and deterministic safety tests without mutation.' '* 2. Never change the version: raising it is the explicit bump command.' '* 3. Report the result and keep this terminal open.' '*******************************************************************************'
printf '\n[1/1] Running non-mutating build test...\n'
python3 tools/build_test.py
HYDRA_UMC_VERSION_BUILT="$(python3 -c 'import json; print(json.load(open("hydra-umc.project.json", encoding="utf-8"))["version"])')"
printf 'BUILD=PASS. Built v%s. Building never changes the version; to raise it run: python tools/hydra_project_tool.py bump\n' "$HYDRA_UMC_VERSION_BUILT"
