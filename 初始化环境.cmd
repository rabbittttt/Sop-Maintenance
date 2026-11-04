@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-environment.ps1" %*
set "SOP_SETUP_EXIT=%ERRORLEVEL%"
if not "%SOP_SETUP_EXIT%"=="0" echo SOP environment setup failed. See the message above.
pause
exit /b %SOP_SETUP_EXIT%
