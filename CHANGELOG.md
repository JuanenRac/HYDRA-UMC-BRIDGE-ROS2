<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Change history
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Changelog

## [0.0.4] - Real, partial rclpy transport (pre-real: connected where honest, not simulated)

- **`rclpy_transport.py`** (new) - this bridge's first real transport, for
  only the 2 of 4 interfaces that have a real, standard ROS 2 message type
  today - deliberately does not invent a type for the other 2:
  - `Ros2SafeStopClient.call()` calls the real `safe_stop_service` via a
    real `std_srvs/srv/Trigger` client (empty request,
    `{success, message}` response) - a real, common ROS 2 pattern ROS 2
    ships by default.
  - `Ros2StateSubscriber.subscribe()` subscribes to the real `state_topic`
    via a real `std_msgs/msg/String` subscription, using the real
    `transient_local` durability QoS this coordinator already declares
    (v1.1 interface plan).
  - `inspect_service`/`job_action` still need a custom `.srv`/`.action`
    definition this repository doesn't have yet - building a client
    against an invented message type would misrepresent what's actually
    connected, so this module deliberately leaves them untouched.
  - `create_ros2_node()` is the one place `rclpy` is imported, lazily
    (not declared as a pip extra - ROS 2 is normally a full distribution
    install, not a standalone PyPI package); `std_srvs`/`std_msgs` are
    each imported lazily at their own real call site, degrading to a
    clear, reported failure rather than a bare `ImportError` when ROS 2
    isn't installed.
- 3 new regression tests proving the real, honest degradation path on a
  host without ROS 2 (this dev environment genuinely has none installed) -
  13/13 tests passing.

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
