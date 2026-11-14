# Changelog

All notable changes to this project are documented here.

## [Unreleased]

- **The service can refuse to start without ROS 2 security (`security.require_sros2`, off by default):** with the switch on it checks `ROS_SECURITY_ENABLE=true`, `ROS_SECURITY_STRATEGY=Enforce` and a keystore with its `enclaves`, and stops with a configuration error that names what is missing (`--check` reports it too); without the switch nothing changes. Tested without a ROS 2 installation; a deployment with real enclaves remains a bench test.

- **Named goals for Nav2 and MoveIt (`extensions.motion`), off by default and able to move a robot:** a job names a place from a file of the operator (`cmd/nav_goal` for a `NavigateToPose` waypoint, `cmd/move_named` for a `MoveGroup` joint target; no coordinates and no joint values travel in a command). It passes, in this order: the switch and a bridge that follows the global stop of the server (or the service does not start), a signature of the SDK signed commands (there is no switch that turns it off; a missing or short key stops the start), the gate of the SDK with the machine state the bridge observed, one motion at a time, a live action server and a deadline after which the goal is cancelled. An `ABORT` cancels the goal and calls the safe-stop service. Tested without ROS 2 (46 tests, including a seeded stream of 400 random commands) and in the `ros:jazzy` container against fake Nav2 and MoveIt action servers with injected faults (`tests/ros2/test_ext_motion_ros2.py`); simulation only, not run with a real Nav2 stack, MoveIt or robot (`docs/MOTION_ADAPTERS.md`, in eight languages for the user).
- **One entry for a mixed fleet of ROS 2 vehicles and VDA 5050 AMRs (`extensions.fleet`), off by default and able to move a vehicle:** `cmd/fleet_job` takes a job that names a vehicle and a station from a file of the operator. A ROS 2 vehicle is served by this bridge's own job entry; for an AMR the extension gates the job with the machine state that the AMR bridge published (missing, old or unknown is `OFFLINE`), signs a new job with the key of the AMR bridge and publishes it for HYDRA-UMC-BRIDGE-AMR, which gates it again; its answer is relayed. Like the motion extension it needs a bridge that follows the global stop of the server and a signed command (no switch turns that off). An `ABORT` for one vehicle or for all goes to each of them. `fleet/state` shows the machine state of every vehicle. Tested without ROS 2 (37 tests, including a seeded stream of 400 random commands against a simulated AMR bridge) and in the `ros:jazzy` container with a ROS 2 vehicle on a real graph and injected faults (`tests/ros2/test_ext_fleet_ros2.py`); simulation only, not run with HYDRA-UMC-BRIDGE-AMR, a fleet manager or a real vehicle (`docs/FLEET_ENTRY.md`, in eight languages for the user).
- **The state of the HYDRA-UMC cells is published on ROS 2, read only (`extensions.cell_status`):** the robots that HYDRA-UMC-SERVER announces on the broker appear as `hydra_umc_interfaces/msg/CellStatus` on `/hydra_umc/cells/robot_<id>/status` (`transient_local`, so a node that starts later still sees the last state). Nothing is published back to MQTT and the node gets no subscription, service or client, so the feed cannot move anything. It fails closed: every cell is `INHIBITED` until the server is online and the global stop has been heard, a global stop makes every cell `SAFE_STOP`, and a message that cannot be read makes that cell `OFFLINE`. Tested without ROS 2 (24 tests) and in the `ros:jazzy` container with a seeded sequence, injected faults and a subscriber that joins late (`tests/ros2/test_ext_cell_status_ros2.py`); simulation only, not run against the real server and broker (`docs/CELL_STATUS_FEED.md`, in eight languages for the user).
- **Optional extensions, all off by default:** a small hook (`extensions.py`, `docs/EXTENSIONS.md`) lets the bridge carry more of ROS 2 without changing the gate. Each extension is switched on in the `extensions` object of `docs/deploy/bridge.json`; a mistyped key, a value of the wrong type or an unknown extension stops the start (`--check` finds it), and an enabled extension that cannot start stops the service. A command topic an extension declares as moving is refused while the global stop of the server is active. 24 new tests.
- **The `/diagnostics` topic of ROS 2 can be read into HYDRA-UMC, read only (`extensions.diagnostics`):** the last status of each name, the worst level as `health`, a count per level and `STALE` when nothing was heard, published retained on `hydra/bridges/ros2/diagnostics`. Bounded names and texts, key/value pairs left out unless asked (and secret-looking keys hidden even then); it has no command topic and never reaches the gate of the jobs. Tested without ROS 2 (24 tests) and in the `ros:jazzy` container with a seeded stream, injected faults and silence (`tests/ros2/test_ext_diagnostics_ros2.py`); simulation only, not run against the drivers of a real robot (`docs/DIAGNOSTICS_FEED.md`, in eight languages for the user).
- **Tests of the start-up faults of the service:** a damaged configuration, a missing or empty secret file, ROS 2 not installed and a transport loop that raises or ends, with the exit codes systemd relies on and the secret never printed (`tests/test_service_main_faults.py`, part of the simulation bench). CI now measures coverage with a floor.
- **The last machine state expires after 2 seconds by default:** `observed_max_age_seconds` used to be off, so a publisher that died after saying `IDLE` was believed for ever. The limit is now on (`node.observed_max_age_s` in `docs/deploy/bridge.json`, the ROS parameter of the same name, `HYDRA_UMC_ROS2_OBSERVED_MAX_AGE_S`; 0 switches it off); the publisher of `/hydra_umc/machine_state` has to repeat the state as a heartbeat, or every productive job is refused as `OFFLINE`, which fails on the safe side. The guide says that stopping other nodes of the ROS domain from publishing on that topic needs SROS2 in the real deployment.
- **An `ABORT` job calls the real safe-stop service:** it used to name the service and send nothing. Now it calls it (`node.abort_calls_safe_stop`, true by default), the answer says whether the service confirmed (`executed`, `safe_stop`, `mode: send`), a repeated `ABORT` is run again, and with the switch off it only names the service as before.
- **Jobs go through the shared `JobIntake` of the SDK,** like the other bridges (malformed and repeated jobs are recognised in one place); the answers keep their shape.
- **The bridge starts as a service:** `python -m hydra_umc_bridge_ros2` reads `docs/deploy/bridge.json`, creates the rclpy node, takes the ROS parameters `observed_max_age_s` and `abort_calls_safe_stop` from a parameter file when one is given and runs the bridge; `docs/deploy/hydra_umc_bridge_ros2.launch.py`, `docs/deploy/bridge_params.yaml` and the systemd unit `docs/deploy/hydra-umc-bridge-ros2.service` start it; `--check` validates the configuration.
- **A workflow builds the interface package and tests against a real ROS 2:** "ROS 2 bench" runs in the `ros:jazzy-ros-base` container, builds `interfaces/hydra_umc_interfaces` with colcon, compares the generated types with the contract files and exercises a real node, the state topic with its expiry, the safe-stop service and the entry point (`tests/ros2/`). A container with nodes of its own, never a robot.
- 18 new tests on a host without ROS 2, and the container tests above.
- **README: the platform statement:** a note says that where the README speaks of the CM5 it means the pilot bench that runs today, and that the target platform of HYDRA-UMC, an NVIDIA Jetson Orin NX 16 GB, is a design that has not been built or tested.
- **The bridge can authenticate to the server with a client certificate (mutual TLS), off by default:** `HYDRA_UMC_ROS2_MTLS=1` with the absolute paths `HYDRA_UMC_ROS2_MTLS_CA`, `HYDRA_UMC_ROS2_MTLS_CERT` and `HYDRA_UMC_ROS2_MTLS_KEY` (`mtls.py`, used by `run_forever()` or its `mtls=` argument). TLS 1.2 or newer, the server certificate and its name checked against that one CA, the key by path only: refused when other users can read it (mode 0600) or when it lies inside a git working tree; the SDK `HYDRA_MQTT_TLS*` settings cannot be mixed in. 15 new tests, including real handshakes against a local TLS server that demands the certificate (skipped without `openssl`). Not run against the real broker.
- **The `.msg`, `.srv` and `.action` contracts of the plan are written, versioned and checked without ROS 2:** `interfaces/hydra_umc_interfaces` is the `ament_cmake` package with `srv/InspectCell`, `action/ExecuteCellJob` and `msg/CellStatus`; `hydra_umc_bridge_ros2.interface_contract` parses them, refuses types outside the built-in ones and any field that could carry a motion, and fails when the phase and state constants differ from the SDK (`JobPhase`, `CellState`, `MachineState`), when an abort becomes a goal, when a goal claims the state of the machine or lacks what a `BridgeJob` carries. `tests/fixtures/interface-contract-v1.json` is the published fixture and `inspect_interface_plan.py --verify-contract` (also a step of the simulation bench) compares against it; 21 new tests. The bridge does not use the new types yet and the package has not been built with `colcon`; signing in to the server stays as it is.

