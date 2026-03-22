@echo off
REM ============================================================================
REM Gauss Agent Installer for Windows (CMD wrapper)
REM ============================================================================
REM This batch file launches the PowerShell installer for users running CMD.
REM
REM Usage:
REM   curl -fsSL https://raw.githubusercontent.com/math-inc/OpenGauss/main/scripts/install.cmd -o install.cmd && install.cmd && del install.cmd
REM
REM Or if you're already in PowerShell, use the direct command instead:
REM   irm https://raw.githubusercontent.com/math-inc/OpenGauss/main/scripts/install.ps1 | iex
REM ============================================================================

echo.
echo  Gauss Agent Installer
echo  Launching PowerShell installer...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://raw.githubusercontent.com/math-inc/OpenGauss/main/scripts/install.ps1 | iex"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. Please try running PowerShell directly:
    echo    powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/math-inc/OpenGauss/main/scripts/install.ps1 | iex"
    echo.
    pause
    exit /b 1
)
