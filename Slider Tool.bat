@echo off
cd /d "%~dp0"
title 2K14 Slider Tool (keep this window open while using the app)
python slider_tool.py
if errorlevel 1 (
  echo.
  echo The app hit an error - see the message above.
  pause
)
