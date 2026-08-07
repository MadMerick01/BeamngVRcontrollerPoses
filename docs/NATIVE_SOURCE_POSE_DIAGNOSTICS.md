# BeamNG-native source-pose diagnostic

This experiment is diagnostic-only. BeamNG's installed
`lua/ge/extensions/render/openxr.lua` contains a disabled `TODO` helper that
copies the position and quaternion returned by `OpenXR.getSourcePoseStates`.
The mod calls that function and `OpenXR.getInputSourceStates` as provided; it
does not edit BeamNG, replace either function, or make these poses the
production controller source. The existing blue controllers remain the
API-layer result.

The provisional yellow position is exactly:

```text
captured GE Lua anchor position + raw BeamNG native source position
```

There is deliberately no axis mapping, rotation, HMD baseline or delta,
rebase, gain, or smoothing. Grip, aim, and unknown pose paths are classified
only by explicit path text. All paths and their semantics must be confirmed
in live VR before this source can be considered production-ready.

## Headset test

Install the artifact, open the GE Lua console, and run:

```lua
extensions.load('beamngVRControllerPoses')
extensions.beamngVRControllerPoses.startGeluaCameraAnchorCapture()
extensions.beamngVRControllerPoses.startNativeSourcePoseDiagnostics()
dump(extensions.beamngVRControllerPoses.getNativeSourcePoseDiagnosticState())
```

Enter VR, hold both controllers still, move each controller independently,
translate and rotate the headset, recenter, then use stick translation and
rotation separately and together. Compare the yellow candidates with the blue
controllers, exit VR, and save `beamng.log`. Record visible pose paths; whether
grip or aim matches; responses to physical, stick, combined, and recenter
motion; stability; and when blue and yellow separate.

Stop the experiment with:

```lua
extensions.beamngVRControllerPoses.stopNativeSourcePoseDiagnostics()
```

## Interpreting observations

- If yellow matches controllers through all motion, the native source is a
  likely production candidate, subject to further headset evidence.
- If yellow matches physical motion but ignores stick motion, it remains in
  tracking-local space and needs the hidden stick transform.
- If yellow is near the map origin, it needs a world parent.
- If yellow moves on the wrong axis, it needs a documented basis conversion.
- If aim matches but grip does not, investigate aim selection or controller
  model offsets.
- If candidates overlap, record every path and later select using explicit
  semantics.
