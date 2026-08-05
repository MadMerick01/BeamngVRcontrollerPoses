param([string]$PackageDirectory)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($PackageDirectory)) { $PackageDirectory = Split-Path -Parent $PSScriptRoot }
$packageRoot = (Resolve-Path -LiteralPath $PackageDirectory).Path
$target = Join-Path $packageRoot 'Launch-BeamNGVRControllerPoses.cmd'
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Launcher not found: $target" }
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$shortcutPath = Join-Path $desktop 'BeamNG VR Controller Poses.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $packageRoot
$shortcut.Description = 'Launch BeamNG with BeamNG VR Controller Poses enabled for this process tree only.'
$shortcut.Save()
Write-Host "Installed per-user desktop shortcut: $shortcutPath"
