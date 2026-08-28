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
printf '%s\n' '* HYDRA-UMC-BRIDGE-ROS2 - build.sh' '* Mode      : INCREMENTAL BUILD' '* Author    : JuanenRac (Electro Hobby 3D)' '* Email     : electrohobby3d@gmail.com' '* Copyright : (C) 2026 JuanenRac' '* License   : GPL-3.0-or-later - see LICENSE' '* ------------------------------------------------------------------------- *' '* 1. Validate source and deterministic safety tests without mutation.' '* 2. Synchronize native version, manifest and CHANGELOG after success.' '* 3. Report the result and keep this terminal open.' '*******************************************************************************'
printf '\n[1/2] Running non-mutating build test...\n'
python3 tools/build_test.py
printf '[2/2] Incrementing synchronized project version...\n'
python3 tools/bump_version.py
printf 'BUILD=PASS. Version, manifest and CHANGELOG were synchronized.\n'
