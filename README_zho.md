<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - ROS 2 双向协调桥接
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="HYDRA-UMC-BRIDGE-ROS2 横幅" width="100%">
</p>

# 🤖 HYDRA-UMC-BRIDGE-ROS2

<p align="center"><a href="README.md">🇺🇸 English</a> | <a href="README_spa.md">🇪🇸 Español</a> | <a href="README_fra.md">🇫🇷 Français</a> | <a href="README_ita.md">🇮🇹 Italiano</a> | <a href="README_deu.md">🇩🇪 Deutsch</a> | 🇨🇳 <b>简体中文</b> | <a href="README_jpn.md">🇯🇵 日本語</a></p>

### 🔗 HYDRA-UMC 与 ROS 2 之间无依赖的协调边界

<p align="left">
  <img src="https://img.shields.io/badge/许可证-GPL%203.0-blue.svg" alt="GPL 3.0">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Safety-Fails%20Closed-red.svg" alt="故障安全">
</p>

---

## 1. 🛠️ 技术概览

**HYDRA-UMC-BRIDGE-ROS2** 是 HYDRA-UMC 与 ROS 2 之间双向的高层协调边界。它把持续观测映射为 topic,把即时检查映射为 service,把长时间运行的单元作业映射为可取消的 action。它不是一个电机控制节点,也不能绕过 HYDRA-UMC-SERVER、MCU 限位、看门狗或急停(E-STOP)。

它属于 **External Automation Bridges** 家族:一组共享 `HYDRA-UMC-SDK` 相同安全契约的兄弟仓库(CNC、LASER、OPENPNP、PRINTER3D、ROS2),因此任何一个桥接都不能自行发明"可以安全工作"的定义。

### 核心特性:
* ✅ **真实的无依赖协调核心:** `coordinator.py` 中的 `Ros2Coordinator` 完全没有导入 `rclpy`——它刻意保持为纯 Python,可以在任何主机上测试,无需安装 ROS 2。*(已实现,并在 `tests/test_coordinator.py` 中测试)*
* ✅ **真实的三向接口映射:** 三个固定的类属性为每种用途精确保留对应的 ROS 2 接口类型——`/hydra_umc/machine_state`(topic,持续状态)、`/hydra_umc/inspect_cell`(service,短时检查)、`/hydra_umc/execute_cell_job`(action,可取消作业)。*(已实现)*
* ✅ **真实的共享安全门控:** 每个通过 `Ros2Coordinator.dispatch()` 派发的任务都会由 `HYDRA-UMC-SDK` 的 `bridge_contract` 中的 `evaluate_job()` 评估,这与所有兄弟桥接以及 HYDRA-UMC-SERVER 使用的是同一个门控;生产性阶段需要外部机器处于 `IDLE` 且 HYDRA-UMC 单元处于 `READY`,而 `ABORT` 在故障期间仍可请求。*(已实现)*
* ✅ **安全拒绝的阶段路由与静态证据:** 生产性阶段只映射到计划的作业操作,`ABORT` 映射到 `/hydra_umc/request_safe_stop`,未知的未来 SDK 阶段会被拒绝。`inspect_interface_plan.py` 会输出静态模式 `1.0` 计划,不导入 `rclpy` 也不联系 DDS。*(已实现,已测试)*
* ✅ **非变更式构建/测试:** `build-test.bat`/`.sh` 编译源码并运行确定性单元测试,不改变版本或 CHANGELOG。*(已实现,见下方"构建与运行")*
* 🔜 **`rclpy` 适配器与 ROS `.msg`/`.srv`/`.action` 契约** —— 只有在选定并测试了真实的 ROS 2 环境之后才会引入。*(计划中)*

---

## 2. 🔄 ROS 2 协调流程

