@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
where pwsh >nul 2>nul
if "%ERRORLEVEL%"=="0" (
  set "PS_HOST=pwsh"
) else (
  set "PS_HOST=powershell"
)
"%PS_HOST%" -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%open-shujuan-workbench.ps1" %*
exit /b %ERRORLEVEL%
