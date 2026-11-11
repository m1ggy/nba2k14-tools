@echo off
cd /d "%~dp0"
title 2K14 Ratings Tool (keep this window open while using the app)
python ratings_tool.py
if errorlevel 1 (
  echo.
  echo The app hit an error - see the message above.
  pause
)
