<#
.SYNOPSIS
Temporarily enables the BeamNG controller-pose OpenXR API layer for one launch.
.PARAMETER PackageDirectory
Directory containing XR_APILAYER_BEAMNG_controller_poses.json and BeamNGVRPosesLayer.dll.
.PARAMETER BeamNGExecutable
Executable to start with the process-scoped API-layer environment. This may point
to the BeamNG launcher (BeamNG.drive.exe) and should do so when Vulkan must be
selected for BeamNG VR; the Vulkan child process inherits the environment from
this launcher.
#>
param(
  [Parameter(Mandatory=$true)][string]$PackageDirectory,
  [Parameter(Mandatory=$true)][string]$BeamNGExecutable
)

$ErrorActionPreference = 'Stop'
$manifest = Join-Path $PackageDirectory 'XR_APILAYER_BEAMNG_controller_poses.json'
$dll = Join-Path $PackageDirectory 'BeamNGVRPosesLayer.dll'
if (-not (Test-Path $manifest) -or -not (Test-Path $dll)) {
  throw "PackageDirectory must contain the API-layer manifest and DLL"
}

# These changes are process-scoped. They are inherited by BeamNG and disappear
# when this PowerShell is closed; no loader/runtime registry keys are changed.
$env:XR_API_LAYER_PATH = (Resolve-Path $PackageDirectory).Path
$env:XR_ENABLE_API_LAYERS = 'XR_APILAYER_BEAMNG_controller_poses'
$env:XR_LOADER_DEBUG = 'all'
$log = Join-Path $env:TEMP 'BeamNG-OpenXR-loader.log'

Write-Host "Loader diagnostics: $log"
& $BeamNGExecutable *>&1 | Tee-Object -FilePath $log
exit $LASTEXITCODE
