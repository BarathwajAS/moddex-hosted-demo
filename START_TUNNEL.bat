@echo off
title ModDex - remote access
cd /d "%~dp0"

if "%MODDEX_PIN%"=="" (
  echo.
  echo  ============================================================
  echo   STOP - no staff PIN is set.
  echo  ============================================================
  echo.
  echo   Putting this online without a PIN means anyone who finds
  echo   the address can spend your Google credit.
  echo.
  echo   Open a Command Prompt here and run:
  echo.
  echo       setx MODDEX_PIN 4321
  echo       setx MODDEX_REMOTE 1
  echo       setx MODDEX_DAILY_CAP 40
  echo.
  echo   Use your own number instead of 4321, then close this
  echo   window, open a NEW one, and run this file again.
  echo.
  echo   Full instructions are in REMOTE_ACCESS.txt
  echo.
  pause
  exit /b 1
)

if not exist cloudflared.exe (
  echo.
  echo  cloudflared.exe is not in this folder.
  echo.
  echo  Download the Windows 64-bit build from
  echo    https://github.com/cloudflare/cloudflared/releases/latest
  echo  rename it to cloudflared.exe and put it next to this file.
  echo.
  pause
  exit /b 1
)

set MODSTUDIO_PORT=8765

echo.
echo  Starting ModDex on port %MODSTUDIO_PORT% ...
start "ModDex server" cmd /k python ModStudio.py --noopen

timeout /t 4 /nobreak >nul

echo.
echo  ============================================================
echo   Opening the tunnel. Your address appears below in a moment,
echo   as a https://....trycloudflare.com link.
echo.
echo   Keep BOTH windows open. Closing either one kills the link.
echo  ============================================================
echo.
cloudflared.exe tunnel --url http://127.0.0.1:%MODSTUDIO_PORT%

echo.
echo  Tunnel closed.
pause
