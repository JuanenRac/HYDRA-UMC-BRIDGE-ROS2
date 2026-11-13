<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - The cell status feed (extension `cell_status`)
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# The cell status feed

## Design

HYDRA-UMC-SERVER announces its robots on the MQTT broker (retained): `hydra/swarm/robot_<id>/state`, its own presence on `hydra/swarm/status` and the global stop on `hydra/swarm/safety/estop`. A ROS 2 node that wants to know
whether a cell is ready, running or stopped should not have to speak MQTT. The extension `cell_status` reads those topics and publishes `hydra_umc_interfaces/msg/CellStatus` (the message the interface package already
defines) on `/hydra_umc/cells/robot_<id>/status`, one topic per cell, with the `transient_local` durability of the state topic of the bridge, so a node that starts later still gets the last state.

* **Read only in both directions.** MQTT is only subscribed to: nothing is published back and no command goes to the server. On the ROS 2 side it makes publishers of one message type and nothing else (no
  subscription, service, client or action), so a ROS 2 node cannot use the feed to move anything.
* **Fail closed.** Until the server has said `online` and the global stop has been heard, every cell is `INHIBITED`; a global stop that says `SAFE_STOP` or cannot be read makes every cell `SAFE_STOP` (an online machine is then
  `SAFE_STOP`); a state message that cannot be read, or an empty one (the server clearing a retained topic), makes that cell `OFFLINE` and `INHIBITED` instead of keeping the last good state. A server that does not announce
  the global stop needs `estop_required` set to `false`.
* **Only what is known.** machine: not online is `OFFLINE`; playing and paused is `HOLDING`; playing is `RUNNING`; otherwise `IDLE`. `FAULT` is never reported and `job_id` stays empty, because the summary of the server holds
  neither a fault nor a job.
* **Bounded.** At most `max_cells` cells (a new one over the limit is not published); only names like `robot_<letters or digits>` become ROS 2 topics; a record is published only when it changes.

## Settings (`extensions.cell_status` in `docs/deploy/bridge.json`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | switches the extension on |
| `swarm_prefix` | `hydra/swarm` | the prefix the server publishes under (`mqtt.prefix` of the server) |
| `ros_prefix` | `/hydra_umc/cells` | where the ROS 2 topics are made |
| `max_cells` | `32` | cells kept (1 to 256) |
| `estop_required` | `true` | every cell is `INHIBITED` until the global stop announcement has been heard |
| `server_required` | `true` | every cell is `INHIBITED` until the server has said it is online |

The broker must let the credential of the bridge subscribe below the swarm prefix. The node needs the `hydra_umc_interfaces` package built and sourced.

## Evidence

Simulation only. Host tests (`tests/test_ext_cell_status.py`) cover the derivation of both states, every fail-closed path, the limits and a seeded random stream that checks that no cell is ever `READY` during a stop and that nothing is
published back. The workflow "ROS 2 bench" runs `tests/ros2/test_ext_cell_status_ros2.py` in the `ros:jazzy-ros-base` container: the server is played by the test (a seeded sequence with unreadable messages, a cleared topic, a
global stop and more cells than allowed), a real subscriber and a late one read the real messages, and the node is checked to have no subscription, service or client. **Not done:** a run against the real server and broker with their
real rates, and a real ROS 2 node of a robot reading the feed.

## User guide

### English
Turn it on by adding `"extensions": { "cell_status": { "enabled": true } }` to `docs/deploy/bridge.json` and restart the service. Each HYDRA-UMC robot then appears on ROS 2 as `/hydra_umc/cells/robot_<id>/status` with its cell state
(ready, inhibited, safe stop) and machine state (idle, running, holding, offline, safe stop). It only reports: a ROS 2 node cannot move or stop anything through it. If a cell shows INHIBITED, the server is not online or its
global stop has not been announced yet; if your server does not announce the global stop, set `estop_required` to `false`.

### Español
Actívala añadiendo `"extensions": { "cell_status": { "enabled": true } }` a `docs/deploy/bridge.json` y reinicia el servicio. Cada robot de HYDRA-UMC aparecerá entonces en ROS 2 como `/hydra_umc/cells/robot_<id>/status`
con el estado de su celda (lista, inhibida, parada segura) y el de su máquina (en reposo, en marcha, en pausa, desconectada, parada segura). Solo informa: un nodo ROS 2 no puede mover ni detener nada con ella. Si una celda
aparece INHIBITED, el servidor no está en línea o aún no se ha anunciado su parada global; si tu servidor no anuncia la parada global, pon `estop_required` a `false`.

