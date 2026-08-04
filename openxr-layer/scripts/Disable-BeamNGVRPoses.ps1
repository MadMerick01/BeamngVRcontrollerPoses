Remove-Item Env:XR_API_LAYER_PATH -ErrorAction SilentlyContinue
Remove-Item Env:XR_ENABLE_API_LAYERS -ErrorAction SilentlyContinue
Remove-Item Env:XR_LOADER_DEBUG -ErrorAction SilentlyContinue
Remove-Item Env:BEAMNG_VR_POSES_DISABLE -ErrorAction SilentlyContinue
Write-Host 'BeamNG VR poses layer disabled for this PowerShell session.'
