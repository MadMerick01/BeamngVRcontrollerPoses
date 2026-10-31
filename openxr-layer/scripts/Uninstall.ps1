<#
.SYNOPSIS
Removes the BeamNG VR controller poses layer and mod. Run Uninstall.cmd.
#>
param([string]$UserLocalAppData = $env:LOCALAPPDATA)
$ErrorActionPreference = 'Stop'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
  ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  Start-Process powershell -Verb RunAs -Wait -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
    '-UserLocalAppData', "`"$UserLocalAppData`"")
  exit
}

$key = 'HKLM:\SOFTWARE\Khronos\OpenXR\1\ApiLayers\Implicit'
if (Test-Path $key) {
  foreach ($name in (Get-Item $key).Property) {
    if ($name -like '*XR_APILAYER_BEAMNG_controller_poses.json') { Remove-ItemProperty -Path $key -Name $name }
  }
}
Remove-Item (Join-Path $env:ProgramData 'BeamNGVRControllerPoses') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $UserLocalAppData 'BeamNG\BeamNG.drive\current\mods\vrControllerPoses.zip') -ErrorAction SilentlyContinue
Write-Host 'BeamNG VR controller poses removed.' -ForegroundColor Green
Read-Host 'Press Enter to close'