- **The tests have a coverage floor of 95 %:** `tools/coverage_floor.json` holds the measured line and branch coverage of the bridge, rounded down (95.9 % measured in the CI environment), and `python tools/hydra_project_tool.py coverage` warns when it falls below it (a warning, not a CI check). Tooling only: no behaviour changes.
## [0.1.7] - A visible simulation bench: the workflow "Simulation bench" runs the emulators and fakes of the bridge without hardware on every push, by hand and once a week; docs/SIMULATION.md says what it exercises and what it cannot show. (and 1 more changes)

- **A visible simulation bench:** the workflow "Simulation bench" runs the emulators and fakes of the bridge without hardware on every push, by hand and once a week; `docs/SIMULATION.md` says what it exercises and what it cannot show.

- **The build test of the repository compiles only on CI or when allowed:** for the stacks that compile (`rust`, `go`, `android`, `flutter`, `firmware-c`), `tools/build_test.py` prints `BUILD_TEST=SKIPPED` with exit status 2 on a plain PC, never a pass; `ALLOW_LOCAL_BUILD=1` allows it and a CI runner always may. The `python` and `node` stacks are not locked.

## [0.1.6] - The global stop guard comes from the shared SDK and the MQTT transport has optional limits, off by default

- **The guard of the global stop comes from the shared SDK:** the bridge no longer carries its own copy of `global_stop`; it imports `GlobalStopGuard`, `GlobalStopSettings` and `follow_on_connect` from `hydra_umc_sdk.global_stop`, so every bridge refuses moving commands under the same rule. The environment variables and the `[global_stop]` table are unchanged.

