<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Puente de coordinación bidireccional con ROS 2
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="Banner de HYDRA-UMC-BRIDGE-ROS2" width="100%">
</p>

# 🤖 HYDRA-UMC-BRIDGE-ROS2

<p align="center"><a href="README.md">🇺🇸 English</a> | 🇪🇸 <b>Español</b> | <a href="README_fra.md">🇫🇷 Français</a> | <a href="README_ita.md">🇮🇹 Italiano</a> | <a href="README_deu.md">🇩🇪 Deutsch</a> | <a href="README_zho.md">🇨🇳 简体中文</a> | <a href="README_jpn.md">🇯🇵 日本語</a></p>

### 🔗 Frontera de coordinación sin dependencias entre HYDRA-UMC y ROS 2

<p align="left">
  <img src="https://img.shields.io/badge/Licencia-GPL%203.0-blue.svg" alt="GPL 3.0">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Safety-Fails%20Closed-red.svg" alt="Seguro por defecto">
</p>

---

## 1. 🛠️ VISIÓN TÉCNICA GENERAL

**HYDRA-UMC-BRIDGE-ROS2** es la frontera de coordinación bidireccional de alto nivel entre HYDRA-UMC y ROS 2. Mapea la observación continua a un topic, la inspección inmediata a un service, y el trabajo de celda de larga duración a una action cancelable. No es un nodo de control de motores y no puede eludir HYDRA-UMC-SERVER, los límites de MCU, los watchdogs ni el E-STOP.

Pertenece a la familia **External Automation Bridges**: un conjunto de repositorios hermanos (CNC, LASER, OPENPNP, PRINTER3D, ROS2) que hablan el mismo contrato de seguridad compartido de `HYDRA-UMC-SDK`, de modo que ningún puente puede inventar su propia definición de "seguro para trabajar".

### Características clave:
* ✅ **Núcleo de coordinación sin dependencias, real:** `coordinator.py` — `Ros2Coordinator` no importa `rclpy` en absoluto; es Python plano de forma deliberada, comprobable en cualquier host sin una instalación de ROS 2. *(implementado, probado en `tests/test_coordinator.py`)*
* ✅ **Mapeo de interfaz triple, real:** tres atributos de clase fijos reservan el tipo exacto de interfaz ROS 2 para cada propósito — `/hydra_umc/machine_state` (topic, estado continuo), `/hydra_umc/inspect_cell` (service, inspección corta), `/hydra_umc/execute_cell_job` (action, trabajo cancelable). *(implementado)*
* ✅ **Puerta de seguridad compartida, real:** cada trabajo despachado a través de `Ros2Coordinator.dispatch()` se evalúa mediante `evaluate_job()` de `bridge_contract` en `HYDRA-UMC-SDK`, la misma puerta que usan todos los puentes hermanos y HYDRA-UMC-SERVER; una fase productiva requiere una máquina externa `IDLE` y una celda HYDRA-UMC `READY`, mientras que `ABORT` sigue siendo solicitable durante un fallo. *(implementado)*
* ✅ **Enrutado de fases cerrado y evidencia estática:** las fases productivas se asignan solo a la acción de trabajo planificada, `ABORT` se asigna a `/hydra_umc/request_safe_stop` y una fase futura desconocida del SDK se deniega. `inspect_interface_plan.py` emite el plan estático de esquema `1.0` sin importar `rclpy` ni contactar DDS. *(implementado, probado)*
* ✅ **Compilación/prueba no mutante:** `build-test.bat`/`.sh` compilan el código y ejecutan pruebas unitarias deterministas sin cambiar la versión ni el CHANGELOG. *(implementado, ver COMPILACIÓN Y EJECUCIÓN más abajo)*
* 🔜 **Adaptador `rclpy` y contratos ROS `.msg`/`.srv`/`.action`** — se introducirán solo después de seleccionar y probar un entorno ROS 2 real. *(planeado)*

---

## 2. 🔄 FLUJO DE COORDINACIÓN ROS 2

```mermaid
flowchart LR
    ROS["Nodos ROS 2"] -- "topic / service / action" --> BRIDGE["BRIDGE-ROS2<br/>Ros2Coordinator.dispatch()"]
    BRIDGE -- BridgeJob --> SDK["HYDRA-UMC-SDK<br/>evaluate_job()"]
    SDK -- GateDecision --> SERVER["HYDRA-UMC-SERVER"]
    SERVER -- "trabajo / aborto" --> MCU["Seguridad MCU"]
```

---

