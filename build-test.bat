@echo off
rem PRETTY_RUN_BEGIN
if not defined PRETTY_MODE set "PRETTY_MODE=build-test"
set "PRETTY_PY=python"
where py >nul 2>&1 && set "PRETTY_PY=py -3"
if not defined PRETTY_RUN if exist "%~dp0tools\pretty_run.py" (
    set "PRETTY_RUN=1"
    %PRETTY_PY% "%~dp0tools\pretty_run.py" --wrap --mode %PRETTY_MODE% -- cmd /d /c "%~f0" %*
    exit /b
)
rem PRETTY_RUN_END
REM =============================================================================
REM HYDRA-UMC-BRIDGE-ROS2 - Non-mutating build test
REM Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
REM GPL-3.0-or-later - see LICENSE
REM =============================================================================
setlocal
rem Keep the window open for people, never in automation (CI / HYDRA_UMC_CI).
set "KEEP_OPEN=pause"
if defined CI set "KEEP_OPEN=ver >nul"
if "%HYDRA_UMC_CI%"=="1" set "KEEP_OPEN=ver >nul"
cd /d "%~dp0"
echo *********************************************************************
echo * HYDRA-UMC-BRIDGE-ROS2 - BUILD TEST (NO VERSION CHANGE)           *
echo * 1. Compile Python source.  2. Run deterministic safety tests.    *
echo *********************************************************************
where py >nul 2>&1 && (py -3 tools\build_test.py) || (python tools\build_test.py)
set "RESULT=%ERRORLEVEL%"
echo.
%KEEP_OPEN%
exit /b %RESULT%
