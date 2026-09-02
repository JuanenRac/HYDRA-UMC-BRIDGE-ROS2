<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Pont de coordination bidirectionnel avec ROS 2
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="Bannière HYDRA-UMC-BRIDGE-ROS2" width="100%">
</p>

# 🤖 HYDRA-UMC-BRIDGE-ROS2

<p align="center"><a href="README.md">🇺🇸 English</a> | <a href="README_spa.md">🇪🇸 Español</a> | 🇫🇷 <b>Français</b> | <a href="README_ita.md">🇮🇹 Italiano</a> | <a href="README_deu.md">🇩🇪 Deutsch</a> | <a href="README_zho.md">🇨🇳 简体中文</a> | <a href="README_jpn.md">🇯🇵 日本語</a></p>

### 🔗 Frontière de coordination sans dépendance entre HYDRA-UMC et ROS 2

<p align="left">
  <img src="https://img.shields.io/badge/Licence-GPL%203.0-blue.svg" alt="GPL 3.0">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Safety-Fails%20Closed-red.svg" alt="Sécurité intrinsèque">
</p>

---

## 1. 🛠️ APERÇU TECHNIQUE

**HYDRA-UMC-BRIDGE-ROS2** est la frontière de coordination bidirectionnelle et haut niveau entre HYDRA-UMC et ROS 2. Elle associe l'observation continue à un topic, l'inspection immédiate à un service, et le travail de cellule de longue durée à une action annulable. Ce n'est pas un nœud de contrôle moteur, et elle ne peut pas contourner HYDRA-UMC-SERVER, les limites du MCU, les watchdogs ou l'E-STOP.

Il appartient à la famille **External Automation Bridges** : un ensemble de dépôts frères (CNC, LASER, OPENPNP, PRINTER3D, ROS2) qui partagent le même contrat de sécurité de `HYDRA-UMC-SDK`, afin qu'aucun pont ne puisse inventer sa propre définition du « sûr pour travailler ».