- **Limits of the MQTT transport, off by default:** with `HYDRA_UMC_ROS2_HARDENED=1` (and the optional `HYDRA_UMC_ROS2_MAX_QUEUE`, `..._MAX_PAYLOAD_BYTES`, `..._CONNECT_BACKOFF_FACTOR`, `..._CONNECT_MAX_DELAY_SECONDS` and `..._CONNECT_JITTER`) the queue of waiting commands is bounded (the oldest waiting command is dropped, the stop lane is never bounded), the size of a payload is capped (stop verbs are exempt) and the first connection retries with exponential backoff and jitter. With nothing set, nothing changes.

## [0.1.5] - A bridge that can follow the global stop of the server and a transport tested against a real broker

- **The bridge can follow the global stop of the server, off by default:** with `HYDRA_UMC_ROS2_GLOBAL_STOP=1` it subscribes to the retained announcement on `hydra/swarm/safety/estop` (`HYDRA_UMC_ROS2_GLOBAL_STOP_TOPIC` changes the topic) and, while the last message says `SAFE_STOP`, says anything but `READY` or cannot be read, `cmd/job` (an abort passes) is refused with the reason in the answer; stops and read-only requests keep working and only `READY` ends it. `HYDRA_UMC_ROS2_GLOBAL_STOP_STRICT=1` also refuses until the first announcement has been heard. Without the switch nothing changes. `tests/test_global_stop.py` covers the guard and the refusals, `tests/test_global_stop_broker.py` runs them against a real broker on a free local port (the announcement is delivered at QoS 0, 1 and 2, a retained stop is obeyed at connect, an unreadable one fails closed).

