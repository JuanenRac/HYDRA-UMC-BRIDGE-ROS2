<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - The diagnostics feed (extension `diagnostics`)
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# The diagnostics feed

## Design

ROS 2 drivers and the `diagnostic_aggregator` report their health on one standard topic, `/diagnostics` (`diagnostic_msgs/DiagnosticArray`). HYDRA-UMC had no way to see it. The extension
`diagnostics` reads that topic and publishes a bounded summary on `hydra/bridges/ros2/diagnostics` (retained), where the data lake, the dashboards and the studio can read it like the
`state` of any other bridge.

* **Read only.** One ROS 2 subscription, one MQTT topic. No ROS publisher, service or action client, no command topic, nothing that reaches the gate of the jobs: an `ERROR` in the
  diagnostics does not refuse a job and an `OK` does not allow one. (Turning a diagnostic into a stop is a decision about safety that belongs to the safety path, not to a monitoring feed.)
* **Bounded.** The last status of each name is kept, at most `max_statuses` names (a new name over the limit is counted in `dropped_statuses`); texts are cleaned of control characters and cut at
  `max_text_chars`; the whole summary is cut to 100000 characters (the key/value pairs go first, then the end of the list).
* **Honest about silence.** If nothing was heard for `stale_after_s` the `health` is `STALE` with a `reason`; a driver that stopped talking is never shown as healthy. A level that is not
  OK, WARN, ERROR or STALE is read as `ERROR`.
* **Private by default.** The key/value pairs of a status can hold anything a driver prints, so they are left out unless `include_values` is `true`; even then the value of a key that looks like
  a secret (`pass`, `secret`, `token`, `key`, `credential`, `auth`) is replaced by `***`.

Summary (JSON): `observed_at` (ms since the epoch), `health` (`OK`, `WARN`, `ERROR`, `STALE`), `counts` per level, `dropped_statuses`, `statuses` (`name`, `level`, `message`,
`hardware_id`, and `values` when asked) and, when silent, `reason`.

## Settings (`extensions.diagnostics` in `docs/deploy/bridge.json`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | switches the extension on |
| `topic` | `/diagnostics` | the ROS 2 topic that is read |
| `publish_interval_s` | `5.0` | seconds between two summaries |
| `stale_after_s` | `30.0` | silence after which the health is `STALE` |
| `max_statuses` | `64` | names kept (1 to 1000) |
| `max_values` | `16` | key/value pairs per status when `include_values` is on |
| `max_text_chars` | `200` | longest text kept |
| `include_values` | `false` | publish the key/value pairs |
| `name_prefixes` | `[]` | keep only the statuses whose name starts with one of these |

The broker must let the credential of the bridge publish on `hydra/bridges/ros2/diagnostics`. The container image needs `ros-<distro>-diagnostic-msgs`.

## Evidence

Simulation only. Host tests (`tests/test_ext_diagnostics.py`, `tests/test_extensions.py`) cover the summary, the limits, the silence, the cleaning, a seeded random stream and the proof that the
extension claims no command topic. The workflow "ROS 2 bench" runs `tests/ros2/test_ext_diagnostics_ros2.py` in the `ros:jazzy-ros-base` container: a real node publishes a seeded stream of real
`DiagnosticArray` messages, with injected faults (a level that does not exist, 200 names, a text of 5000 characters, a secret-looking key) and a silent period, and the test checks that the node gains no publisher,
service or client. **Not done:** a run against the drivers and the aggregator of a real robot, with their real rates and message sizes.

## User guide

### English
Turn it on by adding `"extensions": { "diagnostics": { "enabled": true } }` to `docs/deploy/bridge.json` and restart the service. HYDRA-UMC then shows the health that your ROS 2 drivers report on `/diagnostics`
(OK, warning, error, or stale when they went quiet) on the topic `hydra/bridges/ros2/diagnostics`. It only reads: it never moves anything and never stops anything. Details such as key/value pairs stay off unless you set `include_values` to `true`.
`python -m hydra_umc_bridge_ros2 --check` tells you if the section is wrong.

