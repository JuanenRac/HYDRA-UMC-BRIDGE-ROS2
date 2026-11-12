# HYDRA-UMC-BRIDGE-ROS2 - hydra_umc_interfaces/msg/CellStatus
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
#
# What a cell reports after a phase. The values are the ones of the SDK
# (CellState and MachineState); the contract check fails when they drift.
string CELL_READY="READY"
string CELL_INHIBITED="INHIBITED"
string CELL_FAULT="FAULT"
string CELL_SAFE_STOP="SAFE_STOP"
string MACHINE_OFFLINE="OFFLINE"
string MACHINE_IDLE="IDLE"
string MACHINE_RUNNING="RUNNING"
string MACHINE_HOLDING="HOLDING"
string MACHINE_STOPPED="STOPPED"
string MACHINE_FAULT="FAULT"
string MACHINE_SAFE_STOP="SAFE_STOP"
string cell_state
string machine_state
string job_id
