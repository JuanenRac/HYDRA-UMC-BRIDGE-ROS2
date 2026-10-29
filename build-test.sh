#!/usr/bin/env bash
# PRETTY_RUN_BEGIN
if [ -z "${PRETTY_RUN:-}" ] && [ -f "$(dirname "$0")/tools/pretty_run.py" ] && command -v python3 >/dev/null 2>&1; then
    PRETTY_RUN=1 exec python3 "$(dirname "$0")/tools/pretty_run.py" --wrap --mode "${PRETTY_MODE:-build-test}" -- bash "$0" "$@"
fi
# PRETTY_RUN_END
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
