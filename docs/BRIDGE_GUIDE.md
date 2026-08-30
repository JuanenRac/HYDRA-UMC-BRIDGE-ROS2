<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Technical bridge guide
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# HYDRA-UMC-BRIDGE-ROS2 Technical Guide

## Scope and operating model

This bridge maps a validated `BridgeJob` to a **static ROS 2 interface plan**. The current core has no `rclpy`, DDS or ROS graph dependency, so it can be verified on Windows, Linux or CI without a robot. `Ros2Coordinator` emits only a plan: state topic `/hydra_umc/machine_state`, inspect service `/hydra_umc/inspect_cell`, job action `/hydra_umc/execute_cell_job` and safe-stop service `/hydra_umc/request_safe_stop`.

`PREPARE`, `LOAD`, `PROCESS`, `UNLOAD` and `COMPLETE` map to the job action; `ABORT` maps to the safe-stop service. An unknown SDK phase is rejected. The result is always `plan-only`, never a ROS message, motion command or node startup.

## Versioned interface-plan evidence

`tests/fixtures/interface-plan-v1.json` is the published compatibility
fixture for schema `1.0`. It records all four reserved names, their
`/hydra_umc/` namespace and the mandatory `plan-only` mode. The coordinator
validates a serialized plan before accepting it, and
`inspect_interface_plan.py --verify-fixture` compares the live static plan
against the fixture. Therefore a renamed endpoint, missing field, namespace
escape or unannounced schema change fails deterministic local validation.

To introduce an incompatible interface, publish a new schema version and a
new fixture deliberately; do not silently edit the v1 fixture. This evidence
does not create a ROS graph or prove any DDS/QoS behavior.

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