## 3. 🧱 ARQUITECTURA Y DECISIONES DE DISEÑO

* **Por qué `coordinator.py` no tiene dependencia de `rclpy`.** Su propio docstring de módulo lo afirma deliberadamente: "puede probarse en cualquier host y solo se convierte en un nodo ROS 2 mediante un adaptador desplegado por separado". Esto mantiene comprobable en CI la lógica de coordinación relevante para la seguridad sin necesidad de una instalación de ROS 2, y permite elegir y validar el adaptador de forma independiente, más adelante.
* **Por qué tres tipos de interfaz distintos en lugar de un canal genérico.** `state_topic`, `inspect_service` y `job_action` se corresponden deliberadamente con la propia semántica de ROS 2: la publicación continua de estado no necesita petición/respuesta (topic), una inspección rápida necesita una respuesta síncrona (service), y el trabajo de celda necesita poder cancelarse a mitad de ejecución (action) — reducir esto a un solo canal perdería esa distinción.
* **Por qué `Ros2Coordinator.dispatch()` sigue canalizando cada trabajo a través de la puerta compartida `evaluate_job()`.** ROS 2 es simplemente otro cliente del mismo `bridge_contract` que usan CNC, LASER, OPENPNP y PRINTER3D — no obtiene ninguna excepción especial de la lógica IDLE/READY que aplican todos los demás puentes y HYDRA-UMC-SERVER.
* **Por qué `ABORT` sigue siendo solicitable durante un fallo.** El requisito de fase productiva de la puerta (`IDLE` + `READY`) deliberadamente no se aplica de la misma manera a una solicitud de aborto — un operador o un nodo ROS 2 siempre debe poder pedir una parada controlada, incluso en mitad de un fallo.
* **Por qué el adaptador `rclpy` y los contratos ROS `.msg`/`.srv`/`.action` todavía no están en este repositorio.** Comprometerse con definiciones concretas de mensajes/servicios/acciones antes de seleccionar y probar un entorno ROS 2 real arriesgaría a incorporar suposiciones que este núcleo local sin dependencias no puede verificar.
* **Cómo encaja en el resto del ecosistema.** BRIDGE-ROS2 se sitúa entre los nodos ROS 2 y `HYDRA-UMC-SDK` → `HYDRA-UMC-SERVER` → seguridad de MCU: es una frontera de coordinación, nunca un nodo de control de motores, y no puede eludir HYDRA-UMC-SERVER, los límites de MCU, los watchdogs ni el E-STOP.

---

## 📂 ESTRUCTURA DE DIRECTORIOS

```text
HYDRA-UMC-BRIDGE-ROS2/
├── src/
│   └── hydra_umc_bridge_ros2/
│       ├── __init__.py
│       └── coordinator.py       # Ros2Coordinator: puerta topic/service/action sin dependencias
├── tests/
│   └── test_coordinator.py      # Pruebas unitarias deterministas del núcleo de coordinación
├── tools/
│   ├── build_test.py            # Compilador + ejecutor de pruebas no mutante (build-test.bat/.sh)
│   └── bump_version.py          # Sincroniza pyproject.toml, manifiesto y CHANGELOG.md
├── build-test.bat / build-test.sh  # Solo valida, nunca modifica el repositorio
├── build.bat / build.sh            # Valida y, solo si tiene éxito, sube versión + CHANGELOG
├── pyproject.toml               # Metadatos del paquete; depende de HYDRA-UMC-SDK (git)
├── hydra-umc.project.json       # Manifiesto del ecosistema (versión, madurez, familia)
├── CHANGELOG.md
├── CODE_OF_CONDUCT.md / CONTRIBUTING.md / SECURITY.md / SUPPORT.md
├── LICENSE / LICENSE.md
└── README.md / README_*.md      # Este archivo y sus 6 traducciones
```

---

## 4. ⚙️ COMPILACIÓN Y EJECUCIÓN

Requiere Python 3.11+. `tools/build_test.py` espera que `HYDRA-UMC-SDK` esté clonado como directorio hermano (`../HYDRA-UMC-SDK`) o indicado mediante la variable de entorno `HYDRA_UMC_SDK_ROOT`.

```bash
# Windows
build-test.bat      # solo valida — sin cambio de versión/CHANGELOG
build.bat            # valida y, si tiene éxito, sube versión + CHANGELOG

# Linux/macOS
bash build-test.sh
bash build.sh
```

