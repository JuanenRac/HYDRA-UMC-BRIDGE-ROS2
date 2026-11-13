<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Named goals for Nav2 and MoveIt (extension `motion`)
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# Named goals for Nav2 and MoveIt

## Design

Until now the bridge could only plan a job and call the safe-stop service. The extension `motion` lets a HYDRA-UMC job send a robot of the ROS 2 graph to a **named place**: a Nav2 waypoint on `hydra/bridges/ros2/cmd/nav_goal`
(`nav2_msgs/NavigateToPose`) or a MoveIt joint target on `hydra/bridges/ros2/cmd/move_named` (`moveit_msgs/MoveGroup`). **This extension can move a machine.** It is off by default and nothing about it is implicit.

What a command must pass, in this order (any refusal is an answer on `<command>/result`, and nothing reaches the robot):

1. **The switch.** `extensions.motion.enabled`. At the start the bridge must also follow the global stop of the server (`HYDRA_UMC_ROS2_GLOBAL_STOP=1`), or the service does not start (`require_global_stop: false` lifts this and is the
   operator's decision). While the server says `SAFE_STOP`, or its announcement cannot be read, every command is refused; an `ABORT` still goes through.
2. **A signed confirmation.** The command is a `BridgeJob` signed with the signed commands of the SDK (HMAC-SHA256 over the topic and the job, an expiry, a key in a file only the service can read; at least 32 characters). Unsigned,
   forged, altered, expired, future-dated or too-long-lived commands are refused, and so is a repeated idempotency key. There is no switch that turns the signature off, and a missing or unusable key stops the start.
3. **The gate of the SDK.** `evaluate_job` with the cell state of the bridge and the state of the machine **as the bridge observed it** on the state topic (and expired after `node.observed_max_age_s`), never the one written in the
   job: the cell must be `READY` and the machine `IDLE`. A machine that was never heard counts as `OFFLINE`.
4. **One motion at a time.** While a goal runs, the next command is refused.
5. **A name, not a place.** The command carries `parameters.target`, a name that the operator wrote in a file. There are no coordinates and no joint values in a command, so a message cannot ask for a place that was not listed.
6. **A live action server and a deadline.** If the action server is not up now the command is refused (nothing is queued for later). A goal with no result after `goal_timeout_s` is cancelled.

An `ABORT` job on either topic cancels the running goal and, like `cmd/job`, calls the safe-stop service when `node.abort_calls_safe_stop` is on; the answer says what was done. After `sent`, the statuses `accepted`, `succeeded`, `failed`,
`canceled`, `rejected` or `timeout` are published on the same result topic. The extension publishes no velocity and no trajectory of its own: Nav2 and MoveIt plan and move.

## Settings (`extensions.motion` in `docs/deploy/bridge.json`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | switches the extension on |
| `command_key_file` | `""` | the key of the signed commands (required) |
| `command_max_skew_ms` / `command_max_expires_ms` | `5000` / `30000` | clock difference and longest life of a command that are accepted |
| `require_global_stop` | `true` | the bridge must follow the global stop of the server |
| `goal_timeout_s` | `120` | a goal without a result after this long is cancelled |
| `nav2_enabled`, `nav2_action`, `nav2_frame`, `nav2_waypoints_file` | `false`, `navigate_to_pose`, `map`, `""` | the Nav2 side and its list |
| `moveit_enabled`, `moveit_action`, `moveit_targets_file` | `false`, `move_action`, `""` | the MoveIt side and its list |
| `moveit_velocity_scale`, `moveit_acceleration_scale`, `moveit_planning_time_s` | `0.1`, `0.1`, `5.0` | MoveIt scaling (above 0, at most 1) and planning time |

The lists are JSON files of the operator. Waypoints (metres in the frame, yaw in radians; at most 1000 m and 2 pi):

```json
{ "waypoints": { "dock_a": { "x": 1.5, "y": -2.0, "yaw": 1.57 } } }
```

MoveIt targets (joint values in radians, at most 16 joints a target):

```json
{ "targets": { "park": { "group": "arm", "joints": { "joint1": 0.0, "joint2": -1.2 } } } }
```

A command is the job of the SDK with the signature fields: `sign_command(topic, job_to_dict(job), key)` of `hydra_umc_sdk.signed_commands`, with `parameters` `{"target": "dock_a"}` and any productive phase (`PROCESS`, for example).

## Evidence

Simulation only. Host tests (`tests/test_ext_motion.py`) cover the settings and lists, every refusal in the chain, the global stop, the signatures (forged, altered, expired, future, wrong topic), one motion at a time, the timeout, the abort, the
adapters against fake action clients and a seeded stream of 400 random commands that must send a goal exactly when every condition holds. The workflow "ROS 2 bench" runs `tests/ros2/test_ext_motion_ros2.py` in the `ros:jazzy-ros-base` container:
real action clients against fake `NavigateToPose` and `MoveGroup` servers of the test (accept, succeed, abort, reject, hang until cancelled, absent), the real safe-stop service, and a seeded stream with injected faults.
**Not done:** a real Nav2 stack or a real MoveIt with a robot. That test must show that every name in the lists is a safe place for the real robot, that cancel plus the safe stop halts it as fast as its safety path needs, and that
the real clocks stay within the allowed skew.

## User guide

### English
This is the only extension that can make a robot move, so it takes several steps to turn on. Write the key file (at least 32 characters, readable only by the service), the list of named places, and add the `motion` section to `docs/deploy/bridge.json`;
start the service with `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. A job then names a place (`"target": "dock_a"`) and must be signed; it is refused while the server announces a global stop, while the cell or the machine is not ready, or while another motion runs.
An abort cancels the goal and calls the safe stop. `python -m hydra_umc_bridge_ros2 --check` tells you if the section or a list is wrong.

### Español
Es la única extensión que puede hacer que un robot se mueva, así que tiene varios pasos. Crea el archivo de clave (al menos 32 caracteres, legible solo por el servicio), la lista de lugares con nombre y añade la sección `motion` a
`docs/deploy/bridge.json`; arranca el servicio con `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. Un trabajo nombra entonces un lugar (`"target": "dock_a"`) y debe ir firmado; se rechaza mientras el servidor anuncia una parada global, mientras la celda o la
máquina no estén listas o mientras se ejecute otro movimiento. Un aborto cancela el objetivo y llama a la parada segura. `python -m hydra_umc_bridge_ros2 --check` te dice si la sección o una lista están mal.

### Deutsch
Dies ist die einzige Erweiterung, die einen Roboter bewegen kann, daher braucht das Einschalten mehrere Schritte. Legen Sie die Schlüsseldatei an (mindestens 32 Zeichen, nur für den Dienst lesbar), die Liste der benannten Orte und fügen Sie den
Abschnitt `motion` in `docs/deploy/bridge.json` ein; starten Sie den Dienst mit `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. Ein Auftrag nennt dann einen Ort (`"target": "dock_a"`) und muss signiert sein; er wird abgelehnt, solange der Server einen globalen Stopp
meldet, solange Zelle oder Maschine nicht bereit sind oder eine andere Bewegung läuft. Ein Abbruch storniert das Ziel und ruft den Sicherheitsstopp auf. `python -m hydra_umc_bridge_ros2 --check` meldet, wenn der Abschnitt oder eine Liste falsch ist.

### Français
C'est la seule extension qui peut faire bouger un robot ; son activation demande donc plusieurs étapes. Créez le fichier de clé (au moins 32 caractères, lisible uniquement par le service), la liste des lieux nommés et ajoutez la section `motion` à
`docs/deploy/bridge.json` ; démarrez le service avec `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. Un travail nomme alors un lieu (`"target": "dock_a"`) et doit être signé ; il est refusé tant que le serveur annonce un arrêt global, tant que la cellule ou la machine
n'est pas prête ou tant qu'un autre mouvement est en cours. Un abandon annule l'objectif et appelle l'arrêt sûr. `python -m hydra_umc_bridge_ros2 --check` indique si la section ou une liste est incorrecte.

### Italiano
È l'unica estensione che può far muovere un robot, quindi richiede più passaggi. Crea il file della chiave (almeno 32 caratteri, leggibile solo dal servizio), l'elenco dei luoghi con nome e aggiungi la sezione `motion` a `docs/deploy/bridge.json`;
avvia il servizio con `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. Un lavoro indica allora un luogo (`"target": "dock_a"`) e deve essere firmato; viene rifiutato finché il server annuncia un arresto globale, finché la cella o la macchina non sono pronte o
finché un altro movimento è in corso. Un annullamento cancella l'obiettivo e chiama l'arresto sicuro. `python -m hydra_umc_bridge_ros2 --check` ti dice se la sezione o un elenco è sbagliato.

### 日本語
ロボットを動かせる唯一の拡張なので、有効にするには複数の手順が必要です。キーファイル（32 文字以上、サービスだけが読める）と名前付きの場所の一覧を作り、`docs/deploy/bridge.json` に `motion` セクションを追加し、
`HYDRA_UMC_ROS2_GLOBAL_STOP=1` でサービスを起動します。ジョブは場所の名前（`"target": "dock_a"`）を指定し、署名が必要です。サーバーがグローバル停止を通知している間、セルまたは機械の準備ができていない間、
別の動作が実行中の間は拒否されます。中止はゴールをキャンセルしてセーフストップを呼び出します。セクションや一覧が誤っている場合は `python -m hydra_umc_bridge_ros2 --check` で分かります。

### 中文
这是唯一能让机器人移动的扩展，因此启用需要几个步骤。创建密钥文件（至少 32 个字符，仅服务可读）和具名地点列表，并在 `docs/deploy/bridge.json` 中加入 `motion` 配置段；
以 `HYDRA_UMC_ROS2_GLOBAL_STOP=1` 启动服务。随后作业需指明地点（`"target": "dock_a"`）并且必须签名；在服务器发布全局停止、单元或机器未就绪、或另一个运动正在进行时，作业会被拒绝。
中止会取消目标并调用安全停止。若配置段或列表有误，`python -m hydra_umc_bridge_ros2 --check` 会给出提示。

### Português
É a única extensão que pode fazer um robô mover-se, por isso exige vários passos. Crie o ficheiro da chave (pelo menos 32 carateres, legível apenas pelo serviço), a lista de locais com nome e acrescente a secção `motion` a
`docs/deploy/bridge.json`; arranque o serviço com `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. Um trabalho indica então um local (`"target": "dock_a"`) e tem de ser assinado; é recusado enquanto o servidor anunciar uma paragem global, enquanto a célula
ou a máquina não estiverem prontas ou enquanto outro movimento decorrer. Um aborto cancela o objetivo e chama a paragem segura. `python -m hydra_umc_bridge_ros2 --check` indica se a secção ou uma lista está errada.
