<#
.SYNOPSIS
Safely launches BeamNG through its normal launcher with this OpenXR API layer enabled only for the child process tree.
#>
param(
  [string]$PackageDirectory,
  [switch]$ResetLauncherPath,
  [switch]$ValidateOnly,
  [switch]$NoLaunch,
  [string]$LauncherPath
)

$ErrorActionPreference = 'Stop'
$LayerName = 'XR_APILAYER_BEAMNG_controller_poses'
$LauncherLog = Join-Path $env:TEMP 'BeamNGVRPosesLauncher.log'
$LoaderLog = Join-Path $env:TEMP 'BeamNG-OpenXR-loader.log'
$LayerLog = Join-Path $env:TEMP 'BeamNGVRPosesLayer.log'
$ConfigDir = Join-Path $env:LOCALAPPDATA 'BeamNGVRControllerPoses'
$ConfigPath = Join-Path $ConfigDir 'launcher.json'

function Write-LauncherLog([string]$Message) {
  $line = '{0} {1}' -f (Get-Date -Format o), $Message
  Add-Content -LiteralPath $LauncherLog -Value $line -Encoding UTF8
}

function Resolve-PackageDirectory([string]$Directory) {
  if ([string]::IsNullOrWhiteSpace($Directory)) {
    $Directory = Split-Path -Parent $PSScriptRoot
  }
  return (Resolve-Path -LiteralPath $Directory).Path.TrimEnd('\')
}

function Test-BeamNGRootLauncher([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
  $file = Get-Item -LiteralPath $Path
  if ($file.Name -ne 'BeamNG.drive.exe') { return $false }
  if ($file.FullName -match '(?i)[\\/]Bin64[\\/]') { return $false }
  return $true
}

function Read-SavedLauncherPath {
  if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { return $null }
  try { return (Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json).launcherPath } catch { return $null }
}

function Save-LauncherPath([string]$Path) {
  New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
  @{ launcherPath = $Path; savedAt = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
}

function Get-SteamLibraries {
  $roots = New-Object System.Collections.Generic.List[string]
  foreach ($key in 'HKCU:\Software\Valve\Steam','HKLM:\SOFTWARE\WOW6432Node\Valve\Steam','HKLM:\SOFTWARE\Valve\Steam') {
    try {
      $p = (Get-ItemProperty -Path $key -ErrorAction Stop).SteamPath
      if ($p) { $roots.Add($p) }
      $p = (Get-ItemProperty -Path $key -ErrorAction Stop).InstallPath
      if ($p) { $roots.Add($p) }
    } catch {}
  }
  foreach ($p in @('C:\Program Files (x86)\Steam','C:\Program Files\Steam','D:\SteamLibrary','E:\SteamLibrary')) { $roots.Add($p) }
  $libs = New-Object System.Collections.Generic.List[string]
  foreach ($root in ($roots | Select-Object -Unique)) {
    if (-not $root) { continue }
    $libs.Add($root)
    $vdf = Join-Path $root 'steamapps\libraryfolders.vdf'
    if (Test-Path -LiteralPath $vdf) {
      $text = Get-Content -LiteralPath $vdf -Raw
      foreach ($m in [regex]::Matches($text, '"path"\s+"([^"`r`n]+)"')) { $libs.Add(($m.Groups[1].Value -replace '\\','\')) }
    }
  }
  return $libs | Select-Object -Unique
}

function Find-BeamNGLauncherAutomatically {
  foreach ($lib in Get-SteamLibraries) {
    foreach ($candidate in @(
      (Join-Path $lib 'steamapps\common\BeamNG.drive\BeamNG.drive.exe'),
      (Join-Path $lib 'common\BeamNG.drive\BeamNG.drive.exe')
    )) {
      if (Test-BeamNGRootLauncher $candidate) { return (Resolve-Path -LiteralPath $candidate).Path }
    }
  }
  return $null
}

function Select-BeamNGLauncherWithDialog {
  Add-Type -AssemblyName System.Windows.Forms
  $dialog = New-Object System.Windows.Forms.OpenFileDialog
  $dialog.Title = 'Select the root BeamNG.drive.exe launcher (not Bin64\BeamNG.drive.x64.exe)'
  $dialog.Filter = 'BeamNG launcher (BeamNG.drive.exe)|BeamNG.drive.exe|Executables (*.exe)|*.exe'
  if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dialog.FileName }
  throw 'No BeamNG launcher selected.'
}

function Get-BeamNGLauncher([string]$ExplicitPath) {
  if ($ExplicitPath) {
    if (-not (Test-BeamNGRootLauncher $ExplicitPath)) { throw "Selected file is not the root BeamNG.drive.exe launcher: $ExplicitPath" }
    Save-LauncherPath (Resolve-Path -LiteralPath $ExplicitPath).Path
    return (Resolve-Path -LiteralPath $ExplicitPath).Path
  }
  if (-not $ResetLauncherPath) {
    $saved = Read-SavedLauncherPath
    if (Test-BeamNGRootLauncher $saved) { return (Resolve-Path -LiteralPath $saved).Path }
    if ($saved) { Write-Host "Saved BeamNG launcher path is invalid; rediscovering: $saved" }
  }
  $auto = Find-BeamNGLauncherAutomatically
  if ($auto) { Save-LauncherPath $auto; return $auto }
  $chosen = Select-BeamNGLauncherWithDialog
  if (-not (Test-BeamNGRootLauncher $chosen)) { throw "Selected file is not the root BeamNG.drive.exe launcher: $chosen" }
  Save-LauncherPath (Resolve-Path -LiteralPath $chosen).Path
  return (Resolve-Path -LiteralPath $chosen).Path
}

function Test-PackageManifest([string]$PackageRoot) {
  $manifestPath = Join-Path $PackageRoot 'XR_APILAYER_BEAMNG_controller_poses.json'
  $dllPath = Join-Path $PackageRoot 'BeamNGVRPosesLayer.dll'
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "Missing API-layer manifest: $manifestPath" }
  if (-not (Test-Path -LiteralPath $dllPath -PathType Leaf)) { throw "Missing API-layer DLL: $dllPath" }
  $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  $libraryPath = $manifest.api_layer.library_path
  if (-not $libraryPath) { throw 'Manifest does not contain api_layer.library_path.' }
  if ([IO.Path]::IsPathRooted($libraryPath)) { $resolvedLibrary = [IO.Path]::GetFullPath($libraryPath) }
  else { $resolvedLibrary = [IO.Path]::GetFullPath((Join-Path $PackageRoot $libraryPath)) }
  if ($resolvedLibrary -ne [IO.Path]::GetFullPath($dllPath)) { throw "Manifest library_path resolves to '$resolvedLibrary', expected '$dllPath'." }
  return @{ Manifest = $manifestPath; Dll = $dllPath }
}

function Get-ActiveOpenXRRuntime {
  foreach ($key in 'HKCU:\Software\Khronos\OpenXR\1','HKLM:\SOFTWARE\Khronos\OpenXR\1') {
    try {
      $value = (Get-ItemProperty -Path $key -Name ActiveRuntime -ErrorAction Stop).ActiveRuntime
      if ($value) { return @{ Key = $key; Path = $value } }
    } catch {}
  }
  return $null
}

try {
  Write-LauncherLog '--- launcher start ---'
  $PackageRoot = Resolve-PackageDirectory $PackageDirectory
  Write-LauncherLog "PackageDirectory=$PackageRoot"
  $validation = Test-PackageManifest $PackageRoot
  Write-LauncherLog "ValidationPassed Manifest=$($validation.Manifest) Dll=$($validation.Dll)"

  $runtime = Get-ActiveOpenXRRuntime
  if ($runtime) {
    Write-Host "Active OpenXR runtime manifest: $($runtime.Path) ($($runtime.Key))"
    Write-LauncherLog "ActiveRuntime=$($runtime.Path) Source=$($runtime.Key)"
    if ($runtime.Path -notmatch '(?i)(virtualdesktop|vdxr)') { Write-Warning 'Active OpenXR runtime does not appear to be Virtual Desktop VDXR. The launcher will not change it automatically.' }
  } else {
    Write-Warning 'No ActiveRuntime value found in HKCU or HKLM OpenXR registry keys.'
    Write-LauncherLog 'ActiveRuntime=<not found>'
  }

  Write-Host "OpenXR loader diagnostics: $LoaderLog"
  Write-Host "API layer diagnostics:    $LayerLog"
  Write-Host "Launcher diagnostics:     $LauncherLog"

  $env:XR_API_LAYER_PATH = $PackageRoot
  $env:XR_ENABLE_API_LAYERS = $LayerName
  $env:XR_LOADER_DEBUG = 'all'
  Write-LauncherLog 'Process-scoped OpenXR environment prepared.'

  if ($ValidateOnly) { Write-Host 'Validation-only mode complete.'; exit 0 }
  $beamng = Get-BeamNGLauncher $LauncherPath
  Write-Host "BeamNG launcher: $beamng"
  Write-LauncherLog "BeamNGLauncher=$beamng"
  if ($NoLaunch) { Write-Host 'No-launch mode complete.'; exit 0 }
  $proc = Start-Process -FilePath $beamng -PassThru
  Write-LauncherLog "ChildLaunchSuccess Pid=$($proc.Id)"
  Write-Host 'BeamNG launcher started. Select Vulkan, then start BeamNG VR after entering a map.'
  exit 0
} catch {
  Write-Error $_.Exception.Message
  try { Write-LauncherLog "Failure=$($_.Exception.Message)" } catch {}
  exit 1
}
