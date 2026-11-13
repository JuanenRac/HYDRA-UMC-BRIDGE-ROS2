<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - One entry for a mixed fleet (extension `fleet`)
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# One entry for a mixed fleet

## Design

A plant can have ROS 2 vehicles and AGVs or AMRs that speak VDA 5050, and HYDRA-UMC-BRIDGE-AMR already serves the second kind. The extension `fleet` gives the orchestrator **one** job topic for both,
`hydra/bridges/ros2/cmd/fleet_job`, and one view of the fleet, `hydra/bridges/ros2/fleet/state`. **This extension can move something.** It is off by default.

A job names a `vehicle` and, for a movement, a `station`. Both are names that the operator wrote in a file (`fleet_file`); no coordinate and no node id travels in a command.

```json
{ "vehicles": {
    "amr_1": { "kind": "vda5050", "stations": { "dock_a": "N12", "home": "N1" }, "topic_prefix": "hydra/bridges/amr/" },
    "arm_1": { "kind": "ros2" } } }
```

* **A `ros2` vehicle** is served by this bridge: the job goes to its own `cmd/job` (its gate on the machine state observed on the ROS 2 state topic, its idempotency, and the safe-stop call of an `ABORT`).
  That entry is a plan: a motion of a ROS 2 vehicle is the extension `motion` (named goals for Nav2 and MoveIt).
* **A `vda5050` vehicle** is served by the AMR bridge. This extension gates the job with the machine state THAT bridge published on `<prefix>state` (needs `HYDRA_UMC_AMR_PUBLISH_STATE=1` there). A state that was never heard, is older
  than `amr_state_max_age_s`, is unreadable or unknown counts as `OFFLINE`. If the gate allows it, a new job is signed with the key of the AMR bridge and published on `<prefix>cmd/job` at QoS 1 (a movement carries the
  `goal_node` of the station; `LOAD` and `UNLOAD` carry none). The AMR bridge then gates it again with what the vehicle itself reported and answers on `<prefix>cmd/job/result`; the answer is relayed on `cmd/fleet_job/result`.
  The AMR bridge must ask for signed commands (`HYDRA_UMC_AMR_SIGNED_COMMANDS`) and have its job entry on (`HYDRA_UMC_AMR_JOB_ENTRY=1`); otherwise it simply refuses or ignores the job.
* **An `ABORT`** for one vehicle, or for `*` (or no vehicle), goes to each: a cancel job to the AMR bridge, the safe-stop call to the ROS 2 side. An abort needs no idle vehicle.

What a command passes before anything is forwarded, in this order: the switch, and a bridge that follows the global stop of the server (`require_global_stop`; while the server says `SAFE_STOP` or cannot be read, a job is refused and an
abort goes through); a signature of the SDK signed commands (`command_key_file`; no switch turns it off, a missing key stops the start); the idempotency of the key; a known vehicle; for a `vda5050` vehicle the gate of the SDK with the
cell state of the bridge and the state of the vehicle, and a station of its list.

## Settings (`extensions.fleet` in `docs/deploy/bridge.json`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | switches the extension on |
| `fleet_file` | `""` | the list of vehicles and stations (required) |
| `command_key_file` | `""` | key of the signed commands this bridge asks for (required) |
| `command_max_skew_ms` / `command_max_expires_ms` | `5000` / `30000` | clock difference and longest life of a command that are accepted |
| `amr_command_key_file` | `""` | key this bridge signs with towards an AMR bridge (required when the fleet has a `vda5050` vehicle) |
| `amr_state_max_age_s` | `15` | age after which the state of an AMR bridge counts as `OFFLINE` |
| `require_global_stop` | `true` | the bridge must follow the global stop of the server |
| `state_interval_s` | `5` | seconds between two `fleet/state` messages |

`fleet/state` (retained): `{"observed_at": ms, "vehicles": {"amr_1": {"kind": "vda5050", "machine_state": "IDLE"}, ...}}`. The broker must let the bridge subscribe to the `state` and `cmd/job/result` topics of the AMR bridges and publish on their `cmd/job`.

## Evidence

Simulation only. Host tests (`tests/test_ext_fleet.py`) cover the settings and the fleet file, every refusal in the chain, the signed job towards the AMR bridge (verified with the SDK), the state that gates it, the relay of the answer, the abort for
one vehicle and for all, and a seeded stream of 400 random commands against a simulated AMR bridge that checks the signature and its own gate. The workflow "ROS 2 bench" runs `tests/ros2/test_ext_fleet_ros2.py` in the
`ros:jazzy-ros-base` container: a ROS 2 vehicle on a real graph (the real state topic and the real safe-stop service) and an AMR played by a simulated AMR bridge, with seeded commands and injected faults (forged, stale or busy vehicle, global stop).
**Not done:** a run with HYDRA-UMC-BRIDGE-AMR itself, a VDA 5050 fleet manager and a real vehicle, and a real ROS 2 vehicle; the simulated AMR bridge of the test is not the real one.

## User guide

### English
Use it when your plant mixes ROS 2 vehicles with VDA 5050 AGVs or AMRs. Write the list of vehicles and their stations in a file, create two key files (one for the commands you receive, one for the jobs sent to the AMR bridge), add the
`fleet` section to `docs/deploy/bridge.json` and start the service with `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. The AMR bridge needs its job entry and signed commands on, and its state published. A job then names a vehicle and a station
(`"vehicle": "amr_1", "station": "dock_a"`) and must be signed. An abort with vehicle `*` stops everything. `python -m hydra_umc_bridge_ros2 --check` tells you if the section or the list is wrong.

### Español
Úsala cuando tu planta mezcla vehículos ROS 2 con AGV o AMR de VDA 5050. Escribe en un archivo la lista de vehículos y sus estaciones, crea dos archivos de clave (uno para las órdenes que recibes y otro para los trabajos enviados al
bridge AMR), añade la sección `fleet` a `docs/deploy/bridge.json` y arranca el servicio con `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. El bridge AMR necesita su entrada de trabajos y las órdenes firmadas activadas, y su estado publicado. Un trabajo nombra
entonces un vehículo y una estación (`"vehicle": "amr_1", "station": "dock_a"`) y debe ir firmado. Un aborto con vehículo `*` lo detiene todo. `python -m hydra_umc_bridge_ros2 --check` te dice si la sección o la lista están mal.