`build-test` compila cada módulo bajo `src/` con `py_compile` y ejecuta la batería completa de `unittest` (`tests/test_coordinator.py`) — de forma determinista, sin instalación de ROS 2, sin red y sin cambio de versión/CHANGELOG. `build` ejecuta primero esa misma validación y, solo si tiene éxito, llama a `tools/bump_version.py` para sincronizar la versión en `pyproject.toml`, `hydra-umc.project.json` y `CHANGELOG.md`. Todavía no existe un comando `run` real de hardware — eso requiere un despliegue ROS 2 validado.

---

## ✅ ESTADO ACTUAL Y PRÓXIMOS PASOS

**Real hoy:** versión `0.0.2`, funcional como núcleo de coordinación sin dependencias (`Ros2Coordinator`) con cinco pruebas locales deterministas de seguridad, enrutado de fases cerrado, un esquema de interfaz estático `plan-only` y scripts build-test no mutantes conectados a CI con un checkout del SDK.

**Frontera de integración:** este puente es únicamente una frontera de coordinación — no es un nodo de control de motores, y no puede eludir HYDRA-UMC-SERVER, los límites de MCU, los watchdogs ni el E-STOP; cada trabajo despachado sigue pasando por la misma puerta compartida que usan todos los puentes hermanos.

**Todavía pendiente:** todavía no se ha validado ninguna red ROS, robot ni actuador físico — el adaptador `rclpy` y los contratos concretos ROS `.msg`/`.srv`/`.action` se introducirán solo después de seleccionar y probar un entorno ROS 2 real.

---

## 🔗 PROYECTOS RELACIONADOS

Este proyecto forma parte de un ecosistema robótico más amplio del mismo autor (JuanenRac / Electro Hobby 3D), que abarca firmware, software de control, nodos de IA y herramientas de flota. Merece la pena conocerlo, ya que una petición podría en realidad referirse a uno de estos proyectos y no a este repositorio.

### Directamente relacionados

- **[HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK)** — el contrato compartido de trabajos y seguridad a través del cual este puente (y todos los demás) evalúa sus trabajos.
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — la frontera autenticada del ecosistema a la que reporta este puente.
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** — vía de evidencia hardware-in-the-loop para un despliegue ROS 2 real.

### Resto del ecosistema

**Plataforma HYDRA-UMC** — la micro-fábrica multi-robot para la que este puente coordina auxiliares
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — la placa base CM5 + STM32H745 que orquesta hasta 8 brazos robóticos.
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — el backend Express/WebSocket con el que hablan todos los clientes de control y puentes.
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** — panel de control web, visualización 3D multi-robot.

**External Automation Bridges** — repositorios hermanos que comparten esta misma puerta de trabajo de `HYDRA-UMC-SDK`
- **[HYDRA-UMC-BRIDGE-CNC](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-CNC)** — puente de coordinación de celda CNC.
- **[HYDRA-UMC-BRIDGE-LASER](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-LASER)** — puente de coordinación de celdas láser.
- **[HYDRA-UMC-BRIDGE-OPENPNP](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-OPENPNP)** — puente de flujo de placas para OpenPnP.
- **[HYDRA-UMC-BRIDGE-PRINTER3D](https://github.com/JuanenRac/HYDRA-UMC-BRIDGE-PRINTER3D)** — puente de coordinación para software abierto de impresión 3D.

**Evidencia de seguridad e integración**
- **[HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)** — evidencia de seguridad de zonas de celda usada en toda la familia de puentes.
- **[HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)** — evidencia de pruebas hardware-in-the-loop.

## 👤 AUTOR
**JuanenRac** (Electro Hobby 3D)
📧 electrohobby3d@gmail.com

## 📜 LICENCIA
GPL-3.0 - Ver LICENSE para más detalles.

## 🛠️ COMPILACIÓN Y EJECUCIÓN

Usa la comprobación de compilación sin versionado antes de una compilación de publicación:

| Acción | Windows | Linux / macOS |
|---|---|---|
| Comprobación de compilación (sin cambio de versión ni CHANGELOG) | `build-test.bat` | `./build-test.sh` |
| Ejecución / desarrollo (cuando exista) | `run*.bat` o `dev*.bat` | `./run*.sh` o `./dev*.sh` |

`build-test.bat` y `build-test.sh` compilan o validan la pila del proyecto sin incrementar `hydra-umc.project.json` ni modificar `CHANGELOG.md`. Solo pueden generar salida normal del compilador. Los scripts `build*.bat`, `build*.sh`, `run*` y `dev*` existentes conservan su comportamiento propio del proyecto, versionado o en tiempo de ejecución; úsalos cuando se necesite ese comportamiento.
