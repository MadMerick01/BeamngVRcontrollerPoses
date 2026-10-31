<#
.SYNOPSIS
One-time install of BeamNG VR controller poses. Run Install.cmd (double-click).

- Copies the OpenXR layer to %ProgramData%\BeamNGVRControllerPoses and registers it
  as an implicit OpenXR API layer, so it loads automatically. The layer only
  activates inside BeamNG.drive and passes every other VR app straight through.
- Copies vrControllerPoses.zip into your BeamNG mods folder. The mod loads itself.
Undo everything with Uninstall.cmd.
#>
param([string]$UserLocalAppData = $env:LOCALAPPDATA)
$ErrorActionPreference = 'Stop'
$package = Split-Path -Parent $PSScriptRoot

# Registry (HKLM) and ProgramData need admin. Re-launch elevated, passing this
# user's LocalAppData so the mod still lands in *your* BeamNG folder.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
  ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  Start-Process powershell -Verb RunAs -Wait -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
    '-UserLocalAppData', "`"$UserLocalAppData`"")
  exit
}

try {
  # 1. Layer files
  $target = Join-Path $env:ProgramData 'BeamNGVRControllerPoses'
  New-Item -ItemType Directory -Force $target | Out-Null
  Copy-Item (Join-Path $package 'BeamNGVRPosesLayer.dll') $target -Force
  Copy-Item (Join-Path $package 'XR_APILAYER_BEAMNG_controller_poses.json') $target -Force
  $manifest = Join-Path $target 'XR_APILAYER_BEAMNG_controller_poses.json'

  # 2. Register as implicit layer (value name = manifest path, DWORD 0 = enabled).
  # Remove registrations of older copies of this layer first.
  $key = 'HKLM:\SOFTWARE\Khronos\OpenXR\1\ApiLayers\Implicit'
  if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
  $existing = (Get-Item $key).Property
  foreach ($name in $existing) {
    if ($name -like '*XR_APILAYER_BEAMNG_controller_poses.json' -and $name -ne $manifest) {
      Remove-ItemProperty -Path $key -Name $name
    }
  }
  New-ItemProperty -Path $key -Name $manifest -PropertyType DWord -Value 0 -Force | Out-Null
  Write-Host "OpenXR layer installed: $manifest"

  # 3. Mod
  $mods = Join-Path $UserLocalAppData 'BeamNG\BeamNG.drive\current\mods'
  if (Test-Path (Split-Path $mods)) {
    New-Item -ItemType Directory -Force $mods | Out-Null
    Remove-Item (Join-Path $mods 'BeamNGVRcontrollerPoses*.zip') -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $package 'vrControllerPoses.zip') $mods -Force
    Write-Host "Mod installed: $(Join-Path $mods 'vrControllerPoses.zip')"
  } else {
    Write-Warning "BeamNG user folder not found at $(Split-Path $mods). Copy vrControllerPoses.zip into your BeamNG mods folder yourself."
  }

  Write-Host ''
  Write-Host 'Done. Start BeamNG, turn on VR and pull the right trigger.' -ForegroundColor Green
} catch {
  Write-Host "Install failed: $_" -ForegroundColor Red
}
Read-Host 'Press Enter to close'
