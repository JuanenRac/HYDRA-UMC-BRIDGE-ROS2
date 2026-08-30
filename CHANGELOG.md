<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Change history
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Changelog

## Unreleased

- Versioned the plan-only ROS 2 interface evidence: the coordinator now
  validates serialized schema `1.0` plans, `interface-plan-v1.json` records
  the stable endpoint contract, and build-test verifies the fixture against
  live coordinator output without importing `rclpy` or contacting DDS.

## [0.0.2] - 2026-08-30

- Added `docs/BRIDGE_GUIDE.md`, defining the plan-only ROS 2 boundary,
  compatible ROS 2 software scope, script conventions and HIL acceptance gate.
- Removed the duplicated terminal BUILD & RUN section from all seven README files.
- Made ROS 2 phase routing fail closed: an SDK phase with no explicitly
  planned route is denied rather than sent to the generic work action.
- Reserved `/hydra_umc/request_safe_stop` for the planned `ABORT` path and
  added a static `plan-only` interface schema `1.0`, inspectable without
  importing `rclpy` or contacting DDS.
- Compiled all Python tools during build-test and synchronized the English
  README with all six translated README files.
- Successful incremental build: synchronized package metadata and
  `hydra-umc.project.json`.

## [0.0.1]

- Added dependency-free ROS 2 coordination core, SDK job gate and safety tests.
- Added non-mutating build-test scripts and CI SDK checkout.
- Standardized README (all 7 languages) and project banner to match the
  rest of the ecosystem's established-project structure.
- Promoted to `established`: manifest, docs, build-test/CI, real local
  verification and no private-doc references all confirmed - no
  functional gap found in this bridge's own small, SDK-delegated core.
