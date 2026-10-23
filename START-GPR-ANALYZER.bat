@echo off
rem Double-click to start GPR Analyzer on Windows. The first run installs everything it needs.
rem Keep the black window open while using the app; close it to stop the app.
title GPR Analyzer
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\launch.ps1" %*
if errorlevel 1 (
  echo.
  pause
)
