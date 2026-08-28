<!-- =============================================================================
HYDRA-UMC-BRIDGE-ROS2 - Puente de coordinación bidireccional ROS 2
Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
GPL-3.0-or-later - see LICENSE
============================================================================= -->

# HYDRA-UMC-BRIDGE-ROS2

[🇺🇸 English](README.md) | 🇪🇸 **Español** | [🇫🇷 Français](README_fra.md) | [🇮🇹 Italiano](README_ita.md) | [🇩🇪 Deutsch](README_deu.md) | [🇨🇳 简体中文](README_zho.md) | [🇯🇵 日本語](README_jpn.md)

Límite de coordinación bidireccional de alto nivel entre HYDRA-UMC y ROS 2.
Mapea la observación continua a un topic, la inspección inmediata a un servicio
y el trabajo prolongado de celda a una acción cancelable. No es un nodo de
control de motores ni puede saltarse HYDRA-UMC-SERVER, límites MCU o E-STOP.

## Arquitectura

```text
Nodos ROS 2 <-> BRIDGE-ROS2 <-> HYDRA-UMC-SDK <-> SERVER <-> seguridad MCU
```

`/hydra_umc/machine_state` queda reservado para publicar estado,
`/hydra_umc/inspect_cell` para inspección corta y
`/hydra_umc/execute_cell_job` para trabajo cancelable. Cada trabajo tiene una
clave de idempotencia. Las fases productivas requieren una máquina externa
`IDLE` y una celda HYDRA-UMC `READY`; `ABORT` sigue disponible durante un fallo.

## Compilar y probar

Ejecuta `build-test.bat` en Windows o `bash build-test.sh` en Linux. Compilan
el código y ejecutan pruebas deterministas sin cambiar versión ni CHANGELOG.
El futuro adaptador rclpy, los contratos ROS `.msg`/`.srv`/`.action` y la
integración DDS solo se incorporarán tras seleccionar y probar ROS 2 real.

## Proyectos relacionados

| Proyecto | Función |
| --- | --- |
| [HYDRA-UMC-SDK](https://github.com/JuanenRac/HYDRA-UMC-SDK) | Contrato compartido de trabajos y seguridad. |
| [HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER) | Límite autenticado del ecosistema. |
| [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE) | Ruta de evidencia hardware-en-el-bucle. |

## Estado

La versión `0.0.1` es funcional como núcleo de coordinación sin dependencias
con pruebas locales de seguridad. Todavía no se ha validado red ROS, robot ni
actuador físico.
