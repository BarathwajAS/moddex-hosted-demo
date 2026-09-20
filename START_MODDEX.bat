@echo off
title ModDex
cd /d "%~dp0"

echo.
echo  ============================================================
echo    MODDEX - Vehicle Modification Visualizer
echo  ============================================================
echo.

set PY=
where py >nul 2>nul && set PY=py
if "%PY%"=="" (where python >nul 2>nul && set PY=python)
if "%PY%"=="" (where python3 >nul 2>nul && set PY=python3)

if "%PY%"=="" (
  echo   Python was not found on this computer.
  echo.
  echo   Install it once from:  https://www.python.org/downloads/
  echo   IMPORTANT: tick "Add Python to PATH" on the first screen.
  echo.
  echo   Then double-click this file again.
  echo.
  pause
  exit /b 1
)

echo   Starting... a ModDex window will open shortly.
echo   Keep THIS black window open while you use the tool.
echo.

%PY% ModStudio.py

echo.
echo   ModDex has stopped.
pause
