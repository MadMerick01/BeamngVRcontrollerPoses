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