### Fonctionnalités clés :
* ✅ **Noyau de coordination sans dépendance, réel :** `coordinator.py` — `Ros2Coordinator` n'importe `rclpy` en aucun cas ; c'est du Python pur délibérément, testable sur n'importe quel hôte sans installation de ROS 2. *(implémenté, testé dans `tests/test_coordinator.py`)*
* ✅ **Mappage à trois interfaces, réel :** trois attributs de classe fixes réservent le type d'interface ROS 2 exact pour chaque objectif — `/hydra_umc/machine_state` (topic, état continu), `/hydra_umc/inspect_cell` (service, inspection courte), `/hydra_umc/execute_cell_job` (action, tâche annulable). *(implémenté)*
* ✅ **Portail de sécurité partagé, réel :** chaque tâche envoyée via `Ros2Coordinator.dispatch()` est évaluée par `evaluate_job()` du `bridge_contract` de `HYDRA-UMC-SDK`, le même portail utilisé par tous les ponts frères et HYDRA-UMC-SERVER ; une phase productive nécessite une machine externe `IDLE` et une cellule HYDRA-UMC `READY`, tandis qu'`ABORT` reste demandable pendant un défaut. *(implémenté)*
* ✅ **Routage de phases fermé et évidence statique :** les phases productives ne se mappent que vers l'action de travail planifiée, `ABORT` se mappe vers `/hydra_umc/request_safe_stop` et une future phase SDK inconnue est refusée. `inspect_interface_plan.py` émet le plan statique de schéma `1.1` — incluant la véritable qualité de service (QoS) de durabilité `transient_local` dont le topic d'état a besoin (le propre remplacement de ROS 2 pour l'éditeur « latched » de ROS 1, documenté sur design.ros2.org/articles/qos.html) — sans importer `rclpy` ni contacter DDS. *(implémenté, testé)*
* ✅ **Transport `rclpy` réel et partiel :** `rclpy_transport.py` connecte les 2 interfaces avec un vrai type de message ROS 2 standard dès aujourd'hui — `Ros2SafeStopClient` (un vrai client `std_srvs/Trigger`) et `Ros2StateSubscriber` (un vrai abonné `std_msgs/String`, utilisant la véritable QoS de durabilité `transient_local`). *(implémenté, testé dans `tests/test_rclpy_transport.py`)*
* ✅ **Build/test non mutant :** `build-test.bat`/`.sh` compilent le code source et exécutent des tests unitaires déterministes sans changer la version ni le CHANGELOG. *(implémenté, voir COMPILATION & EXÉCUTION ci-dessous)*
* 🔜 **Contrats `.srv`/`.action` personnalisés pour `inspect_service`/`job_action`** — ceux-ci n'ont pas de vrai type de message ROS 2 standard, donc un client pour eux nécessite que ce dépôt définisse d'abord son propre paquet d'interface. *(prévu)*

---

## 2. 🔄 FLUX DE COORDINATION ROS 2

```mermaid
flowchart LR
    ROS["Nœuds ROS 2"] -- "topic / service / action" --> BRIDGE["BRIDGE-ROS2<br/>Ros2Coordinator.dispatch()"]
    BRIDGE -- BridgeJob --> SDK["HYDRA-UMC-SDK<br/>evaluate_job()"]
    SDK -- GateDecision --> SERVER["HYDRA-UMC-SERVER"]
    SERVER -- "tâche / abandon" --> MCU["Sécurité MCU"]
```

---

## 3. 🧱 ARCHITECTURE ET CHOIX DE CONCEPTION

* **Pourquoi `coordinator.py` n'a aucune dépendance à `rclpy`.** Son propre docstring de module l'affirme délibérément : il « peut être testé sur n'importe quel hôte et ne devient un nœud ROS 2 que via un adaptateur déployé séparément ». Cela garde la logique de coordination liée à la sécurité testable en CI sans installation ROS 2, et permet de choisir et valider l'adaptateur indépendamment, plus tard.
* **Pourquoi trois types d'interface distincts plutôt qu'un canal générique.** `state_topic`, `inspect_service` et `job_action` correspondent délibérément à la propre sémantique de ROS 2 : la publication continue d'état n'a pas besoin de requête/réponse (topic), une inspection rapide a besoin d'une réponse synchrone (service), et le travail de cellule doit pouvoir être annulé en cours d'exécution (action) — regrouper cela en un seul canal ferait perdre cette distinction.
* **Pourquoi `Ros2Coordinator.dispatch()` fait quand même passer chaque tâche par le portail partagé `evaluate_job()`.** ROS 2 n'est qu'un client de plus du même `bridge_contract` utilisé par CNC, LASER, OPENPNP et PRINTER3D — il ne bénéficie d'aucun contournement spécial de la logique IDLE/READY appliquée par tous les autres ponts et par HYDRA-UMC-SERVER.
* **Pourquoi `ABORT` reste demandable pendant un défaut.** L'exigence de phase productive du portail (`IDLE` + `READY`) n'est délibérément pas appliquée de la même manière à une demande d'abandon — un opérateur ou un nœud ROS 2 doit toujours pouvoir demander un arrêt contrôlé, même en plein défaut.
* **Pourquoi l'adaptateur `rclpy` et les contrats ROS `.msg`/`.srv`/`.action` ne sont pas encore dans ce dépôt.** S'engager sur des définitions de messages/services/actions spécifiques avant qu'un environnement ROS 2 réel ne soit sélectionné et testé risquerait d'intégrer des hypothèses que ce noyau local sans dépendance ne peut pas vérifier.
* **Comment cela s'intègre dans le reste de l'écosystème.** BRIDGE-ROS2 se situe entre les nœuds ROS 2 et `HYDRA-UMC-SDK` → `HYDRA-UMC-SERVER` → sécurité MCU : c'est une frontière de coordination, jamais un nœud de contrôle moteur, et elle ne peut pas contourner HYDRA-UMC-SERVER, les limites du MCU, les watchdogs ou l'E-STOP.

---

## 📂 STRUCTURE DES RÉPERTOIRES

```text
HYDRA-UMC-BRIDGE-ROS2/
├── src/
│   └── hydra_umc_bridge_ros2/
│       ├── __init__.py
│       ├── coordinator.py       # Ros2Coordinator : portail topic/service/action sans dépendance
│       ├── rclpy_transport.py   # Transport rclpy réel - seulement les 2 interfaces avec un type de message ROS 2 réel et standard
│       └── mqtt_transport.py    # Transport MQTT réel du broker pour la logique déjà réelle de ce bridge
├── tests/
│   ├── test_coordinator.py      # Tests unitaires déterministes du noyau de coordination
│   ├── test_rclpy_transport.py  # Tests de transport rclpy réel contre un nœud/publisher simulé
│   ├── test_mqtt_transport.py   # Tests de forme commande/état MQTT contre un client broker simulé
│   └── fixtures/
│       ├── interface-plan-v1.json    # Fixture de compatibilité d'interface schema-1.0
│       └── interface-plan-v1.1.json  # Fixture publiée de compatibilité d'interface schema-1.1
├── tools/
│   ├── build_test.py            # Compilateur + lanceur de tests non mutant (build-test.bat/.sh)
│   └── bump_version.py          # Synchronise pyproject.toml, manifeste et CHANGELOG.md
├── docs/
│   └── BRIDGE_GUIDE.md          # Portée, plateformes compatibles, scripts, portail d'acceptation matérielle
├── images/
│   └── HYDRA_UMC_BANNER.svg     # Bannière du README
├── build-test.bat / build-test.sh  # Valide uniquement, ne modifie jamais le dépôt
├── build.bat / build.sh            # Valide puis, si succès, incrémente version + CHANGELOG
├── pyproject.toml               # Métadonnées du paquet ; dépend de HYDRA-UMC-SDK (git)
├── hydra-umc.project.json       # Manifeste de l'écosystème (version, maturité, famille)
├── CHANGELOG.md
├── CODE_OF_CONDUCT.md / CONTRIBUTING.md / SECURITY.md / SUPPORT.md
├── LICENSE / LICENSE.md
└── README.md / README_*.md      # Ce fichier et ses 6 traductions
```

---

## 4. ⚙️ COMPILATION ET EXÉCUTION

Nécessite Python 3.11+. `tools/build_test.py` attend que `HYDRA-UMC-SDK` soit cloné en tant que répertoire frère (`../HYDRA-UMC-SDK`) ou indiqué via la variable d'environnement `HYDRA_UMC_SDK_ROOT`.

```bash
# Windows
build-test.bat      # validation uniquement — pas de changement de version/CHANGELOG
build.bat            # valide puis, si succès, incrémente version + CHANGELOG

# Linux/macOS
bash build-test.sh
bash build.sh
```

`build-test` compile chaque module sous `src/` avec `py_compile` et exécute la suite complète `unittest` (`tests/test_coordinator.py`) — de manière déterministe, sans installation ROS 2, sans réseau et sans changement de version/CHANGELOG. `build` exécute d'abord cette même validation et, seulement en cas de succès, appelle `tools/bump_version.py` pour synchroniser la version dans `pyproject.toml`, `hydra-umc.project.json` et `CHANGELOG.md`. Il n'existe pas encore de commande `run` matérielle réelle — cela nécessite un déploiement ROS 2 validé.

---

## ✅ ÉTAT ACTUEL ET PROCHAINES ÉTAPES

**Réel aujourd'hui :** version `0.0.5`, fonctionnel en tant que noyau de coordination sans dépendance (`Ros2Coordinator`) avec dix tests de sécurité locaux déterministes, un routage de phases fermé, un schéma d'interface statique `plan-only` déclarant la véritable qualité de service (QoS) de durabilité `transient_local` dont le topic d'état a besoin, un transport `rclpy` réel (importé de façon paresseuse) pour les 2 interfaces avec un vrai type de message ROS 2 standard, et des scripts build-test non mutants intégrés en CI avec un checkout du SDK.

**Frontière d'intégration :** ce pont n'est qu'une frontière de coordination — ce n'est pas un nœud de contrôle moteur, et il ne peut pas contourner HYDRA-UMC-SERVER, les limites du MCU, les watchdogs ou l'E-STOP ; chaque tâche envoyée passe toujours par le même portail partagé utilisé par tous les ponts frères.

**Encore à venir :** aucun réseau ROS, robot ou actionneur physique n'a encore été validé — l'adaptateur `rclpy` et les contrats ROS `.msg`/`.srv`/`.action` concrets seront introduits seulement après la sélection et le test d'un environnement ROS 2 réel.

---

## 🔗 PROJETS LIÉS

Ce projet fait partie d'un écosystème robotique plus large du même auteur (JuanenRac / Electro Hobby 3D), couvrant firmware, logiciel de contrôle, nœuds d'IA et outillage de flotte. Cela vaut la peine de le savoir, car une demande pourrait en réalité concerner l'un de ces projets plutôt que ce dépôt.

### Directement liés

- **[HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK)** — le contrat partagé de tâches et de sécurité à travers lequel ce pont (et tous les autres) évalue ses tâches.
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — la frontière authentifiée de l'écosystème à laquelle ce pont rend compte.
- **[HYDRA-UMC-MQTT-BROKER](https://github.com/JuanenRac/HYDRA-UMC-MQTT-BROKER)** — le vrai transport de `mqtt_transport.py` pour les propres topics `hydra/bridges/ros2/...` de ce pont (le portail de tâche uniquement basé sur le plan, un vrai appel d'arrêt sécurisé `std_srvs/Trigger`, et le vrai topic d'état ROS 2 republié comme « l'adaptateur déployé séparément » que `rclpy_transport.py` a toujours anticipé) - voir le propre `docs/BRIDGE_TOPICS.md` de ce dépôt.
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** — voie de preuve hardware-in-the-loop pour un déploiement ROS 2 réel.

### Reste de l'écosystème

**Plateforme HYDRA-UMC** — la micro-usine multi-robot pour laquelle ce pont coordonne les auxiliaires
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — la carte mère CM5 + STM32H745 orchestrant jusqu'à 8 bras robotiques.
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — le backend Express/WebSocket auquel parlent tous les clients de contrôle et ponts.
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** — tableau de bord web, visualisation 3D multi-robot.

**External Automation Bridges** — dépôts frères partageant ce même portail de tâches `HYDRA-UMC-SDK`
- **[HYDRA-UMC-BRIDGE-CNC](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-CNC)** — pont de coordination de cellule CNC.
- **[HYDRA-UMC-BRIDGE-LASER](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-LASER)** — pont de coordination de cellules laser.
- **[HYDRA-UMC-BRIDGE-OPENPNP](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-OPENPNP)** — pont de flux de cartes pour OpenPnP.
- **[HYDRA-UMC-BRIDGE-PRINTER3D](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-PRINTER3D)** — pont de coordination pour logiciels d'impression 3D ouverts.

**Preuves de sécurité et d'intégration**
- **[HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)** — preuves de sécurité des zones de cellule utilisées dans toute la famille de ponts.
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** — preuves de tests hardware-in-the-loop.

## 👤 AUTEUR
**JuanenRac** (Electro Hobby 3D)
📧 electrohobby3d@gmail.com
📺 [youtube.com/@electrohobby3d](https://youtube.com/@electrohobby3d)

## 📜 LICENCE
GPL-3.0 - Voir LICENSE pour les détails.
