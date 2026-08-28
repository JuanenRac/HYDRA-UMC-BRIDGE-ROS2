<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - ROS 2 双方向協調ブリッジ
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# HYDRA-UMC-BRIDGE-ROS2

[🇺🇸 English](README.md) | [🇪🇸 Español](README_spa.md) | [🇫🇷 Français](README_fra.md) | [🇮🇹 Italiano](README_ita.md) | [🇩🇪 Deutsch](README_deu.md) | [🇨🇳 简体中文](README_zho.md) | 🇯🇵 **日本語**

HYDRA-UMC と ROS 2 間の高レベル双方向協調境界です。連続観測をトピック、
即時検査をサービス、長時間セル作業をキャンセル可能なアクションに対応付けます。
HYDRA-UMC-SERVER、MCU 制限、ウォッチドッグ、E-STOP を迂回できません。

## アーキテクチャ

```text
ROS 2 ノード <-> BRIDGE-ROS2 <-> HYDRA-UMC-SDK <-> SERVER <-> MCU 安全
```

`/hydra_umc/machine_state` は状態を公開し、`/hydra_umc/inspect_cell` は短い
検査を行い、`/hydra_umc/execute_cell_job` はキャンセル可能な作業を扱います。
各ジョブには冪等キーがあります。生産フェーズには `IDLE` と `READY` が必要で、
障害時も `ABORT` は利用可能です。

## ビルドとテスト

Windows では `build-test.bat`、Linux では `bash build-test.sh` を実行します。
バージョンや CHANGELOG は変更しません。rclpy、ROS 契約、DDS は実際の ROS 2
環境が選定・検証された後にのみ追加されます。

## 関連プロジェクト

| プロジェクト | 役割 |
| --- | --- |
| [HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK) | 共有ジョブ・安全契約。 |
| [HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER) | 認証済みエコシステム境界。 |
| [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE) | ハードウェア・イン・ザ・ループ証拠経路。 |

## 状態

バージョン `0.0.1` はローカル安全テスト済みの依存関係不要な協調コアです。
ROS ネットワーク、ロボット、物理アクチュエーターはまだ検証していません。
