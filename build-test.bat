@echo off
REM =============================================================================
REM HYDRA-UMC-BRIDGE-ROS2 - Non-mutating build test
REM Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
REM GPL-3.0-or-later - see LICENSE
REM =============================================================================
setlocal
cd /d "%~dp0"
echo *********************************************************************
echo * HYDRA-UMC-BRIDGE-ROS2 - BUILD TEST (NO VERSION CHANGE)           *
echo * 1. Compile Python source.  2. Run deterministic safety tests.    *
echo *********************************************************************
where py >nul 2>&1 && (py -3 tools\build_test.py) || (python tools\build_test.py)
set "RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %RESULT%
