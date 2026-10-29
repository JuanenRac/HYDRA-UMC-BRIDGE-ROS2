<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - ROS 2 coordination bridge
Copyright (C) JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="HYDRA-UMC-BRIDGE-ROS2 banner" width="100%">
</p>

# 🐢 HYDRA-UMC-BRIDGE-ROS2

<p align="center"><a href="README.md">🇺🇸 English</a> | <a href="README_spa.md">🇪🇸 Español</a> | <a href="README_fra.md">🇫🇷 Français</a> | <a href="README_ita.md">🇮🇹 Italiano</a> | 🇩🇪 <b>Deutsch</b> | <a href="README_zho.md">🇨🇳 简体中文</a> | <a href="README_jpn.md">🇯🇵 日本語</a> | <a href="README_por.md">🇵🇹 Português</a></p>

### 🔗 Abhängigkeitsfreie Koordinationsgrenze zwischen HYDRA-UMC und ROS 2

<p align="center">
  <img src="https://img.shields.io/badge/License-GPL%203.0--or--later-blue.svg" alt="GPL 3.0 or later">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg" alt="Python">
  <img src="https://img.shields.io/badge/Safety-Fails%20Closed-red.svg" alt="Safety">
  <img src="https://img.shields.io/badge/Tests-81%20passing-2ea44f.svg" alt="Tests">
  <img src="https://img.shields.io/badge/Version-0.1.3-0f6cbd.svg" alt="Version">
  <img src="https://img.shields.io/badge/Maturity-functional-0F6CBD.svg" alt="Maturity">
</p>

---

> **Ehrlichkeitscheck - was heute wirklich läuft:** der abhängigkeitsfreie Koordinationskern (`coordinator.py` mit `Ros2Coordinator`, das jeden Dispatch durch das echte `evaluate_job()` von `HYDRA-UMC-SDK` leitet), der echte (lazy importierte) `rclpy`-Transport für `std_srvs/Trigger` und `std_msgs/String` (`rclpy_transport.py` mit `Ros2SafeStopClient`/`Ros2StateSubscriber`) und der MQTT-Befehls-/Statustransport (`mqtt_transport.py`) sind real und durch 81 bestehende Unit-Tests abgedeckt (`python tools/build_test.py` - `test_coordinator.py`, `test_rclpy_transport.py`, `test_mqtt_transport.py`, `test_intake.py`, `test_mqtt_environment.py`, `test_hardening.py`). Nichts davon wurde gegen eine echte ROS-2-Installation, ein echtes DDS-Netzwerk oder einen echten MQTT-Broker getestet - `test_rclpy_transport.py` läuft gegen einen simulierten Node/Publisher und `test_mqtt_transport.py` gegen einen simulierten Broker-Client, sodass `rclpy`/`std_srvs`/`std_msgs` für das Bestehen dieser Tests nicht einmal installiert sein müssen. Die benutzerdefinierten `.srv`/`.action`-Verträge `inspect_service`/`job_action` haben noch keinen echten Standard-ROS-2-Nachrichtentyp und überhaupt keinen Client. Siehe „Aktueller Status und nächste Schritte" weiter unten, das dies bereits klar sagt, sowie `CHANGELOG.md` für das, was bisher genau ausgeliefert wurde.

