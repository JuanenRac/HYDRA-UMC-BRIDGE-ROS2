<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Change history
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Changelog

## [0.0.7] - V07-014: the SDK's own real phase-construction rejection reached this bridge's test suite

A second independent revalidation audit found this bridge's own
`test_unknown_sdk_phase_fails_closed_instead_of_using_the_job_action`
still constructed a `BridgeJob` directly with a raw `"SOME_FUTURE_PHASE"`
string - HYDRA-UMC-SDK's own real fix (REV-008) now rejects that AT
CONSTRUCTION TIME, so the test never even reached the coordinator's own
assertion. Split in two, same as HYDRA-UMC-BRIDGE-UAV's own
already-updated test: a new
`test_constructing_a_bridge_job_with_an_unknown_phase_is_refused_by_the_sdk_itself`
proves the SDK's own real rejection, and the original test now uses a
minimal explicit double (`SimpleNamespace(phase=...)`) to keep proving
`Ros2Coordinator.dispatch()`'s own defensive `_interfaces.get(...)`
fallback still fails closed - real defense-in-depth, not weakened to let
the old construction succeed again.

## [0.0.6] - ROS-01: real bounded safe-stop response, not just availability

- **ROS-01 (found in an ecosystem-wide software-improvements audit,
  P1):** `Ros2SafeStopClient.call()` used the synchronous-style
  `client.call(request)` - `wait_for_service(timeout_sec=...)` only
  bounds whether the service exists, never the response itself. Real
  rclpy's own synchronous `call()` needs an executor already spinning to
  resolve its underlying future at all, and even then carries no
  deadline of its own - a service that accepts the request but never
  replies left this call blocked with no bounded progress guarantee.
  Fixed: `call_async()` + `rclpy.spin_until_future_complete(node,
  future, timeout_sec=...)` - this bridge's own node drives its own
  future to completion or to the real timeout, whichever comes first.
  4 new tests (`Ros2SafeStopClientRealControlFlowTests`) exercise the
  real control flow (timeout, success, a future's own reported
  exception, service-unavailable never reaching `call_async`) via
  fake-but-structurally-real `rclpy`/`std_srvs.srv` modules injected
  through `sys.modules` - proving the actual fix, not just documenting
  rclpy's own API. 29 tests total, up from 25.
- **`tools/bump_version.py`'s own auto-generated CHANGELOG heading
  embedded a literal calendar date** (`date.today().isoformat()`) into
  this public file - every real entry here is otherwise dated only by
  its position, never a literal date. The same bug, copied from the same
  template, found and fixed in the same pass across all 5 sibling
  bridges (BRIDGE-CNC/LASER/OPENPNP/PRINTER3D/ROS2) plus HYDRA-UMC-SDK
  and HYDRA-UMC-OS. Removed before it could ever actually land one (no
  prior real build in this repo's own history shows the script running
  mechanically without a hand-written entry replacing the stub first).
  Repo-hygiene fix, no runtime code changed, no version bump.
- **`run_forever()`'s initial MQTT connect now retries with backoff**
  (`connect_with_retry()`, new) - found in an ecosystem-wide
  software-improvements audit: this bridge's process used to die
  outright if it started before HYDRA-UMC-MQTT-BROKER was listening yet,
  a real race between two independent systemd units with no ordering
  guarantee across a reboot. Only `OSError` (what an unreachable broker
  actually raises) is retried; anything else surfaces immediately as a
  real bug. Once connected, paho-mqtt's own `loop_forever()` already
  handles a later mid-session drop on its own - only the first connect
  needed this.

## [0.0.5] - Real MQTT transport over the real broker

- **`mqtt_transport.py`** (new) - reaches this bridge's already-real logic
  (`Ros2Coordinator.dispatch`, `Ros2SafeStopClient.call`,
  `Ros2StateSubscriber`) over `HYDRA-UMC-MQTT-BROKER`, per the
  ecosystem's own "MQTT via the real broker, real commands included"
  decision - `hydra/bridges/ros2/cmd/{job,safe_stop}` in,
  `hydra/bridges/ros2/state` (retained, republished from the real ROS 2
  `state_topic`) and `.../cmd/<verb>/result` out. `bridge_state_to_mqtt()`
  is the real, intended use of `Ros2StateSubscriber`'s own `on_state`
  callback hook - this module IS the "separately deployed adapter"
  `rclpy_transport.py`'s own docstring already anticipated.
  `Ros2MqttBridge.handle_message()` is a pure(ish) topic dispatcher;
  `cmd/job` never touches rclpy and is fully testable, `cmd/safe_stop`
  degrades the same honest way `rclpy_transport.py`'s own tests already
  document on a host without a real ROS 2 install. `inspect_service`/
  `job_action` stay untouched, same reason as always: no real message/
  action type exists for them yet. `run_forever()` is the thin real-I/O
  glue, lazily importing the new optional `paho-mqtt` dependency (a
  standalone PyPI package, unlike rclpy). 10 new tests.

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

## [0.0.2]

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
