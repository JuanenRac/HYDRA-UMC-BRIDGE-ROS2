<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Pont de coordination bidirectionnel ROS 2
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# HYDRA-UMC-BRIDGE-ROS2

[🇺🇸 English](README.md) | [🇪🇸 Español](README_spa.md) | 🇫🇷 **Français** | [🇮🇹 Italiano](README_ita.md) | [🇩🇪 Deutsch](README_deu.md) | [🇨🇳 简体中文](README_zho.md) | [🇯🇵 日本語](README_jpn.md)

Limite de coordination bidirectionnelle de haut niveau entre HYDRA-UMC et ROS 2.
Elle associe l'observation continue à un topic, l'inspection immédiate à un
service et le travail long à une action annulable. Elle ne peut contourner ni
HYDRA-UMC-SERVER, ni les limites MCU, watchdogs ou E-STOP.

## Architecture

```text
Nœuds ROS 2 <-> BRIDGE-ROS2 <-> HYDRA-UMC-SDK <-> SERVER <-> sécurité MCU
```

`/hydra_umc/machine_state` publie l'état, `/hydra_umc/inspect_cell` réalise
l'inspection courte et `/hydra_umc/execute_cell_job` porte le travail annulable.
Chaque travail possède une clé d'idempotence. Les phases productives exigent
une machine `IDLE` et une cellule `READY`; `ABORT` reste disponible en cas de panne.

## Compiler et tester

Exécutez `build-test.bat` sous Windows ou `bash build-test.sh` sous Linux. Ils
compilent et exécutent des tests sans modifier version ni CHANGELOG. rclpy,
les contrats ROS et DDS seront ajoutés après test d'un environnement ROS 2 réel.

## Projets liés

| Projet | Rôle |
| --- | --- |
| [HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK) | Contrat partagé de travail et sécurité. |
| [HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER) | Limite authentifiée de l'écosystème. |
| [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE) | Chemin de preuve hardware-in-the-loop. |

## État

La version `0.0.1` est fonctionnelle comme noyau sans dépendance avec tests de
sécurité locaux. Aucun réseau ROS, robot ni actionneur physique n'est validé.

## ⚙️ Compilation versionnée

`build-test.bat` / `build-test.sh` valident sans modifier le dépôt.
`build.bat` / `build.sh` exécutent d'abord cette validation puis, uniquement
en cas de succès, synchronisent version native, manifeste et `CHANGELOG.md`.
Il n'existe pas de commande `run` matériel avant validation ROS 2 réelle.