> **Stand von Sicherheit (Safety) und Security:** Dieses Ökosystem ist kein zertifiziertes Sicherheitssystem. Es gibt noch keinen Hardware-E-STOP-Pfad, keine von der Firmware durchgesetzten Grenzen oder Verriegelungen und keinen verifizierten Bewegungsbefehl; Firmware-Images werden mit CRC32 und einem HMAC-SHA256 mit gemeinsamem Schlüssel geprüft, nicht mit einer asymmetrischen Signatur; TLS und gegenseitiges TLS sind optional und standardmäßig aus; noch keine eigene Platine wurde bestückt oder getestet. Lesen Sie die [Sicherheitsbasis](https://github.com/JuanenRac/HYDRA-UMC-DOCS/blob/main/docs/SECURITY_BASELINE.md) und das [Bedrohungsmodell](https://github.com/JuanenRac/HYDRA-UMC-DOCS/blob/main/docs/THREAT_MODEL.md), bevor Sie sich für die Sicherheit einer Person auf eine Aussage hier verlassen.

---

## 📋 Auf einen Blick

| | |
|---|---|
| Version | `0.1.3` |
| Reifegrad | Funktionsfähig |
| Tests | 81 (`python tools/build_test.py`) |
| Technik | Python / ROS 2 / rclpy / HYDRA-UMC-SDK |
| Ziel | Der Raspberry Pi CM5 einer HYDRA-UMC-Zelle |
| Auf echter Hardware | Nur simuliert: gegen Emulatoren, Attrappen oder Simulatoren getestet; für diese Version ist kein Lauf auf echter Hardware vermerkt |

**Springe zu:** [Überblick](#-überblick) · [Architektur](#-architektur) · [Funktionen](#-funktionen) · [Sicherheit und Schutz](#-sicherheit-und-schutz) · [Schnittstellen](#-schnittstellen) · [Konfiguration](#-konfiguration) · [Schnellstart](#-schnellstart) · [Tests](#-tests) · [Fehlerbehebung](#-fehlerbehebung) · [Struktur des Repositorys](#-struktur-des-repositorys) · [Verwandte Projekte](#-verwandte-projekte)

## 🚦 Projektstatus

Jede Zeile ist ein Teil dieses Repositorys, und die Ampel sagt, wie weit er heute ist. Sie beruht auf dem, was das Repository zeigen kann (Tests, Builds, Läufe), nicht auf dem, was geplant ist.

🟢 Fertig und überprüft · 🟡 Funktioniert, mit bekannten Grenzen · 🟠 Teilweise fertig oder noch nicht überprüft · 🔴 Nicht gemacht oder funktioniert nicht

| Bereich | Stand | Was das hier bedeutet |
|---|---|---|
| Software | 🟡 | Funktioniert für seinen Hauptzweck; die Ehrlichkeitsprüfung oben nennt, was noch fehlt |
| Tests | 🟢 | 81 automatische Tests, bei jeder Änderung ausgeführt |
| Echte Hardware | 🟠 | Nur simuliert: gegen Emulatoren, Attrappen oder Simulatoren getestet; für diese Version ist kein Lauf auf echter Hardware vermerkt |
| Dokumentation | 🟢 | README in acht Sprachen, bei jeder Änderung gegen das Manifest geprüft |

## 🎯 Überblick

**HYDRA-UMC-BRIDGE-ROS2** ist die bidirektionale, High-Level-Koordinationsgrenze zwischen HYDRA-UMC und ROS 2. Sie bildet kontinuierliche Beobachtung auf ein Topic, sofortige Inspektion auf einen Service und lang laufende Zellenarbeit auf eine abbrechbare Action ab. Sie ist kein Motorsteuerungsknoten und kann HYDRA-UMC-SERVER, MCU-Grenzen, Watchdogs oder den E-STOP nicht umgehen. Heute sind nur das Zustands-Topic und der Safe-Stop-Service angebunden; der Inspektions-Service und die Job-Action sind reservierte Namen ohne Nachrichtentyp dahinter. Der Knoten muss gedreht werden, damit das Zustands-Topic zugestellt wird: `run_forever()` erledigt das in einem eigenen Thread.

Sie gehört zur Familie **External Automation Bridges**: einer Gruppe von Schwester-Repositories (CNC, LASER, OPENPNP, PRINTER3D, ROS2), die alle denselben gemeinsamen Sicherheitsvertrag von `HYDRA-UMC-SDK` sprechen, sodass keine Brücke ihre eigene Definition von "sicher zum Arbeiten" erfinden kann.

## 🧩 Rolle in HYDRA-UMC

**HYDRA-UMC-BRIDGE-ROS2** sitzt zwischen einem ROS-2-Graphen und der Roboterzelle. Er meldet, was diese Seite sagt, fragt vor jeder Hilfsarbeit das gemeinsame Tor und übernimmt nie die Hoheit der Maschine, mit der er sich abstimmt.

Jeder Bridge spricht den gemeinsamen Sicherheitsvertrag von `HYDRA-UMC-SDK`: Ein Auftrag wird nur angenommen, wenn das gemeinsame Tor `evaluate_job()` zustimmt, und das Tor schließt im Zweifel. Das Elternprojekt ist `HYDRA-UMC-SERVER`; dieser Bridge ruft es nicht auf, er antwortet über MQTT und `HYDRA-UMC-MQTT-BROKER` dem, der die Zelle steuert.

## 🏛️ Architektur

<p align="center">
  <img src="images/architecture.svg" alt="HYDRA-UMC-BRIDGE-ROS2 architecture" width="100%">
</p>

<details>
<summary>Textversion des Diagramms (Mermaid)</summary>

```mermaid
flowchart LR
    ROS["ROS-2-Knoten"] -- "Topic / Service / Action" --> BRIDGE["BRIDGE-ROS2<br/>Ros2Coordinator.dispatch()"]
    BRIDGE -- BridgeJob --> SDK["HYDRA-UMC-SDK<br/>evaluate_job()"]
    SDK -- GateDecision --> BRIDGE
```

</details>

**Warum es so gebaut ist**

* **Warum `coordinator.py` keinerlei `rclpy`-Abhängigkeit hat.** Der eigene Modul-Docstring stellt dies bewusst fest: er "kann auf jedem Host getestet werden und wird nur über einen separat bereitgestellten Adapter zu einem ROS-2-Knoten." Das hält die sicherheitsrelevante Koordinationslogik in CI testbar, ohne ROS-2-Installation, und erlaubt es, den Adapter später unabhängig auszuwählen und zu validieren.
* **Warum drei verschiedene Schnittstellenarten statt eines generischen Kanals.** `state_topic`, `inspect_service` und `job_action` entsprechen absichtlich der eigenen Semantik von ROS 2: kontinuierliche Zustandsveröffentlichung braucht keine Anfrage/Antwort (Topic), eine schnelle Inspektion braucht eine synchrone Antwort (Service), und Zellenarbeit muss mitten im Ablauf abbrechbar sein (Action) — dies zu einem einzigen Kanal zusammenzufassen würde diese Unterscheidung verlieren.
* **Warum `Ros2Coordinator.dispatch()` trotzdem jeden Auftrag durch das gemeinsame `evaluate_job()`-Gatter leitet.** ROS 2 ist nur ein weiterer Client desselben `bridge_contract`, den CNC, LASER, OPENPNP und PRINTER3D verwenden — es erhält keine besondere Umgehung der IDLE/READY-Logik, die jede andere Brücke und HYDRA-UMC-SERVER durchsetzen.
* **Warum `ABORT` während eines Fehlers anforderbar bleibt.** Die Anforderung der produktiven Phase des Gatters (`IDLE` + `READY`) wird bewusst nicht in derselben Weise auf eine Abbruchanfrage angewendet — ein Bediener oder ein ROS-2-Knoten muss immer einen kontrollierten Stopp anfordern können, selbst mitten in einem Fehlerzustand.
* **Warum der `rclpy`-Adapter und ROS-`.msg`/`.srv`/`.action`-Verträge noch nicht in diesem Repository sind.** Sich vor der Auswahl und dem Test einer echten ROS-2-Umgebung auf konkrete Nachrichten-/Service-/Action-Definitionen festzulegen, würde riskieren, Annahmen einzubauen, die dieser lokale, abhängigkeitsfreie Kern nicht verifizieren kann.
* **Wie das in den Rest des Ökosystems passt.** BRIDGE-ROS2 sitzt zwischen ROS-2-Knoten und `HYDRA-UMC-SDK` → `HYDRA-UMC-SERVER` → MCU-Sicherheit: es ist eine Koordinationsgrenze, niemals ein Motorsteuerungsknoten, und es kann HYDRA-UMC-SERVER, MCU-Grenzen, Watchdogs oder den E-STOP nicht umgehen.

## ✨ Funktionen

* ✅ **Echter, abhängigkeitsfreier Koordinationskern:** `coordinator.py`s `Ros2Coordinator` hat keinerlei `rclpy`-Import — es ist bewusst reines Python, testbar auf jedem Host ohne ROS-2-Installation. *(implementiert, getestet in `tests/test_coordinator.py`)*
* ✅ **Echte Vier-Wege-Schnittstellenabbildung:** vier feste Klassenattribute reservieren für jeden Zweck genau die richtige ROS-2-Schnittstellenart — `/hydra_umc/machine_state` (Topic, kontinuierlicher Zustand), `/hydra_umc/inspect_cell` (Service, kurze Inspektion), `/hydra_umc/execute_cell_job` (Action, abbrechbarer Auftrag), `/hydra_umc/request_safe_stop` (Service, kontrollierter Stopp). *(implementiert)*
* ✅ **Echtes gemeinsames Sicherheitsgatter:** jeder über `Ros2Coordinator.dispatch()` versendete Auftrag wird durch `evaluate_job()` aus dem `bridge_contract` von `HYDRA-UMC-SDK` bewertet — demselben Gatter, das jede Schwesterbrücke und HYDRA-UMC-SERVER verwenden; eine produktive Phase erfordert eine externe Maschine im Zustand `IDLE` und eine `READY`-HYDRA-UMC-Zelle, während `ABORT` auch während eines Fehlers angefordert werden kann. *(implementiert)*
* ✅ **Ausfallsicheres Phasenrouting und statische Evidenz:** produktive Phasen werden nur der geplanten Arbeitsaktion zugeordnet, `ABORT` wird `/hydra_umc/request_safe_stop` zugeordnet und eine unbekannte zukünftige SDK-Phase wird abgelehnt. `inspect_interface_plan.py` gibt den statischen Schema-`1.1`-Plan aus — einschließlich der echten `transient_local`-Durability-QoS, die das Zustands-Topic benötigt (ROS 2s eigener Ersatz für ROS 1s „latched"-Publisher, recherchiert anhand von design.ros2.org/articles/qos.html) — ohne `rclpy` zu importieren oder DDS zu kontaktieren. *(implementiert, getestet)*
* ✅ **Echter, teilweiser `rclpy`-Transport:** `rclpy_transport.py` verbindet die 2 Schnittstellen schon heute mit einem echten, standardmäßigen ROS-2-Nachrichtentyp — `Ros2SafeStopClient` (ein echter `std_srvs/Trigger`-Client) und `Ros2StateSubscriber` (ein echter `std_msgs/String`-Abonnent, der die echte `transient_local`-Durability-QoS verwendet). *(implementiert, getestet in `tests/test_rclpy_transport.py`)*
* ✅ **Nicht-mutierender Build/Test:** `build-test.bat`/`.sh` kompilieren den Quellcode und führen deterministische Unit-Tests aus, ohne Version oder CHANGELOG zu ändern. *(implementiert, siehe Build und Veröffentlichung unten)*
* 🔜 **Eigene `.srv`/`.action`-Verträge für `inspect_service`/`job_action`** — dafür gibt es keinen echten Standard-ROS-2-Nachrichtentyp, sodass ein Client dafür zunächst ein eigenes Interface-Paket in diesem Repository definieren muss. *(geplant)*
* ✅ **Beobachteter Maschinenzustand, zurückgehaltene Nachrichten und wiederholte Schlüssel:** sobald der Bridge dem ROS-2-Zustandstopic folgt, verwendet das gemeinsame Tor den von der Maschine gemeldeten Zustand statt des im Auftrag stehenden `machine_state` (eine Nachricht, die jeder am Broker veröffentlichen kann), und eine Maschine, von der man nie etwas gehört hat, zählt als OFFLINE; `handle_message()` ignoriert zurückgehaltene Nachrichten (der Broker spielt sie bei jeder Wiederverbindung erneut ab) und beantwortet, wenn es einen `IdempotencyStore` erhält, einen wiederholten `idempotency_key` mit der ersten Antwort. *(implementiert, getestet in `tests/test_intake.py`)*
* ✅ **Eine Nachrichtenschleife, die ihre Eingaben übersteht, ein Knoten, der wirklich gedreht wird, und ein Zustand, der veralten kann:** `handle_message()` löst nie eine Ausnahme aus (ein übergroßes oder tief verschachteltes JSON und jeder unerwartete Fehler kommen als Ablehnung zurück); `cmd/job` antwortet mit `allowed` wie jeder andere Bridge (und `accepted` wie bisher); ein `ABORT`-Auftrag sagt klar, dass er nur ein Plan ist und der eigentliche Stopp `cmd/safe_stop` ist. `run_forever()` lässt den Knoten in einem eigenen Thread laufen (`spin_node_in_background()`, damit das Zustands-Topic wirklich zugestellt wird), nutzt einen einzigen Service-Client wieder, führt `cmd/safe_stop` auf einer Spur aus, die nie hinter einem anderen Befehl wartet, hinterlegt ein Last Will auf `availability`, veröffentlicht den Zustand mit normalisiertem `machine_state` und einer Zeit `observed_at` erneut und startet nicht, wenn das ROS-2-Zustands-Topic nicht abonniert werden kann; `observed_max_age_seconds` lässt einen Zustand, der nicht mehr erneut veröffentlicht wird, als OFFLINE gelten (standardmäßig aus). *(implementiert, mit Attrappen getestet; nie gegen einen echten ROS-2-Graphen ausgeführt, das Verhalten von Executor und QoS ist daher nicht verifiziert)*

## 🔒 Sicherheit und Schutz

| Bedrohung | Abwehr | Wo |
|---|---|---|
| Ein Auftrag für eine Zelle oder Maschine, die nicht sicher ist | Jeder Auftrag läuft durch das gemeinsame Tor `evaluate_job()` des SDK, das im Zweifel schließt; der Bridge hat keine eigene Annahmelogik | SDK `bridge_contract` |
| Überlange oder unbegrenzte Auftragsdaten | Höchstens 32 Parameter, Schlüssel bis 64 Zeichen, Werte bis 1024, Bezeichner bis 128 (`MAX_PARAMETERS`, `MAX_PARAMETER_KEY_CHARS`, `MAX_PARAMETER_VALUE_CHARS`, `MAX_IDENTIFIER_CHARS`) | SDK `bridge_contract` |
| Ein wiederholter oder erneut gesendeter Befehl | Ein `IdempotencyStore` merkt sich jeden Schlüssel 24 Stunden und höchstens 10 000 Einträge; eine Wiederholung bekommt die erste Antwort, statt neu bewertet zu werden | SDK `job_intake` |
| Eine riesige oder feindliche MQTT-Nutzlast | Eine Nutzlast über 16 KiB, unlesbares oder tief verschachteltes JSON und jeder unerwartete Fehler werden mit einer Ablehnung auf dem Ergebnis-Topic beantwortet und beenden die Schleife nie | `mqtt_transport.py` |
| Eine ROS-2-Schnittstelle außerhalb des erlaubten Raums | Schnittstellennamen müssen mit `/hydra_umc/` beginnen, und der Bridge kennt Schnittstellenarten, nie rohe Bewegung | `coordinator.py` |
| Ein Stopp, der hinter anderer Arbeit wartet | `cmd/safe_stop` läuft auf eigener Spur (`STOP_VERBS`); die beiden rclpy-Standardschnittstellen nutzen die Dauerhaftigkeit `transient_local`, ein später Abonnent bekommt also den letzten Zustand | `mqtt_transport.py`, `rclpy_transport.py` |
| Ein ungeprüft übernommener Maschinenzustand | Vor `start_observing()` wurde nichts gelesen und das Tor lehnt den Auftrag ab; beim Beobachten gilt ein nie gehörter oder unbekannter `MachineState` als `OFFLINE` | `mqtt_transport.py` |

**Was er nie tut:** er setzt keinen Auftrag in rohe Bewegung um und spricht mit ROS 2 nur über zwei standardtypisierte Schnittstellen.

## 🔌 Schnittstellen

Topics unter `hydra/bridges/ros2/`; jeder Befehl wird auf `<verb>/result` beantwortet.

| Topic oder Route | Was es trägt |
|---|---|
| `cmd/job` | ein `BridgeJob`; die Antwort ist die Versandentscheidung für die ROS-2-Schnittstelle |
| `cmd/safe_stop` | ruft den Sicherheitsstopp-Dienst über `Ros2SafeStopClient` auf |
| `state`, `availability` | der Maschinenzustand aus dem ROS-2-Zustands-Topic (`std_msgs/msg/String`) und `online`/`offline` |

Den Schnittstellenplan ohne ROS 2 prüfen: `python tools/inspect_interface_plan.py --verify-fixture`.

## ⚙️ Konfiguration

| Variable | Standard | Was sie festlegt |
|---|---|---|
| `HYDRA_MQTT_USERNAME` | keiner | Der MQTT-Benutzername; der Broker bindet ihn an ein Client-ID-Präfix |
| `HYDRA_MQTT_PASSWORD` | keiner | Das Passwort; nur zusammen mit dem Benutzernamen gültig |
| `HYDRA_MQTT_PASSWORD_FILE` | keiner | Das Passwort aus einer Datei (Docker- oder systemd-Secret); wird ignoriert, wenn `HYDRA_MQTT_PASSWORD` gesetzt ist |
| `HYDRA_MQTT_TLS_CA` | keiner | Die CA-Datei, die das Zertifikat des Brokers signiert hat: Die Verbindung ist TLS und das Zertifikat wird geprüft |
| `HYDRA_MQTT_TLS` | aus | `1` schaltet TLS ein, geprüft gegen die vertrauenswürdigen CAs des Systems |
| `HYDRA_MQTT_TLS_CERT` / `HYDRA_MQTT_TLS_KEY` | keiner | Das Clientzertifikat und sein Schlüssel für einen Broker mit gegenseitigem TLS; beide sind nötig |

Der ROS-2-Knotenname ist das Argument von `create_ros2_node()`; MQTT-Host, Port und Client-ID (`hydra-umc-bridge-ros2`) sind Argumente von `run_forever()`.

## 🚀 Schnellstart

Benötigt Python 3.11 oder neuer. Die Tests und die Build-Skripte erwarten `HYDRA-UMC-SDK` neben diesem Repository (`../HYDRA-UMC-SDK`) oder über `HYDRA_UMC_SDK_ROOT` angegeben.

Ohne Hardware ausprobieren (das prüft den ganzen Bridge gegen seine Emulatoren und Attrappen):

```bash
py -3 tools/build_test.py          # Windows
python3 tools/build_test.py         # Linux, macOS
```

Den Dienst starten (es gibt kein Konsolenskript: Der Dienst ist das `run_forever()` des MQTT-Transports, das du aus deinem eigenen Startcode aufrufst):

```python
from hydra_umc_bridge_ros2.rclpy_transport import create_ros2_node, spin_node_in_background
from hydra_umc_bridge_ros2.mqtt_transport import Ros2MqttBridge, run_forever
node = create_ros2_node("hydra_umc_bridge")          # needs rclpy from a ROS 2 installation
spin_node_in_background(node)
bridge = Ros2MqttBridge(node, cell_state=lambda: cell_state, node_is_spinning=True)
run_forever(bridge, host="192.168.0.180")
```

Benutzername, Passwort und TLS-Dateien kommen aus den Variablen `HYDRA_MQTT_*` (siehe Konfiguration); der Code oben braucht keine eigenen Zugangsdaten.

## 🧪 Tests

81 automatische `unittest`-Fälle laufen mit `python tools/build_test.py` (das kompiliert auch jedes Modul). Die Tests brauchen weder Hardware noch Netz noch optionale Pakete: Die, die `paho-mqtt`, `pyserial` oder eine Herstellerbibliothek bräuchten, nutzen einen Ersatz oder werden übersprungen, wenn das Paket fehlt.

| Testdatei | Tests | Was sie abdeckt |
|---|---|---|
| `test_coordinator.py`, `test_intake.py` | - | Auftrag-zu-Schnittstelle-Abbildung und die Auftragsannahme |
| `test_rclpy_transport.py` | - | die zwei rclpy-Schnittstellen gegen einen rclpy-Ersatz |
| `test_mqtt_transport.py`, `test_mqtt_environment.py`, `test_hardening.py`, `test_edge_cases.py` | - | Topic-Verteilung, Zugangsdaten der Umgebung, feindliche Clients, Randfälle |

Die CI führt `HYDRA_UMC_CI=1 python tools/ci_validate.py` aus; das prüft das Manifest, die Dokumente, die acht READMEs und die Bilder und führt die Tests mit einem SDK-Checkout aus.

## 🏗️ Build und Veröffentlichung

Erfordert Python 3.11+. `tools/build_test.py` erwartet, dass `HYDRA-UMC-SDK` als Schwesterverzeichnis (`../HYDRA-UMC-SDK`) ausgecheckt oder über die Umgebungsvariable `HYDRA_UMC_SDK_ROOT` angegeben ist.

```bash
# Windows
build-test.bat      # nur Validierung — keine Versions-/CHANGELOG-Änderung
build.bat            # Validiert und baut (Bauen ändert nie die Version)

# Linux/macOS
bash build-test.sh
bash build.sh
```

`build-test` kompiliert jedes Modul unter `src/` mit `py_compile` und führt die vollständige `unittest`-Suite aus (jede Datei `test_*.py` unter `tests/`) — deterministisch, ohne ROS-2-Installation, ohne Netzwerk und ohne Versions-/CHANGELOG-Änderung. `build` führt zuerst dieselbe Validierung aus und ruft nur bei Erfolg `tools/bump_version.py` auf, um die Version in `pyproject.toml`, `hydra-umc.project.json` und `CHANGELOG.md` zu synchronisieren. Es gibt noch keinen echten Hardware-`run`-Befehl — dafür ist ein validiertes ROS-2-Deployment erforderlich.

## 🩺 Fehlerbehebung

| Du siehst | Weil | Tu |
|---|---|---|
| `ModuleNotFoundError: hydra_umc_sdk` | Das SDK ist nicht installiert oder liegt nicht neben diesem Repository | `HYDRA-UMC-SDK` neben dieses Repository klonen oder `HYDRA_UMC_SDK_ROOT` setzen |
| `paho-mqtt is not installed` | Der MQTT-Client ist ein optionales Extra | `pip install ".[mqtt]"` (braucht `paho-mqtt` ab 2.1, unter 3) |
| `password was given without a username` | MQTT-Zugangsdaten brauchen beide Teile | Setze auch `HYDRA_MQTT_USERNAME`, nicht nur das Passwort |
| `could not reach the MQTT broker after N attempts` | Der Broker ist aus, die Adresse falsch oder der Port geschlossen | Starte `HYDRA-UMC-MQTT-BROKER` und prüfe Host und Port des Transports (5 Versuche im Abstand von 2 Sekunden); ein TLS-Handshake-Fehler wird nicht wiederholt |
| `the MQTT broker refused the connection` | Der Broker hat die Zugangsdaten abgelehnt (oder nicht rechtzeitig geantwortet) | Prüfe `HYDRA_MQTT_USERNAME` und das Passwort; die Client-ID muss mit dem Präfix beginnen, das der Broker an die Zugangsdaten gebunden hat |
| `"allowed": false` + `reason` | Das gemeinsame Tor oder eine Sperre hat den Befehl abgelehnt | Lies `reason` und behebe die Ursache; nicht blind wiederholen |
| `rclpy` cannot be imported | rclpy kommt mit einer ROS-2-Installation, nicht von PyPI | Lade die ROS-2-Umgebung, bevor der Dienst startet; die Tests brauchen sie nicht |

## 📂 Struktur des Repositorys

<p align="center">
  <img src="images/repository-structure.svg" alt="Repository structure of HYDRA-UMC-BRIDGE-ROS2" width="100%">
</p>

<details>
<summary>Textversion des Baums</summary>

```text
HYDRA-UMC-BRIDGE-ROS2/
├── src/hydra_umc_bridge_ros2/
│   ├── coordinator.py             Auftrag zu einer benannten ROS-2-Schnittstelle, nie rohe Bewegung
│   ├── rclpy_transport.py         die zwei standardtypisierten rclpy-Schnittstellen
│   └── mqtt_transport.py          MQTT-Transport für Befehle, Status und Sicherheitsstopp
├── tests/                         81 Tests, rclpy durch einen Ersatz ersetzt
├── docs/                          Bridge-Leitfaden
├── tools/                         Build- und Testläufer, Versionserhöhung, CI-Prüfer, README- und Bildgeneratoren samt Prüfungen
├── images/                        Banner und die Bilder der README
├── .github/                       CI-Workflow und Issue-Vorlagen
├── pyproject.toml                 Paketmetadaten und Abhängigkeiten
├── hydra-umc.project.json         Manifest: Version, Reifegrad, Familie
├── CHANGELOG.md                   was sich in jeder Version geändert hat
├── build.sh build.bat             Build-Skripte (ändern die Version nie)
├── build-test.sh build-test.bat   Build-Skripte (ändern die Version nie)
├── test.sh test.bat               führen die Tests aus
└── SECURITY.md SUPPORT.md CONTRIBUTING.md CODE_OF_CONDUCT.md LICENSE LICENSE.md
```

</details>

## 🗺️ Roadmap und bekannte Grenzen

**Bekannte Grenzen heute**

**Heute real:** Version `0.1.3`, funktionsfähig als abhängigkeitsfreier Koordinationskern (`Ros2Coordinator`) mit einer deterministischen `unittest`-Suite mit vierundsechzig Tests, die den Koordinationskern, den MQTT-Transport und den rclpy-Transport abdeckt, ausfallsicherem Phasenrouting, einem statischen `plan-only`-Schnittstellenschema, das die echte `transient_local`-Durability-QoS deklariert, die das Zustands-Topic benötigt, einem echten (lazy importierten) `rclpy`-Transport für die 2 Schnittstellen mit einem echten, standardmäßigen ROS-2-Nachrichtentyp, sowie nicht-mutierenden Build-Test-Skripten, die in CI mit SDK-Checkout eingebunden sind.

**Integrationsgrenze:** diese Brücke ist ausschließlich eine Koordinationsgrenze — sie ist kein Motorsteuerungsknoten und kann HYDRA-UMC-SERVER, MCU-Grenzen, Watchdogs oder den E-STOP nicht umgehen; jeder versendete Auftrag durchläuft weiterhin dasselbe gemeinsame Gatter, das jede Schwesterbrücke verwendet.

**Noch offen:** es wurde noch kein ROS-Netzwerk, Roboter oder physischer Aktor validiert — der `rclpy`-Adapter und konkrete ROS-`.msg`/`.srv`/`.action`-Verträge werden erst eingeführt, nachdem eine echte ROS-2-Umgebung ausgewählt und getestet wurde.

## 🌍 Sprachen

Diese README und die Dokumente des Repositorys gibt es in acht Sprachen: Englisch, Spanisch, Deutsch, Französisch, Italienisch, Japanisch, Chinesisch und Portugiesisch (Portugal). Sie entstehen gemeinsam, und die CI prüft, dass die acht Dateien dieselben Abschnitte und dieselben Links haben. Dieses Repository hat weder eine eigene Oberfläche noch einen eigenen Meldungskatalog, es gibt also keine Oberflächentexte zu übersetzen.

## 🔗 Verwandte Projekte

Dieses Projekt ist Teil des HYDRA-UMC-Robotik-Ökosystems desselben Autors (JuanenRac / Electro Hobby 3D). Gut zu wissen, da eine Anfrage eigentlich eines dieser Projekte betreffen könnte statt dieses Repositorys.

**Übergeordnetes Projekt**
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — das reale Headless-Backend (REST/WebSocket), mit dem jeder Steuerungsclient tatsächlich spricht; die authentifizierte Ökosystemgrenze, an die diese Bridge berichtet, sobald jeder Befehl die eigene lokale Sicherheitsschranke dieser Bridge durchlaufen hat.

**Geschwisterprojekte** — sprechen ebenfalls mit der eigenen API von HYDRA-UMC-SERVER, jeweils als eigener Client
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** — Web-Steuerungs-Dashboard mit Echtzeit-3D-Visualisierung mehrerer Roboter.
- **[HYDRA-UMC-SUITE](https://github.com/JuanenRac/HYDRA-UMC-SUITE)** — Desktop-Schwarmleitstand (PySide6) für mehrere Server gleichzeitig, verpackt als eigenständige ausführbare Datei.
- **[HYDRA-UMC-ANDROID-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-ANDROID-CONTROL)** — native Android-Steuerungs-App mit biometrischem Login und einer gekoppelten Wear-OS-Begleit-App.
- **[HYDRA-UMC-IOS-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-IOS-CONTROL)** — iOS/iPadOS-Steuerungs-App (Flutter) mit Echtzeit-WebSocket-Synchronisierung.
- **[HYDRA-UMC-DSI](https://github.com/JuanenRac/HYDRA-UMC-DSI)** — native Touch-UI für das eingebaute 7"-DSI-Touchscreen, direkt auf dem CM5 eingebettet.
- **[HYDRA-UMC-BRIDGE-AMR](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-AMR)** — Koordinationsschranke für AGV-/AMR-Flotten über einen VDA-5050-MQTT-Publisher (nur gegen eine emulierte Flotte getestet).
- **[HYDRA-UMC-BRIDGE-CNC](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-CNC)** — High-Level-Koordinator für CNC-Zellen mit GRBL-Status-/Steuerbyte-Zugriff (nur gegen einen Emulator getestet).
- **[HYDRA-UMC-BRIDGE-DROIDS](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-DROIDS)** — Koordinationsschranke für laufende/humanoide Droiden, mit einem Boston-Dynamics-Spot-Befehlssender (nur gegen einen Emulator getestet).
- **[HYDRA-UMC-BRIDGE-LASER](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-LASER)** — Koordinator für Laserzellen, liest 3 Schlüssel-/Gehäuse-/Verriegelungs-GPIO-Signale (nur gegen einen simulierten Chip getestet; kann keinen Laser scharfschalten oder auslösen).
- **[HYDRA-UMC-BRIDGE-OPENPNP](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-OPENPNP)** — sicherer High-Level-Koordinator für den Leiterplattenfluss von OpenPnP Pick-and-Place.
- **[HYDRA-UMC-BRIDGE-PRINTER3D](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-PRINTER3D)** — sichere Koordinationsschranke für Moonraker/Klipper-3D-Drucker, mit echten gesicherten Job-Befehlen.
- **[HYDRA-UMC-BRIDGE-UAV](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-UAV)** — Koordinationsschranke für kameraausgestattete UAVs, mit einem MAVLink-Befehlssender (nur gegen einen Emulator getestet).

**Direkt verwandt**
- **[HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK)** — der gemeinsame JSON-Schema-Vertrag und die Software-Auftragsschranke, gegen die jede Bridge ihre Befehle validiert (keine zertifizierte Sicherheitsfunktion).
- **[HYDRA-UMC-CONNECTOR-HUB](https://github.com/JuanenRac/HYDRA-UMC-CONNECTOR-HUB)** — deklaratives Adapter-Manifest-Register und Validator für Konnektoren externer Maschinen; erweitert die eigene Vertragsidee des SDK auf externe Maschinen, ohne die Industrie-Gateway-Projekte zu ersetzen.
- **[HYDRA-UMC-MQTT-BROKER](https://github.com/JuanenRac/HYDRA-UMC-MQTT-BROKER)** — der echte Transport von `mqtt_transport.py` für die eigenen `hydra/bridges/ros2/...`-Topics dieser Bridge — die planungsonly Job-Schranke, ein `std_srvs/Trigger`-Safe-Stop-Aufruf (noch nicht gegen eine ROS-2-Installation ausgeführt) und das ROS-2-Statustopic, republiziert genau wie vom separat bereitgestellten `rclpy_transport.py`-Adapter stets vorgesehen; siehe die eigene `docs/BRIDGE_TOPICS.md` dieses Repositorys.
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** — Hardware-in-the-Loop-Router mit einer Software-Kollisionsverriegelung zwischen Simulation und echter Hardware (ohne echte Hardware getestet).

**Ebenfalls Teil des Ökosystems**

*Kern-Hardware & Plattform*
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — der Entwurf des Motherboards des Roboterarms: CM5-Host + Dual-Core-STM32H745, gedacht zur Koordination von bis zu 8 Werkzeugarmen über CAN-OTA/SPI-OTA (Firmware in einer frühen Phase; noch keine bestückte Platine).
- **[HYDRA-UMC-OS](https://github.com/JuanenRac/HYDRA-UMC-OS)** — reproduzierbare Raspberry-Pi-OS-Produktschicht für den CM5: schreibgeschützter Agent, validierte Konfiguration/Profile, WiFi-Ersteinrichtung.

*Kern-Backend & Clients*
- **[HYDRA-UMC-EDITOR-URDF](https://github.com/JuanenRac/HYDRA-UMC-EDITOR-URDF)** — grafischer Desktop-URDF-Ersteller/-Editor, der fertige Modelle in STUDIOs eigenen Katalog überträgt.
- **[HYDRA-UMC-EDITOR-STL](https://github.com/JuanenRac/HYDRA-UMC-EDITOR-STL)** — Desktop-STL-Modelleditor, der echte Teile im selben Modellkatalog transformiert/ersetzt/entfernt/hinzufügt, den auch HYDRA-UMC-EDITOR-URDF bearbeitet.

*URTC-Werkzeugplattform*
- **[URTC](https://github.com/JuanenRac/URTC)** — Firmware für die physische Universal-Robot-Tool-Controller-Platine, 25+ Werkzeugprofile über CAN-Bus.
- **[URTC-FLASHER](https://github.com/JuanenRac/URTC-FLASHER)** — Desktop-GUI-Flash-Tool für URTC-Platinen, CAN-OTA plus Full-Chip-SWD/JTAG.
- **[URTC-TESTER](https://github.com/JuanenRac/URTC-TESTER)** — Desktop-Live-CAN-Bus-Diagnosetool für URTC-Platinen, ein Panel pro Werkzeugprofil.
- **[URTC-WEB-STUDIO](https://github.com/JuanenRac/URTC-WEB-STUDIO)** — browserbasierte Alternative zu URTC-TESTER über die Web-Serial-API, ohne lokale Installation.
- **[URTC-GATEWAY](https://github.com/JuanenRac/URTC-GATEWAY)** — Gateway vor dem CAN-Bus von URTC: Liste erlaubter Kennungen, Ratenbegrenzung und Sitzungsschlüssel; Software auf dem Computer getestet, noch nicht an einem echten Bus.

*Vision-KI-Knoten (Hailo-8)*
- **[HYDRA-UMC-VISION-NODE](https://github.com/JuanenRac/HYDRA-UMC-VISION-NODE)** — Integrationsknoten für die Hailo-8-Vision-Pipeline, mit einer echten stufenweisen Hardware-Bereitschaftsprüfung.
- **[HYDRA-UMC-DETECTION-HEF](https://github.com/JuanenRac/HYDRA-UMC-DETECTION-HEF)** — echte Registry für kompilierte Modelle mit Hailo-Architektur-/Prüfsummen-Safe-Load-Verifizierung.
- **[HYDRA-UMC-VISION-STREAMER](https://github.com/JuanenRac/HYDRA-UMC-VISION-STREAMER)** — echter GStreamer-Pipeline- + MediaMTX-Konfigurationsgenerator mit einer echten HailoRT-Integrationsschranke.
- **[HYDRA-UMC-VISUAL-SERVOING-API](https://github.com/JuanenRac/HYDRA-UMC-VISUAL-SERVOING-API)** — Position-Based-Visual-Servoing-Korrekturgesetz mit Freigabe abhängig vom Zonenstatus; noch keine Posenschätzung per Kamera.
- **[HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)** — v0-Logik für Zonenverletzung, Kalibrierungsaktualität und E-STOP-Anforderung, die Objektpositionen aus einer Datei nimmt; kein Detektor speist sie, und noch kein physischer E-STOP ist angeschlossen.

*Kognitiver KI-Knoten (Hailo-10)*
- **[HYDRA-UMC-COGNITIVE-NODE](https://github.com/JuanenRac/HYDRA-UMC-COGNITIVE-NODE)** — Integrationsknoten für die Hailo-10-Cognitive-Pipeline (LLM-/VLA-/Sprach-Orchestrierung).
- **[HYDRA-UMC-VLA-ENGINE](https://github.com/JuanenRac/HYDRA-UMC-VLA-ENGINE)** — echte Aktions-Token-Kodierung/-Dekodierung und Trajektoriengenerierung für ein Vision-Language-Action-Modell.
- **[HYDRA-UMC-VOICE-UI](https://github.com/JuanenRac/HYDRA-UMC-VOICE-UI)** — echtes Sprach-Frontend (VAD + Intent-Parser) mit einem begrenzten, bestätigungsgesicherten Watch-Relay.
- **[HYDRA-UMC-SEMANTIC-PLANNER](https://github.com/JuanenRac/HYDRA-UMC-SEMANTIC-PLANNER)** — echte regelbasierte Aufgabenzerlegung und semantische Fehlerbehebung über MCU-Fehlercodes.
- **[HYDRA-UMC-DOCS-QA](https://github.com/JuanenRac/HYDRA-UMC-DOCS-QA)** — echte, nur auf der Standardbibliothek basierende TF-IDF-Dokumentensuche über die eigenen Markdown-Dokumente dieses Ökosystems.
- **[HYDRA-UMC-LOCAL-TECHNICIAN](https://github.com/JuanenRac/HYDRA-UMC-LOCAL-TECHNICIAN)** — lokaler, richtlinien-gesteuerter KI-Wartungstechniker für das Ökosystem selbst - beobachtet, diagnostiziert und schlägt Korrekturen vor; die zwei höchsten Risikostufen sind absichtlich noch nicht implementiert.

*Orchestrierung & Schwarm*
- **[HYDRA-UMC-ORCHESTRATOR](https://github.com/JuanenRac/HYDRA-UMC-ORCHESTRATOR)** — Integrationsknoten mit einem echten gRPC/Protobuf-Health-Report-Vertrag und einer Missions-Zustandsmaschine.
- **[HYDRA-UMC-JOB-DISPATCHER](https://github.com/JuanenRac/HYDRA-UMC-JOB-DISPATCHER)** — echte prioritätsbasierte Job-Queue mit Deduplizierung, über eine echte HTTP-API.
- **[HYDRA-UMC-NODE-HEALING](https://github.com/JuanenRac/HYDRA-UMC-NODE-HEALING)** — echter gRPC-basierter Flotten-Health-Watchdog mit Retry/Backoff und Identitäts-Mismatch-Erkennung.
- **[HYDRA-UMC-PATH-PLANNER-3D](https://github.com/JuanenRac/HYDRA-UMC-PATH-PLANNER-3D)** — echter RRT-basierter 3D-Pfadplaner mit echter Hindernis-/Arbeitsraum-Kollisionsvalidierung.
- **[HYDRA-UMC-SWARM-SYNC](https://github.com/JuanenRac/HYDRA-UMC-SWARM-SYNC)** — echte CRDT-LWW-Element-Map-Zustandssynchronisation, eigenschaftsgetestet auf Multi-Zellen-Konvergenz.

*Digitaler Zwilling & Simulation*
- **[HYDRA-UMC-TWIN](https://github.com/JuanenRac/HYDRA-UMC-TWIN)** — Integrationsknoten für die Digital-Twin-Engine, mit einem echten Versionskompatibilitäts-Sync-Vertrag.
- **[HYDRA-UMC-PHYSICS-REPLICA](https://github.com/JuanenRac/HYDRA-UMC-PHYSICS-REPLICA)** — echte Vorwärtskinematik und Gelenkgrenzenvalidierung über eine echte URDF-Teilmenge.
- **[HYDRA-UMC-SYNTHETIC-DATA-GEN](https://github.com/JuanenRac/HYDRA-UMC-SYNTHETIC-DATA-GEN)** — echter prozeduraler 2D-Szenengenerator mit YOLO/COCO-Annotationsexport.

*Daten & Analytik*
- **[HYDRA-UMC-DATALAKE](https://github.com/JuanenRac/HYDRA-UMC-DATALAKE)** — echter sqlite3-gestützter Zeitreihenspeicher mit einer echten Ingest-/Abfrage-HTTP-API.
- **[HYDRA-UMC-ANOMALY-DETECTOR](https://github.com/JuanenRac/HYDRA-UMC-ANOMALY-DETECTOR)** — echter FFT- + statistischer Basislinien-Anomaliedetektor mit Drift-Überwachung.
- **[HYDRA-UMC-PRODUCTION-REPORTS](https://github.com/JuanenRac/HYDRA-UMC-PRODUCTION-REPORTS)** — echte OEE-/Verfügbarkeitsberechnung über den DATALAKE-Verlauf, mit reproduzierbarem CSV-Export.
- **[HYDRA-UMC-TELEMETRY-COLLECTOR](https://github.com/JuanenRac/HYDRA-UMC-TELEMETRY-COLLECTOR)** — echte CAN/WebSocket-Ingestion-Pipeline in DATALAKE, mit Sequenz-Deduplizierung.

*Industrie-Gateway*
- **[HYDRA-UMC-GATEWAY-INDUSTRIAL](https://github.com/JuanenRac/HYDRA-UMC-GATEWAY-INDUSTRIAL)** — Integrationsknoten, der zu Industrieprotokollen weiterleitet, mit einer echten Befehls-Allowlist-/Backpressure-Schicht.
- **[HYDRA-UMC-OPCUA-SERVER](https://github.com/JuanenRac/HYDRA-UMC-OPCUA-SERVER)** — echter OPC-UA-Adressraum, verifiziert mit einer echten Binärprotokoll-Client-Session.
- **[HYDRA-UMC-MTCONNECT-ADAPTER](https://github.com/JuanenRac/HYDRA-UMC-MTCONNECT-ADAPTER)** — echte MTConnect-`/probe`- und `/current`-XML-Endpunkte mit Degraded-Mode-Ausgabe.

*Ergänzende Tools & Ökosystembetrieb*
- **[HYDRA-UMC-DASHBOARD-AI](https://github.com/JuanenRac/HYDRA-UMC-DASHBOARD-AI)** — Smart-Summaries- und Anomaly-Highlighting-Panels über DATALAKE/ANOMALY-DETECTOR, mit einem ehrlichen statistischen Fallback.
- **[HYDRA-UMC-TOOL-CLI](https://github.com/JuanenRac/HYDRA-UMC-TOOL-CLI)** — Flotten-CLI mit einem echten, stabilen Exit-Code-Vertrag, ein echter Live-Client der eigenen API von HYDRA-UMC-SERVER.
- **[HYDRA-UMC-WATCH](https://github.com/JuanenRac/HYDRA-UMC-WATCH)** — WearOS-Begleit-App mit haptischen Alarmmustern und einem Sprach-Relay zum gekoppelten Telefon (noch nicht auf einer physischen Uhr ausgeführt).
- **[URTC-SMART-RACK](https://github.com/JuanenRac/URTC-SMART-RACK)** — Firmware für ein Platinenmontagegestell mit echter Werkzeug-ID-Dekodierung und Smart-Idle-Vorheizlogik.
- **[URTC-VISION-TOOL](https://github.com/JuanenRac/URTC-VISION-TOOL)** — Firmware plus ein echter Python-Vision-Begleiter für einen Thermal-/RGB-Inspektionswerkzeugkopf.
- **[HYDRA-UMC-UPDATER](https://github.com/JuanenRac/HYDRA-UMC-UPDATER)** — administratives Desktop-Tool, das jedes Repository in diesem Ökosystem entdeckt, klont und aktualisiert.
- **[HYDRA-UMC-OS-REBUILDER](https://github.com/JuanenRac/HYDRA-UMC-OS-REBUILDER)** — Windows/Linux-Desktop-Tool, das ein flashbereites CM5-Image baut, vorgeladen mit den aktuellsten Versionen des Ökosystems, mit Ersteinrichtungs-Konfiguration für WLAN/Benutzer/SSH im Stil von Raspberry Pi Imager.
- **[HYDRA-UMC-OPS-AGENT](https://github.com/JuanenRac/HYDRA-UMC-OPS-AGENT)** — Wartungsvorfall-Koordinator: eine Edge-Rolle mit niedrigem Privileg sammelt einen bereinigten Inventar-/Gesundheits-Snapshot, eine Control-Plane-Rolle rendert ihn schreibgeschützt und bittet einen KI-Anbieter um einen Diagnosevorschlag - wendet nie einen Patch an und stellt nie etwas bereit.
- **[HYDRA-UMC-DEV-SERVER](https://github.com/JuanenRac/HYDRA-UMC-DEV-SERVER)** — reproduzierbarer Entwicklungshost (Raspberry Pi 5 / CM5), der den Quellcode des Ökosystems vorhält und begrenzte Build-/Test-Aufgaben über eine dauerhafte Warteschlange ausführt; eine dedizierte Entwicklerrolle, ausdrücklich kein operativer CM5.

## 📚 Dokumentation

Hier steht mehr:

* [docs/BRIDGE_GUIDE.md](docs/BRIDGE_GUIDE.md) - Umfang, der Schnittstellenplan, rclpy, Skripte
* [Sicherheitsgrundlage des Ökosystems](https://github.com/JuanenRac/HYDRA-UMC-DOCS/blob/main/docs/SECURITY_BASELINE.md)
* [Bedrohungsmodell des Ökosystems](https://github.com/JuanenRac/HYDRA-UMC-DOCS/blob/main/docs/THREAT_MODEL.md)
* [Änderungsprotokoll dieses Repositorys](CHANGELOG.md)
* [Lizenz dieses Repositorys](LICENSE.md)

## 🤝 Mitwirken und Community

* Führe vor einem Pull Request `HYDRA_UMC_CI=1 py -3 tools/ci_validate.py` (oder `HYDRA_UMC_CI=1 python3 tools/ci_validate.py`) aus: Es muss `CI_VALIDATION=PASS` ausgeben. Führe dafür nicht `build.sh` oder `build.bat` aus; Bauen ist keine Prüfung.
* Die acht READMEs entstehen aus einer einzigen Quelle in HYDRA-UMC-DOCS; bearbeite sie nicht von Hand. Ändere die Quelle und schreibe sie mit `tools/make_readmes.py` neu.
* Behaupte in einer README nichts, was nicht gelaufen ist: Schreibe „simuliert“ oder „nicht auf Hardware erprobt“, wo das stimmt.
* Siehe [CONTRIBUTING.md](CONTRIBUTING.md), den [Verhaltenskodex](CODE_OF_CONDUCT.md), [SECURITY.md](SECURITY.md) und [SUPPORT.md](SUPPORT.md).

## 👤 AUTOR

**JuanenRac (Electro Hobby 3D)** · electrohobby3d@gmail.com

## 📜 LIZENZ

GPL-3.0-or-later - [LICENSE](LICENSE).