- **The MQTT transport is tested against a real broker:** `tests/test_broker_integration.py` starts a `HYDRA-UMC-MQTT-BROKER` on a free local port and runs the bridge through the shared client: the retained availability, a command answered at QoS 0, 1 and 2, a retained command that is only a replay, messages published at each QoS, the last will after an unclean loss of the link and the way back after the broker went down and up. It skips itself when node, `paho-mqtt` or the installed broker (`HYDRA_UMC_TEST_BROKER_DIR`, default the folder next to this one) are missing.

## [0.1.4] - The universal README and its check in the CI

- **README in the universal layout:** the README and its seven translations follow the layout that the four families share (22 sections in a fixed order), with an architecture picture and a repository-structure picture drawn from `docs/architecture.json` and `docs/structure.json`; the CI validator checks both pictures and the file tree. Where the repository has a service or an MQTT transport, the README now says how to start it and which environment variables it reads.

- **The layout of the README is checked in the CI:** the validator runs `tools/universal_readme_check.py` and lists, as a warning, what differs from the universal layout of the README.

## [0.1.3] - The MQTT transport comes from the SDK

- Building no longer changes the version; raising it is the explicit `bump` command (`python tools/hydra_project_tool.py bump`). The build scripts only report the version they built, and CI fails if a build script runs a bump.
- **The MQTT transport comes from the SDK.** Connecting with retries, the publish record and the message dispatcher are the shared `hydra_umc_sdk.bridge_transport` of SDK 0.3.6 instead of a copy of their own; what is specific to this bridge (its topics and what it does with a message) stays here, and its tests are unchanged.

- The banner follows the animated style of the ecosystem banners, with the name, the kind and the technologies of the project.
- Added `tools/hydra_project_tool.py`: the same `validate`, `test`, `version`, `status` and `bump` commands in every repository. The checks run with `HYDRA_UMC_CI=1` and fail if the version changes; only `bump` changes it.
- The SDK is now fetched at a release tag instead of the main branch, so a change of the SDK cannot break this repository by itself.
- The SDK is pinned to 0.3.6, whose gate refuses a productive job when the bridge did not read the state of its machine. The MQTT bridge passes the state heard on the ROS 2 state topic (a stale or unknown reading counts as OFFLINE), and a bridge that is not observing refuses the job instead of trusting the claim written in the job message. The tests now give the bridge a reading explicitly and check the refusal when there is none.
- Successful incremental build: synchronized package metadata and `hydra-umc.project.json`.

## [0.1.2]

- Successful incremental build: synchronized package metadata and `hydra-umc.project.json`.
- The licence is written as GPL-3.0-or-later in every header and in the package metadata.

## [0.1.1] - Maturity lowered to functional

- Maturity is functional: the code, tests and documentation are in place, but nothing here has been verified on real hardware yet, so the manifest no longer claims the established level.

## [0.1.0] - A node that is really spun, a loop that survives its input and honest answers

- **The node is spun.** `spin_node_in_background()` runs the node on a `SingleThreadedExecutor` thread and `run_forever()` uses it. Before, nothing spun the node, so the state subscription never delivered and every productive job was refused as OFFLINE (the state was only refreshed as a side effect of a safe-stop call). `Ros2SafeStopClient(node_is_spinning=True)` waits for its answer instead of spinning the node a second time, which two executors cannot do.
- **The loop does not die on its input.** `handle_message()` never raises: a deeply nested JSON (which used to end `loop_forever()` with a `RecursionError`), an oversized payload and any unexpected failure come back as a refusal on the verb's result topic. `Ros2SafeStopClient.call()` returns a result for any failure, including a down rclpy context.
- **Safe stop.** The service client is created once and reused (a new one per call leaked DDS entities), is not waited for when already ready, and `cmd/safe_stop` runs on a lane of its own that never queues behind a slow command.
- **paho-mqtt callback API version 2**, `reconnect_delay_set`, and a Last Will on `hydra/bridges/ros2/availability` ("online" once connected, "offline" when the link is lost); the dependency is `paho-mqtt>=2.1,<3`.
- **Answers.** `cmd/job` answers `allowed` as every bridge does, and `accepted` as before. An `ABORT` job no longer claims it was "forwarded to the authorised safety path": the dispatch is plan-only, and the answer says so and names `cmd/safe_stop` as the real stop.
- **State topic.** The republished state is `{"state", "machine_state", "observed_at"}` (the raw string, the machine state it normalizes to or null, and when it was heard). `run_forever()` refuses to start when the state topic cannot be subscribed instead of refusing every job as OFFLINE without a word. A state that stops being republished can now count as OFFLINE: `observed_max_age_seconds` (off by default, since it needs a publisher that republishes on a timer). Any node of the same ROS domain can still publish on the topic: restricting that is a matter of SROS2 or DDS permissions.
- **Documentation.** The test counts and the list of test files, the four interfaces, what is really connected, the need to spin the node and the diagram (no path to the server exists in this code) are corrected in the eight READMEs and the guide; `LICENSE` carries the full GPL-3.0-or-later text.
- Not run against a real ROS 2 graph: the executor, the future wait and the QoS behaviour are checked against fakes only.
- 23 new tests (64 in total); an unused import in the CI validator removed.

