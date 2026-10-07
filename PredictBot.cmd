@echo off
rem PredictBot launcher: double-click this file (or a desktop shortcut to it).
rem It updates the code from GitHub, then starts the app on this laptop.
cd /d "%~dp0"
where git >nul 2>nul
if not errorlevel 1 (
  git pull --ff-only --quiet
  if errorlevel 1 echo Could not update the code from GitHub. Starting the version already on this laptop.
)
set "PY=python"
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
%PY% predictbot\app.py %*
if errorlevel 1 pause
