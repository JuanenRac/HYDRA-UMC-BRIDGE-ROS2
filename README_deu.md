<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Bidirektionale ROS-2-Koordinationsbrücke
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# HYDRA-UMC-BRIDGE-ROS2

[🇺🇸 English](README.md) | [🇪🇸 Español](README_spa.md) | [🇫🇷 Français](README_fra.md) | [🇮🇹 Italiano](README_ita.md) | 🇩🇪 **Deutsch** | [🇨🇳 简体中文](README_zho.md) | [🇯🇵 日本語](README_jpn.md)

Hochrangige bidirektionale Koordinationsgrenze zwischen HYDRA-UMC und ROS 2.
Sie ordnet Beobachtung einem Topic, Prüfung einem Dienst und lange Zellenarbeit
einer abbrechbaren Aktion zu. SERVER, MCU-Grenzen, Watchdogs und E-STOP können
nicht umgangen werden.

## Architektur

```text
ROS-2-Knoten <-> BRIDGE-ROS2 <-> HYDRA-UMC-SDK <-> SERVER <-> MCU-Sicherheit
```

`/hydra_umc/machine_state` veröffentlicht Zustand, `/hydra_umc/inspect_cell`
führt kurze Prüfungen aus und `/hydra_umc/execute_cell_job` abbrechbare Arbeit.
Jeder Job hat einen Idempotenzschlüssel. Produktive Phasen brauchen `IDLE` und
`READY`; `ABORT` bleibt während eines Fehlers verfügbar.

## Bauen und testen

`build-test.bat` unter Windows oder `bash build-test.sh` unter Linux ausführen.
Sie ändern weder Version noch CHANGELOG. rclpy, ROS-Verträge und DDS folgen
erst nach einem Test mit einer echten ROS-2-Umgebung.

## Verwandte Projekte

| Projekt | Rolle |
| --- | --- |
| [HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK) | Gemeinsamer Job- und Sicherheitsvertrag. |
| [HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER) | Authentifizierte Ökosystemgrenze. |
| [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE) | Hardware-in-the-loop-Nachweisweg. |

## Status

Version `0.0.1` ist als abhängigkeitfreier Kern mit lokalen Sicherheitstests
funktional. ROS-Netz, Roboter und physischer Aktor sind noch nicht validiert.

## ⚙️ Versionierter Build

`build-test.bat` / `build-test.sh` validieren ohne das Repository zu ändern.
`build.bat` / `build.sh` führen zuerst diese Validierung aus und
synchronisieren nur bei Erfolg native Version, Manifest und `CHANGELOG.md`.
Vor einer realen ROS-2-Validierung gibt es keinen Hardware-`run`-Befehl.