```mermaid
flowchart LR
    ROS["ROS 2 节点"] -- "topic / service / action" --> BRIDGE["BRIDGE-ROS2<br/>Ros2Coordinator.dispatch()"]
    BRIDGE -- BridgeJob --> SDK["HYDRA-UMC-SDK<br/>evaluate_job()"]
    SDK -- GateDecision --> SERVER["HYDRA-UMC-SERVER"]
    SERVER -- "任务 / 中止" --> MCU["MCU 安全"]
```

---

## 3. 🧱 架构与设计决策

* **为什么 `coordinator.py` 完全不依赖 `rclpy`。** 其模块自身的文档字符串明确说明了这一点:它"可以在任何主机上测试,只有通过单独部署的适配器才会变成一个 ROS 2 节点"。这让与安全相关的协调逻辑无需安装 ROS 2 就能在 CI 中测试,并允许日后独立选择和验证适配器。
* **为什么使用三种不同的接口类型,而不是一个通用通道。** `state_topic`、`inspect_service` 和 `job_action` 被刻意设计为对应 ROS 2 自身的语义:持续状态发布不需要请求/响应(topic),快速检查需要同步的应答(service),而单元作业需要能在执行中途被取消(action)——把它们压缩成一个通道会丢失这种区分。
* **为什么 `Ros2Coordinator.dispatch()` 仍然让每个任务经过共享的 `evaluate_job()` 门控。** ROS 2 只是使用与 CNC、LASER、OPENPNP 和 PRINTER3D 相同的 `bridge_contract` 的又一个客户端——它不会获得任何绕过所有其他桥接和 HYDRA-UMC-SERVER 所执行的 IDLE/READY 逻辑的特殊待遇。
* **为什么在故障期间仍可请求 `ABORT`。** 门控的生产性阶段要求(`IDLE` + `READY`)被刻意地不以同样的方式应用于中止请求——操作员或 ROS 2 节点必须始终能够请求受控停止,即使正处于故障中。
* **为什么 `rclpy` 适配器和 ROS `.msg`/`.srv`/`.action` 契约尚未加入本仓库。** 在选定并测试真实的 ROS 2 环境之前就绑定具体的消息/服务/动作定义,会有引入这个本地无依赖核心无法验证的假设的风险。
* **它如何融入整个生态系统。** BRIDGE-ROS2 位于 ROS 2 节点与 `HYDRA-UMC-SDK` → `HYDRA-UMC-SERVER` → MCU 安全之间:它是一个协调边界,绝不是电机控制节点,也不能绕过 HYDRA-UMC-SERVER、MCU 限位、看门狗或急停。

---

## 📂 目录结构

```text
HYDRA-UMC-BRIDGE-ROS2/
├── src/
│   └── hydra_umc_bridge_ros2/
│       ├── __init__.py
│       └── coordinator.py       # Ros2Coordinator: 无依赖的 topic/service/action 门控
├── tests/
│   ├── test_coordinator.py      # 协调核心的确定性单元测试
│   └── fixtures/interface-plan-v1.json # 已发布的 schema-1.0 接口兼容性 fixture
├── tools/
│   ├── build_test.py            # 非变更式编译 + 测试运行器 (build-test.bat/.sh)
│   └── bump_version.py          # 同步 pyproject.toml、清单和 CHANGELOG.md
├── build-test.bat / build-test.sh  # 仅验证,绝不修改仓库
├── build.bat / build.sh            # 先验证,成功后才更新版本 + CHANGELOG
├── pyproject.toml               # 包元数据;依赖 HYDRA-UMC-SDK (git)
├── hydra-umc.project.json       # 生态系统清单(版本、成熟度、家族)
├── CHANGELOG.md
├── CODE_OF_CONDUCT.md / CONTRIBUTING.md / SECURITY.md / SUPPORT.md
├── LICENSE / LICENSE.md
└── README.md / README_*.md      # 本文件及其 6 种译文
```

---

## 4. ⚙️ 构建与运行

