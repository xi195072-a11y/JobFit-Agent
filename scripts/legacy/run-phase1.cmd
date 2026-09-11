@echo off
rem JobFit Agent Phase 1 - one-click launcher (self-contained, no UAC needed after WSL/Docker installed).
rem Double-click this file. Progress is logged to D:\ai\phase1.log
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\ai\phase1-continue.ps1"
exit /b 0
