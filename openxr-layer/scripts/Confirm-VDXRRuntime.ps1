$key = 'HKCU:\Software\Khronos\OpenXR\1'
$runtime = (Get-ItemProperty -Path $key -Name ActiveRuntime -ErrorAction Stop).ActiveRuntime
Write-Host "ActiveRuntime=$runtime"
if ($runtime -notmatch 'VDXR|VirtualDesktop') {
  throw 'The current-user OpenXR ActiveRuntime does not appear to be VDXR.'
}
$json = Get-Content -Raw -LiteralPath $runtime | ConvertFrom-Json
Write-Host "Runtime name=$($json.runtime.name)"
Write-Host "Runtime library=$($json.runtime.library_path)"