## [0.0.9] - Connects to a broker that demands credentials: user, password and TLS from the environment

- The MQTT client now takes its credentials and TLS from the environment of the service (`HYDRA_MQTT_USERNAME`, `HYDRA_MQTT_PASSWORD` or `HYDRA_MQTT_PASSWORD_FILE`, `HYDRA_MQTT_TLS_CA`) through `hydra_umc_sdk.mqtt_connection`, so it can reach a production HYDRA-UMC-MQTT-BROKER, which refuses anonymous clients and evaluates its ACL for the identity of the credential.
- An explicit `username`/`password` argument still wins, and a password without a user name is refused.
- The client ID must start with the `clientIdPrefix` of the bridge's credential.
- New `tests/test_mqtt_environment.py` (a fake paho client, so it runs without paho-mqtt) proves that the environment credentials and the CA reach the client, that the explicit ones win, that nothing set is an anonymous plain connection, and that a lonely password is refused.

## [0.0.8] - The gate uses the machine state the ROS 2 topic reports; retained messages are ignored and a repeated key is answered once

- **Observed machine state.** The bridge follows the ROS 2 state topic (`observe()`); once it does, the shared gate uses what the machine reported instead of the `machine_state` a requester wrote in the job (a message anybody on the broker may publish), and a machine never heard from, or reporting a state that is not known, counts as OFFLINE. `Ros2Coordinator.dispatch()` takes the observed state as an optional argument.
- **Retained messages.** `handle_message()` ignores a retained message: a retained `cmd/job` or `cmd/safe_stop` was repeated at every reconnect of the bridge (the CNC, laser and printer bridges had already fixed this).
- **Idempotency keys.** With an `IdempotencyStore` from HYDRA-UMC-SDK a repeated job key gets the answer given the first time and is not evaluated again.
- Seven new tests (tests/test_intake.py).

## [0.0.7] - the SDK's own real phase-construction rejection reached this bridge's test suite

A second, closer review found this bridge's own
`test_unknown_sdk_phase_fails_closed_instead_of_using_the_job_action`
still constructed a `BridgeJob` directly with a raw `"SOME_FUTURE_PHASE"`
string - HYDRA-UMC-SDK's own real fix now rejects that AT
CONSTRUCTION TIME, so the test never even reached the coordinator's own
assertion. Split in two, same as HYDRA-UMC-BRIDGE-UAV's own
already-updated test: a new
`test_constructing_a_bridge_job_with_an_unknown_phase_is_refused_by_the_sdk_itself`
proves the SDK's own real rejection, and the original test now uses a
minimal explicit double (`SimpleNamespace(phase=...)`) to keep proving
`Ros2Coordinator.dispatch()`'s own defensive `_interfaces.get(...)`
fallback still fails closed - real defense-in-depth, not weakened to let
the old construction succeed again.

## [0.0.6] - real bounded safe-stop response, not just availability

- `Ros2SafeStopClient.call` used the synchronous-style
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
  (`connect_with_retry()`, new) - this bridge's process used to die
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