### Deutsch
Schalten Sie es ein, indem Sie `"extensions": { "cell_status": { "enabled": true } }` in `docs/deploy/bridge.json` eintragen und den Dienst neu starten. Jeder HYDRA-UMC-Roboter erscheint dann in ROS 2 als
`/hydra_umc/cells/robot_<id>/status` mit dem Zustand seiner Zelle (bereit, gesperrt, Sicherheitsstopp) und seiner Maschine (Leerlauf, läuft, angehalten, offline, Sicherheitsstopp). Es meldet nur: ein ROS-2-Knoten kann damit nichts
bewegen oder stoppen. Zeigt eine Zelle INHIBITED, ist der Server nicht online oder sein globaler Stopp wurde noch nicht angekündigt; kündigt Ihr Server ihn nicht an, setzen Sie `estop_required` auf `false`.

### Français
Activez-la en ajoutant `"extensions": { "cell_status": { "enabled": true } }` à `docs/deploy/bridge.json`, puis redémarrez le service. Chaque robot HYDRA-UMC apparaît alors dans ROS 2 sous
`/hydra_umc/cells/robot_<id>/status` avec l'état de sa cellule (prête, inhibée, arrêt sûr) et de sa machine (au repos, en marche, en pause, hors ligne, arrêt sûr). Elle ne fait que renseigner : un nœud ROS 2 ne peut rien déplacer ni
arrêter par ce biais. Si une cellule affiche INHIBITED, le serveur n'est pas en ligne ou son arrêt global n'a pas encore été annoncé ; si votre serveur ne l'annonce pas, mettez `estop_required` à `false`.

### Italiano
Attivala aggiungendo `"extensions": { "cell_status": { "enabled": true } }` a `docs/deploy/bridge.json` e riavvia il servizio. Ogni robot HYDRA-UMC comparirà allora in ROS 2 come `/hydra_umc/cells/robot_<id>/status` con lo stato
della sua cella (pronta, inibita, arresto sicuro) e della sua macchina (a riposo, in marcia, in pausa, offline, arresto sicuro). Segnala soltanto: un nodo ROS 2 non può muovere né fermare nulla attraverso di essa. Se una cella mostra
INHIBITED, il server non è online o il suo arresto globale non è ancora stato annunciato; se il tuo server non lo annuncia, imposta `estop_required` su `false`.

### 日本語
`docs/deploy/bridge.json` に `"extensions": { "cell_status": { "enabled": true } }` を追加し、サービスを再起動すると有効になります。各 HYDRA-UMC ロボットは ROS 2 上で `/hydra_umc/cells/robot_<id>/status` として現れ、セルの状態
（準備完了、抑止中、セーフストップ）と機械の状態（待機、動作中、保留、オフライン、セーフストップ）を示します。報告のみで、ROS 2 ノードはこれを使って何も動かしたり止めたりできません。セルが INHIBITED の場合は、サーバーがオンラインでないか、
グローバル停止がまだ通知されていません。サーバーがグローバル停止を通知しない場合は `estop_required` を `false` にしてください。

### 中文
在 `docs/deploy/bridge.json` 中加入 `"extensions": { "cell_status": { "enabled": true } }` 并重启服务即可启用。每台 HYDRA-UMC 机器人随后会在 ROS 2 中以 `/hydra_umc/cells/robot_<id>/status` 出现，
给出其单元状态（就绪、被禁止、安全停止）和机器状态（空闲、运行、暂停、离线、安全停止）。它只负责报告：ROS 2 节点无法通过它移动或停止任何东西。若某个单元显示 INHIBITED，说明服务器不在线或尚未发布全局停止通告；
如果您的服务器不发布全局停止，请将 `estop_required` 设为 `false`。

### Português
Ative-a acrescentando `"extensions": { "cell_status": { "enabled": true } }` a `docs/deploy/bridge.json` e reinicie o serviço. Cada robô do HYDRA-UMC passa então a aparecer no ROS 2 como `/hydra_umc/cells/robot_<id>/status`
com o estado da sua célula (pronta, inibida, paragem segura) e da sua máquina (em repouso, em marcha, em pausa, desligada, paragem segura). Apenas informa: um nó ROS 2 não pode mover nem parar nada através dela. Se uma célula
mostrar INHIBITED, o servidor não está em linha ou a sua paragem global ainda não foi anunciada; se o seu servidor não a anuncia, ponha `estop_required` a `false`.