需要 Python 3.11+。`tools/build_test.py` 期望 `HYDRA-UMC-SDK` 作为兄弟目录被检出(`../HYDRA-UMC-SDK`),或通过环境变量 `HYDRA_UMC_SDK_ROOT` 指定。

```bash
# Windows
build-test.bat      # 仅验证 —— 不改变版本/CHANGELOG
build.bat            # 先验证,成功后更新版本 + CHANGELOG

# Linux/macOS
bash build-test.sh
bash build.sh
```

`build-test` 使用 `py_compile` 编译 `src/` 下的每个模块,并运行完整的 `unittest` 套件(`tests/test_coordinator.py`)——以确定性的方式进行,不需要安装 ROS 2,不需要网络,也不会改变版本/CHANGELOG。`build` 会先运行同样的验证,只有成功后才调用 `tools/bump_version.py`,在 `pyproject.toml`、`hydra-umc.project.json` 和 `CHANGELOG.md` 之间同步版本号。目前尚无真正的硬件 `run` 命令 —— 这需要经过验证的 ROS 2 部署。

---

## ✅ 当前状态与后续步骤

**目前真实的部分:** 版本 `0.0.2`,作为一个无依赖协调核心(`Ros2Coordinator`)是功能齐备的,配有五项确定性的本地安全测试、安全拒绝的阶段路由、静态 `plan-only` 接口模式以及已接入 CI 并带 SDK 检出的非变更式 build-test 脚本。

**集成边界:** 本桥接只是一个协调边界——它不是电机控制节点,也不能绕过 HYDRA-UMC-SERVER、MCU 限位、看门狗或急停;每个被派发的任务仍然要经过所有兄弟桥接使用的同一个共享门控。

**仍待完成:** 尚未验证任何 ROS 网络、机器人或物理执行器 —— `rclpy` 适配器和具体的 ROS `.msg`/`.srv`/`.action` 契约只会在选定并测试了真实的 ROS 2 环境之后才会引入。

---

## 🔗 相关项目

本项目是同一作者(JuanenRac / Electro Hobby 3D)更大的机器人生态系统的一部分,涵盖固件、控制软件、AI 节点和车队工具。了解这一点很有必要,因为某个请求实际上可能与这些项目之一有关,而不是与本仓库有关。

### 直接相关

- **[HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK)** —— 共享的任务与安全契约,本桥接(以及所有其他桥接)都通过它评估任务。
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** —— 本桥接汇报的经过身份验证的生态系统边界。
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** —— 面向真实 ROS 2 部署的硬件在环证据途径。

### 生态系统的其余部分

**HYDRA-UMC 平台** —— 本桥接为其协调辅助功能的多机器人微工厂
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** —— 协调多达 8 条机械臂的 CM5 + STM32H745 主板。
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** —— 每个控制客户端和桥接都会对接的 Express/WebSocket 后端。
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** —— 基于网页的控制仪表盘,多机器人 3D 可视化。

**External Automation Bridges** —— 共享同一个 `HYDRA-UMC-SDK` 任务门控的兄弟仓库
- **[HYDRA-UMC-BRIDGE-CNC](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-CNC)** —— CNC 单元协调桥接。
- **[HYDRA-UMC-BRIDGE-LASER](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-LASER)** —— 激光单元协调桥接。
- **[HYDRA-UMC-BRIDGE-OPENPNP](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-OPENPNP)** —— 面向 OpenPnP 的板级流程桥接。
- **[HYDRA-UMC-BRIDGE-PRINTER3D](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-PRINTER3D)** —— 面向开源 3D 打印软件的协调桥接。

**安全与集成证据**
- **[HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)** —— 整个桥接家族共用的单元区域安全证据。
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** —— 硬件在环测试证据。

## 👤 作者
**JuanenRac** (Electro Hobby 3D)
📧 electrohobby3d@gmail.com

## 📜 许可证
GPL-3.0 - 详见 LICENSE。
