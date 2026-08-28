<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - ROS 2 bidirectional coordination bridge
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# HYDRA-UMC-BRIDGE-ROS2

🇺🇸 **English** | [🇪🇸 Español](README_spa.md) | [🇫🇷 Français](README_fra.md) | [🇮🇹 Italiano](README_ita.md) | [🇩🇪 Deutsch](README_deu.md) | [🇨🇳 简体中文](README_zho.md) | [🇯🇵 日本語](README_jpn.md)

Bidirectional, high-level coordination boundary between HYDRA-UMC and ROS 2.
It maps continuous observation to a topic, immediate inspection to a service,
and long-running cell work to a cancellable action. It is not a motor-control
node and it cannot bypass HYDRA-UMC-SERVER, MCU limits, watchdogs or E-STOP.

## Architecture

```text
ROS 2 nodes <-> BRIDGE-ROS2 <-> HYDRA-UMC-SDK <-> SERVER <-> MCU safety
```

`/hydra_umc/machine_state` is reserved for state publication,
`/hydra_umc/inspect_cell` for short inspection, and
`/hydra_umc/execute_cell_job` for cancellable work. Every job has an
idempotency key. Productive phases require an `IDLE` external machine and a
`READY` HYDRA-UMC cell; `ABORT` remains requestable during a fault.

## Build & Test

Run `build-test.bat` on Windows or `bash build-test.sh` on Linux. They compile
the source and execute deterministic unit tests without changing version or
CHANGELOG. A future rclpy adapter, ROS `.msg`/`.srv`/`.action` contracts and
DDS integration will be introduced only after a real ROS 2 environment is
selected and tested.

## Related Projects

| Project | Role |
| --- | --- |
| [HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK) | Shared job and safety contract. |
| [HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER) | Authenticated ecosystem boundary. |
| [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE) | Hardware-in-the-loop evidence path. |

## Status

Version `0.0.1` is functional as a dependency-free coordination core with
local safety tests. No ROS network, robot or physical actuator has been
validated yet.