### Deutsch
Nutzen Sie sie, wenn Ihre Anlage ROS-2-Fahrzeuge mit VDA-5050-FTS oder -AMR mischt. Schreiben Sie die Liste der Fahrzeuge und ihrer Stationen in eine Datei, legen Sie zwei Schlüsseldateien an (eine für die empfangenen Befehle, eine für die an den
AMR-Bridge gesendeten Aufträge), fügen Sie den Abschnitt `fleet` in `docs/deploy/bridge.json` ein und starten Sie den Dienst mit `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. Der AMR-Bridge braucht seinen Auftragseingang und signierte Befehle, und sein Status muss veröffentlicht werden.
Ein Auftrag nennt dann ein Fahrzeug und eine Station (`"vehicle": "amr_1", "station": "dock_a"`) und muss signiert sein. Ein Abbruch mit Fahrzeug `*` stoppt alles. `python -m hydra_umc_bridge_ros2 --check` meldet, wenn der Abschnitt oder die Liste falsch ist.

### Français
Utilisez-la quand votre site mélange des véhicules ROS 2 avec des AGV ou AMR VDA 5050. Écrivez dans un fichier la liste des véhicules et de leurs stations, créez deux fichiers de clé (un pour les commandes reçues, un pour les travaux envoyés au bridge AMR),
ajoutez la section `fleet` à `docs/deploy/bridge.json` et démarrez le service avec `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. Le bridge AMR doit avoir son entrée de travaux et les commandes signées activées, et son état publié. Un travail nomme alors un véhicule
et une station (`"vehicle": "amr_1", "station": "dock_a"`) et doit être signé. Un abandon avec le véhicule `*` arrête tout. `python -m hydra_umc_bridge_ros2 --check` indique si la section ou la liste est incorrecte.

### Italiano
Usala quando il tuo impianto mescola veicoli ROS 2 con AGV o AMR VDA 5050. Scrivi in un file l'elenco dei veicoli e delle loro stazioni, crea due file di chiave (uno per i comandi che ricevi, uno per i lavori inviati al bridge AMR), aggiungi la sezione
`fleet` a `docs/deploy/bridge.json` e avvia il servizio con `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. Il bridge AMR deve avere attivi l'ingresso dei lavori e i comandi firmati, e il suo stato pubblicato. Un lavoro indica allora un veicolo e una stazione
(`"vehicle": "amr_1", "station": "dock_a"`) e deve essere firmato. Un annullamento con veicolo `*` ferma tutto. `python -m hydra_umc_bridge_ros2 --check` ti dice se la sezione o l'elenco è sbagliato.

### 日本語
工場で ROS 2 の車両と VDA 5050 の AGV/AMR が混在する場合に使います。車両とステーションの一覧をファイルに書き、2 つのキーファイル（受け取るコマンド用と、AMR ブリッジへ送るジョブ用）を作り、`docs/deploy/bridge.json` に `fleet` セクションを追加して、
`HYDRA_UMC_ROS2_GLOBAL_STOP=1` でサービスを起動します。AMR ブリッジ側でもジョブ入力と署名付きコマンドを有効にし、状態を公開しておく必要があります。ジョブは車両とステーション（`"vehicle": "amr_1", "station": "dock_a"`）を指定し、署名が必要です。
車両に `*` を指定した中止はすべてを停止します。セクションや一覧が誤っている場合は `python -m hydra_umc_bridge_ros2 --check` で分かります。

### 中文
当工厂同时有 ROS 2 车辆和 VDA 5050 的 AGV/AMR 时使用。把车辆及其站点的列表写入文件，创建两个密钥文件（一个用于接收的命令，一个用于发给 AMR 桥接的作业），在 `docs/deploy/bridge.json` 中加入 `fleet` 配置段，
并以 `HYDRA_UMC_ROS2_GLOBAL_STOP=1` 启动服务。AMR 桥接需要启用作业入口和签名命令，并发布其状态。随后作业需指明车辆和站点（`"vehicle": "amr_1", "station": "dock_a"`）并且必须签名。车辆为 `*` 的中止会停止全部。
若配置段或列表有误，`python -m hydra_umc_bridge_ros2 --check` 会给出提示。

### Português
Use-a quando a sua fábrica mistura veículos ROS 2 com AGV ou AMR VDA 5050. Escreva num ficheiro a lista de veículos e das suas estações, crie dois ficheiros de chave (um para os comandos que recebe, outro para os trabalhos enviados ao bridge AMR),
acrescente a secção `fleet` a `docs/deploy/bridge.json` e arranque o serviço com `HYDRA_UMC_ROS2_GLOBAL_STOP=1`. O bridge AMR precisa de ter a entrada de trabalhos e os comandos assinados ativos, e o seu estado publicado. Um trabalho indica então um veículo
e uma estação (`"vehicle": "amr_1", "station": "dock_a"`) e tem de ser assinado. Um aborto com o veículo `*` pára tudo. `python -m hydra_umc_bridge_ros2 --check` indica se a secção ou a lista está errada.
