@echo off
setlocal
chcp 65001 >nul
title SOP计划管理
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-sop.ps1" %*
set "SOP_EXIT_CODE=%ERRORLEVEL%"
if not "%SOP_EXIT_CODE%"=="0" (
    echo.
    echo Could not start SOP Plan Management. See the error above.
    if /I not "%~1"=="-NoBrowser" pause
)
exit /b %SOP_EXIT_CODE%
