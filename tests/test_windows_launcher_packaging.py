from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CMD = (ROOT / "Launch-BeamNGVRControllerPoses.cmd").read_text()
PS1 = (ROOT / "scripts" / "Launch-BeamNGVRControllerPoses.ps1").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "windows-x64.yml").read_text()


def test_cmd_uses_own_directory_spaces_and_process_only_bypass():
    assert "%~dp0" in CMD
    assert '"%SCRIPT%"' in CMD
    assert "-ExecutionPolicy Bypass" in CMD
    assert "Set-ExecutionPolicy" not in CMD
    assert "exit /b %EXITCODE%" in CMD
    assert "pause >nul" in CMD


def test_package_validation_cases_are_covered():
    assert "Missing API-layer manifest" in PS1
    assert "Missing API-layer DLL" in PS1
    assert "Manifest library_path resolves" in PS1
    assert "[IO.Path]::GetFullPath((Join-Path $PackageRoot $libraryPath))" in PS1


def test_hkcu_missing_falls_back_to_hklm_and_vdxr_warning():
    assert "HKCU:\\Software\\Khronos\\OpenXR\\1" in PS1
    assert "HKLM:\\SOFTWARE\\Khronos\\OpenXR\\1" in PS1
    assert "catch {}" in PS1
    assert "virtualdesktop|vdxr" in PS1
    assert "will not change it automatically" in PS1


def test_launcher_path_selection_and_rejection_rules():
    assert "%LOCALAPPDATA%" not in PS1  # uses env object, not package-root config
    assert "$env:LOCALAPPDATA" in PS1
    assert "launcher.json" in PS1
    assert "Read-SavedLauncherPath" in PS1
    assert "Find-BeamNGLauncherAutomatically" in PS1
    assert "Select-BeamNGLauncherWithDialog" in PS1
    assert "-ResetLauncherPath" in PS1 or "ResetLauncherPath" in PS1
    assert "BeamNG.drive.exe" in PS1
    assert "Bin64" in PS1
    assert "BeamNG.drive.x64.exe" in PS1


def test_process_scoped_environment_and_no_persistence():
    assert "$env:XR_API_LAYER_PATH = $PackageRoot" in PS1
    assert "$env:XR_ENABLE_API_LAYERS = $LayerName" in PS1
    assert "$env:XR_LOADER_DEBUG = 'all'" in PS1
    forbidden = ["SetEnvironmentVariable", "ActiveRuntime =", "New-ItemProperty", "Set-ItemProperty"]
    for token in forbidden:
        assert token not in PS1


def test_logging_and_shortcut_scripts_present():
    assert "BeamNG-OpenXR-loader.log" in PS1
    assert "BeamNGVRPosesLayer.log" in PS1
    assert "BeamNGVRPosesLauncher.log" in PS1
    assert (ROOT / "scripts" / "Install-DesktopShortcut.ps1").exists()
    assert (ROOT / "scripts" / "Remove-DesktopShortcut.ps1").exists()


def test_windows_artifact_includes_launcher_files():
    for required in [
        "Launch-BeamNGVRControllerPoses.cmd",
        "scripts\\Launch-BeamNGVRControllerPoses.ps1",
        "scripts\\Install-DesktopShortcut.ps1",
        "scripts\\Remove-DesktopShortcut.ps1",
        "scripts\\Enable-BeamNGVRPoses.ps1",
        "mod\\lua\\ge\\extensions\\beamngVRControllerPoses.lua",
        "mod\\settings\\beamngVRControllerPoses.json",
    ]:
        assert required in WORKFLOW
    assert "Copy-Item openxr-layer/scripts\\* (Join-Path $package scripts) -Recurse" in WORKFLOW
    assert "Copy-Item scripts\\* (Join-Path $package scripts) -Recurse" in WORKFLOW
