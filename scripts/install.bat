@echo off
setlocal enabledelayedexpansion
title Hermes Agent Installer

echo.
echo +----------------------------------------------------------+
echo ^|          Hermes Agent Installer (Windows)               ^|
echo +----------------------------------------------------------+
echo ^|  An open source AI agent by Nous Research              ^|
echo ^|                                                          ^|
echo ^|  https://github.com/NousResearch/hermes-agent           ^|
echo +----------------------------------------------------------+
echo.

:: ---------------------------------------------------------------
:: This batch file launches the PowerShell installer.
:: CMD users can run it directly:
::
::   curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.bat -o hermes_install.bat && hermes_install.bat
::
:: Or double-click it after downloading.
:: ---------------------------------------------------------------

:: Require PowerShell
where powershell >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: PowerShell was not found on this system.
    echo.
    echo Please open PowerShell manually and run:
    echo.
    echo   irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1 ^| iex
    echo.
    pause
    exit /b 1
)

echo Launching the PowerShell installer...
echo.
echo If prompted about execution policy, type "R" (Run once).
echo.

powershell -ExecutionPolicy Bypass -NoProfile -Command "& { $ErrorActionPreference = 'Stop'; irm 'https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1' | iex }"

if %errorlevel% neq 0 (
    echo.
    echo Installation failed. Check the error above.
    pause
    exit /b %errorlevel%
)

exit /b 0
