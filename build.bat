@echo off
REM =============================================================================
REM HYDRA-UMC-BRIDGE-ROS2 - Incremental build workflow
REM Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
REM GPL-3.0-or-later - see LICENSE
REM =============================================================================
setlocal
cd /d "%~dp0"
echo.
echo *******************************************************************************
echo * HYDRA-UMC-BRIDGE-ROS2 - build.bat
echo * Mode      : INCREMENTAL BUILD
echo * Author    : JuanenRac ^(Electro Hobby 3D^)
echo * Email     : electrohobby3d@gmail.com
echo * Copyright : ^(C^) 2026 JuanenRac
echo * License   : GPL-3.0-or-later - see LICENSE
echo * ------------------------------------------------------------------------- *
echo * 1. Validate source and deterministic safety tests without mutation.
echo * 2. Synchronize native version, manifest and CHANGELOG after success.
echo * 3. Report the result and keep this terminal open.
echo *******************************************************************************
echo.
echo [1/2] Running non-mutating build test...
where py >nul 2>&1
if errorlevel 1 (python tools\build_test.py) else (py -3 tools\build_test.py)
if errorlevel 1 goto :error
echo [2/2] Incrementing synchronized project version...
where py >nul 2>&1
if errorlevel 1 (python tools\bump_version.py) else (py -3 tools\bump_version.py)
if errorlevel 1 goto :error
echo BUILD=PASS. Version, manifest and CHANGELOG were synchronized.
echo.
pause
exit /b 0
:error
echo BUILD FAILED. No version increment is attempted after a failed validation.
echo.
pause
exit /b 1
