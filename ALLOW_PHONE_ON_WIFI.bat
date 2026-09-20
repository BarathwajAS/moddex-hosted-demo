@echo off
title ModDex - allow phone on WiFi

net session >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Asking Windows for administrator rights...
  powershell -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)

cd /d "%~dp0"

echo.
echo  ============================================================
echo    MODDEX - let your phone reach this PC over WiFi
echo  ============================================================
echo.

netsh advfirewall firewall delete rule name="ModDex" >nul 2>nul
netsh advfirewall firewall add rule name="ModDex" dir=in action=allow protocol=TCP localport=8765-8805 profile=private,domain

if errorlevel 1 (
  echo   Could not add the rule. Right-click this file and choose
  echo   "Run as administrator".
  echo.
  pause
  exit /b 1
)

echo   Done. Ports 8765-8805 are now allowed on private networks.
echo.
echo   Next:
echo     1. Start ModDex with START_MODDEX.bat
echo     2. Read the "ON YOUR PHONE" line in that black window
echo     3. Type that address into Chrome on your phone
echo.
echo   If the phone still times out, your WiFi is blocking
echo   device-to-device traffic - common on phone hotspots and
echo   guest networks. Use START_TUNNEL.bat instead.
echo.
pause
