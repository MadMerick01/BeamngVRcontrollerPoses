$ErrorActionPreference = 'Stop'

$keys = @(
  @{ Scope = 'HKCU'; Path = 'HKCU:\Software\Khronos\OpenXR\1' },
  @{ Scope = 'HKLM'; Path = 'HKLM:\SOFTWARE\Khronos\OpenXR\1' }
)

$found = $null
foreach ($key in $keys) {
  try {
    $runtime = (Get-ItemProperty -Path $key.Path -Name ActiveRuntime -ErrorAction Stop).ActiveRuntime
    if ($runtime) {
      $found = @{ Scope = $key.Scope; Runtime = $runtime }
      break
    }
  } catch [System.Management.Automation.ItemNotFoundException] {
    Write-Host "$($key.Scope) OpenXR key not present; checking next scope."
  } catch {
    if ($_.FullyQualifiedErrorId -like '*PathNotFound*') {
      Write-Host "$($key.Scope) OpenXR key not present; checking next scope."
    } else {
      throw
    }
  }
}

if (-not $found) { throw 'No OpenXR ActiveRuntime value was found in HKCU or HKLM.' }

Write-Host "ActiveRuntimeSource=$($found.Scope)"
Write-Host "ActiveRuntime=$($found.Runtime)"
if ($found.Runtime -notmatch 'VDXR|VirtualDesktop|virtualdesktop') {
  throw 'The discovered OpenXR ActiveRuntime does not appear to be VDXR.'
}
$json = Get-Content -Raw -LiteralPath $found.Runtime | ConvertFrom-Json
Write-Host "Runtime name=$($json.runtime.name)"
Write-Host "Runtime library=$($json.runtime.library_path)"
