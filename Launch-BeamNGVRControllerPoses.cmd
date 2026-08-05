@echo off
setlocal
set "PACKAGE_DIR=%~dp0"
set "SCRIPT=%PACKAGE_DIR%scripts\Launch-BeamNGVRControllerPoses.ps1"

if not exist "%SCRIPT%" (
  echo ERROR: PowerShell launcher was not found:
  echo   "%SCRIPT%"
  echo.
  echo Press any key to close this window.
  pause >nul
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -PackageDirectory "%PACKAGE_DIR%" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo ERROR: BeamNG VR Controller Poses launcher failed with exit code %EXITCODE%.
  echo Review the messages above or %%TEMP%%\BeamNGVRPosesLauncher.log.
  echo.
  echo Press any key to close this window.
  pause >nul
)
exit /b %EXITCODE%