### Español
Actívala añadiendo `"extensions": { "diagnostics": { "enabled": true } }` a `docs/deploy/bridge.json` y reinicia el servicio. HYDRA-UMC mostrará entonces la salud que tus controladores ROS 2 comunican en `/diagnostics`
(correcto, aviso, error o caducado si dejaron de hablar) en el tema `hydra/bridges/ros2/diagnostics`. Solo lee: no mueve ni detiene nada. Los detalles clave/valor siguen apagados salvo que pongas `include_values` a `true`.
`python -m hydra_umc_bridge_ros2 --check` te dice si la sección está mal.

### Deutsch
Schalten Sie es ein, indem Sie `"extensions": { "diagnostics": { "enabled": true } }` in `docs/deploy/bridge.json` eintragen und den Dienst neu starten. HYDRA-UMC zeigt dann den Zustand, den Ihre ROS-2-Treiber auf `/diagnostics` melden
(in Ordnung, Warnung, Fehler oder veraltet, wenn sie verstummt sind), auf dem Thema `hydra/bridges/ros2/diagnostics`. Es liest nur: es bewegt und stoppt nichts. Schlüssel-Wert-Details bleiben aus, bis Sie `include_values` auf `true` setzen.
`python -m hydra_umc_bridge_ros2 --check` meldet, wenn der Abschnitt falsch ist.

### Français
Activez-la en ajoutant `"extensions": { "diagnostics": { "enabled": true } }` à `docs/deploy/bridge.json`, puis redémarrez le service. HYDRA-UMC affiche alors l'état que vos pilotes ROS 2 signalent sur `/diagnostics`
(correct, avertissement, erreur, ou périmé s'ils se sont tus) sur le sujet `hydra/bridges/ros2/diagnostics`. Elle ne fait que lire : elle ne déplace et n'arrête rien. Les détails clé/valeur restent désactivés tant que `include_values` n'est pas `true`.
`python -m hydra_umc_bridge_ros2 --check` indique si la section est incorrecte.

### Italiano
Attivala aggiungendo `"extensions": { "diagnostics": { "enabled": true } }` a `docs/deploy/bridge.json` e riavvia il servizio. HYDRA-UMC mostrerà allora lo stato che i tuoi driver ROS 2 riportano su `/diagnostics`
(corretto, avviso, errore o scaduto se hanno smesso di parlare) sul topic `hydra/bridges/ros2/diagnostics`. Legge soltanto: non muove e non ferma nulla. I dettagli chiave/valore restano spenti finché non imposti `include_values` a `true`.
`python -m hydra_umc_bridge_ros2 --check` ti dice se la sezione è sbagliata.

### 日本語
`docs/deploy/bridge.json` に `"extensions": { "diagnostics": { "enabled": true } }` を追加し、サービスを再起動すると有効になります。HYDRA-UMC は、ROS 2 のドライバが `/diagnostics` で報告する状態
（正常、警告、エラー、応答が途絶えた場合は期限切れ）をトピック `hydra/bridges/ros2/diagnostics` に表示します。読み取り専用で、何も動かさず、何も止めません。キーと値の詳細は `include_values` を `true` にするまでオフです。
設定が誤っている場合は `python -m hydra_umc_bridge_ros2 --check` で分かります。

### 中文
在 `docs/deploy/bridge.json` 中加入 `"extensions": { "diagnostics": { "enabled": true } }` 并重启服务即可启用。随后 HYDRA-UMC 会在主题 `hydra/bridges/ros2/diagnostics` 上显示 ROS 2 驱动在 `/diagnostics` 上报告的健康状态
（正常、警告、错误；若驱动不再发送则为过期）。它只读取：不会移动或停止任何东西。键值详情默认关闭，除非将 `include_values` 设为 `true`。
若该配置段有误，`python -m hydra_umc_bridge_ros2 --check` 会给出提示。

### Português
Ative-a acrescentando `"extensions": { "diagnostics": { "enabled": true } }` a `docs/deploy/bridge.json` e reinicie o serviço. O HYDRA-UMC passa então a mostrar o estado que os seus controladores ROS 2 comunicam em `/diagnostics`
(correto, aviso, erro ou expirado, se deixaram de falar) no tópico `hydra/bridges/ros2/diagnostics`. Só lê: não move nem pára nada. Os detalhes chave/valor ficam desligados, a não ser que defina `include_values` como `true`.
`python -m hydra_umc_bridge_ros2 --check` indica se a secção está errada.
