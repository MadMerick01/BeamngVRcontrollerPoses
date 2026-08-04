param([Parameter(Mandatory=$true)][string]$PackageDirectory)
& (Join-Path $PSScriptRoot 'Disable-BeamNGVRPoses.ps1')
if (Test-Path $PackageDirectory) {
  Remove-Item -LiteralPath $PackageDirectory -Recurse -Force
}
Remove-Item (Join-Path $env:TEMP 'BeamNGVRPosesLayer.log') -ErrorAction SilentlyContinue
Write-Host 'Layer files and layer diagnostic log removed. The active runtime was not changed.'
