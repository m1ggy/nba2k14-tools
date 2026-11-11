@echo off
cd /d "%~dp0"
title 2K14 Player Creator (keep this window open while using the app)
python player_creator.py
if errorlevel 1 (
  echo.
  echo The app hit an error - see the message above.
  pause
)
