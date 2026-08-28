<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - ROS 2 双向协调桥
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# HYDRA-UMC-BRIDGE-ROS2

[🇺🇸 English](README.md) | [🇪🇸 Español](README_spa.md) | [🇫🇷 Français](README_fra.md) | [🇮🇹 Italiano](README_ita.md) | [🇩🇪 Deutsch](README_deu.md) | 🇨🇳 **简体中文** | [🇯🇵 日本語](README_jpn.md)

HYDRA-UMC 与 ROS 2 之间的高层双向协调边界。它将持续观测映射到主题，
即时检查映射到服务，将长时间单元任务映射到可取消动作。它不能绕过
HYDRA-UMC-SERVER、MCU 限制、看门狗或 E-STOP。

## 架构

```text
ROS 2 节点 <-> BRIDGE-ROS2 <-> HYDRA-UMC-SDK <-> SERVER <-> MCU 安全
```

`/hydra_umc/machine_state` 发布状态，`/hydra_umc/inspect_cell` 用于短检查，
`/hydra_umc/execute_cell_job` 用于可取消任务。每个任务都有幂等键。生产
阶段需要外部机器 `IDLE` 和单元 `READY`；故障时 `ABORT` 仍然可用。

## 构建和测试

在 Windows 运行 `build-test.bat`，或在 Linux 运行 `bash build-test.sh`。
它们不会改变版本或 CHANGELOG。rclpy、ROS 契约和 DDS 只会在真实 ROS 2
环境选定并验证后加入。

## 相关项目

| 项目 | 角色 |
| --- | --- |
| [HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK) | 共享任务和安全契约。 |
| [HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER) | 经认证的生态系统边界。 |
| [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE) | 硬件在环证据路径。 |

## 状态

版本 `0.0.1` 是经过本地安全测试的无依赖协调核心。尚未验证 ROS 网络、
机器人或物理执行器。

## ⚙️ 版本化构建

`build-test.bat` / `build-test.sh` 只验证，不修改仓库。`build.bat` /
`build.sh` 先运行该验证，只有成功后才同步原生包版本、清单和 `CHANGELOG.md`。
在真实 ROS 2 部署验证前，不提供硬件 `run` 命令。
