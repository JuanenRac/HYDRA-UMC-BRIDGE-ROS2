<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Technical bridge guide
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# HYDRA-UMC-BRIDGE-ROS2 Technical Guide

## Scope and operating model

This bridge maps a validated `BridgeJob` to a **static ROS 2 interface plan**. The current core has no `rclpy`, DDS or ROS graph dependency, so it can be verified on Windows, Linux or CI without a robot. `Ros2Coordinator` emits only a plan: state topic `/hydra_umc/machine_state`, inspect service `/hydra_umc/inspect_cell`, job action `/hydra_umc/execute_cell_job` and safe-stop service `/hydra_umc/request_safe_stop`.

`PREPARE`, `LOAD`, `PROCESS`, `UNLOAD` and `COMPLETE` map to the job action; `ABORT` maps to the safe-stop service. An unknown SDK phase is rejected. `Ros2Coordinator.dispatch()` itself is always `plan-only`, never a ROS message, motion command or node startup - only `rclpy_transport.py`, given an already-gated dispatch explicitly, ever touches a real ROS 2 graph.

`rclpy_transport.py` is this bridge's first real transport, deliberately partial: `Ros2SafeStopClient` calls the real `safe_stop_service` via a real `std_srvs/Trigger` client; `Ros2StateSubscriber` subscribes to the real `state_topic` via a real `std_msgs/String` subscription using the `transient_local` durability QoS above. `inspect_service`/`job_action` have no real standard ROS 2 message type - building a client for them needs this repository to define its own `.srv`/`.action` package first, which this module deliberately does not invent. `create_ros2_node()` is the one place `rclpy` is imported, lazily; `std_srvs`/`std_msgs` are each imported lazily at their own real call site.

`mqtt_transport.py` reaches this same real logic over `HYDRA-UMC-MQTT-BROKER`, this bridge's second real transport. `Ros2MqttBridge.handle_message()` routes `hydra/bridges/ros2/cmd/job` (never touches rclpy, same as `Ros2Coordinator.dispatch()`) and `hydra/bridges/ros2/cmd/safe_stop` (a real `Ros2SafeStopClient.call()`, needing an already-created rclpy `Node`), publishing `.../cmd/<verb>/result`. `bridge_state_to_mqtt()` is the real, intended use of `Ros2StateSubscriber`'s own `on_state` callback - it republishes the real `state_topic` onto `hydra/bridges/ros2/state` (retained) as each message arrives, making this module the "separately deployed adapter" this guide's own Scope section already anticipated. `cmd/job` is fully testable without a ROS 2 install; `cmd/safe_stop` degrades the same honest way `rclpy_transport.py`'s own tests already document on a host without one. `paho-mqtt` (optional `[mqtt]` extra, a standalone PyPI package unlike rclpy) is only imported, lazily, inside `run_forever()`.

## Versioned interface-plan evidence

`tests/fixtures/interface-plan-v1.1.json` is the published compatibility
fixture for schema `1.1`. It records all four reserved names, their
`/hydra_umc/` namespace, the mandatory `plan-only` mode, and the real ROS 2
durability QoS policy (`state_topic_durability`) `state_topic` needs:
`transient_local`, ROS 2's own replacement for ROS 1's latched publisher, so
a late-joining subscriber (a monitor, a logger, an operator's session) sees
the current machine state immediately instead of only future changes. The
coordinator validates a serialized plan before accepting it, and
`inspect_interface_plan.py --verify-fixture` compares the live static plan
against the fixture. Therefore a renamed endpoint, missing field, namespace
escape, unreal QoS value or unannounced schema change fails deterministic
local validation. The now-superseded schema `1.0` fixture
(`tests/fixtures/interface-plan-v1.json`, no QoS field) is kept only to
prove a `1.1`-only parser correctly rejects it.

To introduce an incompatible interface, publish a new schema version and a
new fixture deliberately; do not silently edit the v1.1 fixture. This
evidence does not create a ROS graph; declaring `transient_local` here
documents the QoS a future rclpy adapter must set on both the publisher and
the subscriber side (ROS 2, unlike ROS 1, requires both to declare it) - it
does not itself prove any live DDS/QoS behavior.

## Compatible software

The planned northbound/southbound boundary is for ROS 2 distributions and applications that provide their own documented topics, services or actions: Nav2-based mobile robots, MoveIt-capable manipulators, simulation systems such as Gazebo, and custom ROS 2 nodes. Compatibility means adapting their public ROS 2 interfaces through a separately deployed adapter after an interface contract is selected; it does **not** mean that this repository controls them today.

## Scripts and verification

| Script | Purpose | Changes version/CHANGELOG? |
|---|---|---|
| `build-test.bat` / `build-test.sh` | Compile Python and run local tests | No |
| `build.bat` / `build.sh` | Run the same validation, then increment the project version | Yes, after success |

Set `HYDRA_UMC_SDK_ROOT` when the SDK is not a sibling checkout. Use `build-test` during development; it is the only safe default before a real ROS 2 adapter exists.

## Adding a new script

Keep a new script in the repository root only when it is an operator entry point. Add the standard copyright header, state whether it mutates version/CHANGELOG, print numbered steps, and end `.bat` scripts with `pause`. Put reusable Python logic under `tools/`, compile it in `tools/build_test.py`, add deterministic tests and document the command in the README and this guide. A script must not start ROS 2, discover a graph or send a command implicitly.

## Hardware acceptance gate

Before deploying an adapter: select the ROS 2 distribution and middleware, document namespaces/types/QoS, bind authenticated robot identity, verify stale/disconnected-state behavior, test abort independently, and perform bench/HIL validation. The native ROS controller remains responsible for motion safety.
