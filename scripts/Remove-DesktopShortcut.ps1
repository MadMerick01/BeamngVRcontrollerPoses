param([string]$PackageDirectory)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($PackageDirectory)) { $PackageDirectory = Split-Path -Parent $PSScriptRoot }
$packageRoot = (Resolve-Path -LiteralPath $PackageDirectory).Path
$target = Join-Path $packageRoot 'Launch-BeamNGVRControllerPoses.cmd'
$shortcutPath = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'BeamNG VR Controller Poses.lnk'
if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) { Write-Host 'Project desktop shortcut was not found.'; exit 0 }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
if ($shortcut.TargetPath -eq $target) {
  Remove-Item -LiteralPath $shortcutPath -Force
  Write-Host "Removed project desktop shortcut: $shortcutPath"
} else {
  Write-Warning "Shortcut name exists but points elsewhere; leaving it unchanged: $shortcutPath"
}
