<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Change history
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Changelog

## [0.0.3] - Real transient_local QoS for the state topic

- Versioned the plan-only ROS 2 interface evidence: the coordinator now
  validates serialized schema `1.1` plans, `interface-plan-v1.1.json` records
  the stable endpoint contract, and build-test verifies the fixture against
  live coordinator output without importing `rclpy` or contacting DDS.
- **`coordinator.py`** - `Ros2InterfacePlan` gained `state_topic_durability`,
  a real, closed-set (`transient_local`/`volatile`) ROS 2 durability QoS
  value this plan-only contract never captured before. Researched against
  ROS 2's own real QoS documentation
  ([design.ros2.org/articles/qos.html](https://design.ros2.org/articles/qos.html)):
  the real ROS 2 default is `volatile` (no history for a late-joining
  subscriber) - a real gap for `/hydra_umc/machine_state` specifically,
  since a monitor that subscribes after the last state change would
  otherwise see nothing until the next one. `transient_local` is ROS 2's
  own real replacement for ROS 1's latched publisher; unlike ROS 1, ROS 2
  requires BOTH the publisher and the subscriber to declare it, which is
  exactly why a future rclpy adapter needs this documented in the plan
  contract itself rather than discovered against a real deployment.
- The now-superseded schema `1.0` fixture (`interface-plan-v1.json`, no QoS
  field) is kept only to prove a `1.1`-only parser correctly rejects it.
- Schema bumped `1.0` -> `1.1`. 5 new/updated regression tests - 10/10
  tests passing.

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
