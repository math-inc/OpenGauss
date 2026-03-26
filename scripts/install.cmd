@echo off
REM ============================================================================
REM Open Gauss WSL Installer (CMD wrapper)
REM ============================================================================
REM Windows support is WSL-first. This wrapper forwards to install.ps1, which
REM bootstraps WSL2 and then runs the standard Linux installer there.
REM ============================================================================

echo.
echo  Open Gauss WSL Installer
echo  Launching PowerShell bootstrap...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -File "%~dp0install.ps1" %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed.
    echo  Re-run in PowerShell for the full error output:
    echo    powershell -ExecutionPolicy ByPass -NoProfile -File "%~dp0install.ps1"
    echo.
    exit /b 1
)
