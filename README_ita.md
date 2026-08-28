<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Bridge di coordinamento bidirezionale ROS 2
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# HYDRA-UMC-BRIDGE-ROS2

[🇺🇸 English](README.md) | [🇪🇸 Español](README_spa.md) | [🇫🇷 Français](README_fra.md) | 🇮🇹 **Italiano** | [🇩🇪 Deutsch](README_deu.md) | [🇨🇳 简体中文](README_zho.md) | [🇯🇵 日本語](README_jpn.md)

Confine di coordinamento bidirezionale ad alto livello tra HYDRA-UMC e ROS 2.
Mappa l'osservazione su topic, l'ispezione su servizio e il lavoro lungo su
un'azione annullabile. Non può aggirare SERVER, limiti MCU, watchdog o E-STOP.

## Architettura

```text
Nodi ROS 2 <-> BRIDGE-ROS2 <-> HYDRA-UMC-SDK <-> SERVER <-> sicurezza MCU
```

`/hydra_umc/machine_state` pubblica stato, `/hydra_umc/inspect_cell` ispeziona
e `/hydra_umc/execute_cell_job` esegue lavoro annullabile. Ogni lavoro ha una
chiave di idempotenza. Fasi produttive: macchina `IDLE`, cella `READY`; `ABORT`
resta disponibile durante un guasto.

## Compilare e testare

Eseguire `build-test.bat` su Windows o `bash build-test.sh` su Linux. Compilano
e testano senza modificare versione o CHANGELOG. rclpy, contratti ROS e DDS
saranno aggiunti solo dopo una prova ROS 2 reale.

## Progetti correlati

| Progetto | Ruolo |
| --- | --- |
| [HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK) | Contratto condiviso di lavoro e sicurezza. |
| [HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER) | Confine autenticato dell'ecosistema. |
| [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE) | Percorso hardware-in-the-loop. |

## Stato

La versione `0.0.1` è funzionale come nucleo senza dipendenze con test locali.
Nessuna rete ROS, robot o attuatore fisico è ancora validato.
