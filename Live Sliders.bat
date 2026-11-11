@echo off
cd /d "%~dp0"
title 2K14 MyCareer Live Sliders (keep this window open while playing)
python live_sliders.py
if errorlevel 1 (
  echo.
  echo The app hit an error - see the message above.
  pause
)
