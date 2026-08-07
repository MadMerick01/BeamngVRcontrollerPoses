# PR #32: `setGeluaCameraPosRot` anchor investigation

This PR is diagnostic only. It does not add an operational translation mode,
change controller placement, draw tracking-local data in BeamNG world space, or
modify the OpenXR layer/protocol. Capture is explicit opt-in and is **not**
installed when the extension loads.

## Static evidence and decision

Selective searches covered both supplied main API dump representations, the
113,169,906-byte `openxr_dump.txt` member, and all repository sources/docs. The
main dumps expose these two members of the global `OpenXR` table:

* `getCameraPosRotPredictedXYZXYZW`
* `setGeluaCameraPosRot`

That is the full setter evidence. The dumps provide no signature, traced setter
call, GE Lua call site, native implementation, units, quaternion ordering or
transform direction. The OpenXR trace has no occurrence of the setter. Repository
source has no call site. In particular, there is no evidence that BeamNG resolves
`OpenXR.setGeluaCameraPosRot` dynamically from the global Lua table each frame,
rather than retaining/calling a native binding internally.

Consequently none of the following is proven for the setter:

| Question | Evidence-based result |
|---|---|
| Argument count/order | Unknown |
| Position units | Unknown |
| Quaternion ordering | Unknown |
| Camera-to-world vs world-to-camera | Unknown |
| Called from GE Lua every frame | Not proven |
| Dynamic global-table lookup | Not proven |
| Safely wrappable by an extension | No, on current evidence |

The `XYZXYZW` suffix proves an ordering only for the separately named predicted
**getter**; it cannot be transferred to the setter. The setter name alone is not
treated as semantic evidence, so no seven-scalar interpretation is exposed as
valid and no fabricated values are ever passed to it.

A trial assignment is not a safe runtime test: it proves table mutability but not
that the camera path dynamically looks up that member, and a real camera call can
race between assignment and restoration. Therefore this PR neither invokes nor
replaces the setter. The narrowest future observation point is a proven BeamNG GE
Lua call site whose inputs can be recorded immediately before its setter call (or
an official getter for the GE Lua camera base). A native BeamNG-to-OpenXR camera
boundary separate from `xrLocateSpace` is the fallback if no Lua call site exists.

## Console API and returned state

```lua
extensions.beamngVRControllerPoses.startGeluaCameraAnchorCapture()
extensions.beamngVRControllerPoses.stopGeluaCameraAnchorCapture()
extensions.beamngVRControllerPoses.getGeluaCameraAnchorCaptureState()
```

With the supplied evidence, `startGeluaCameraAnchorCapture()` returns `false`
plus a clear reason. `stopGeluaCameraAnchorCapture()` is idempotent. Unload also
calls stop. Since no wrapper is ever installed, the original global member is
always untouched; state reports `wrapperInstalled=false`,
`originalFunctionPreserved=true`, `captureAvailable=false`, counters/raw fields,
passthrough fields, and the failure reason. Raw and interpreted fields are kept
separate. Interpreted transforms and comparison deltas remain `nil`, rather than
inventing an argument format. No candidate sphere/tripod is drawn because world
position semantics are not evidenced.

## Headset procedure once dynamic-call evidence exists

Do not expect step 4 to return true in this revision. Preserve these exact
commands/procedure for a future evidence-backed implementation:

1. Launch BeamNG with the existing OpenXR layer.
2. Run `extensions.load('beamngVRControllerPoses')`.
3. Run:

   ```lua
   extensions.beamngVRControllerPoses.setHmdTranslationMode(
     'baselineRigidPositionBeamngRotationRebased'
   )
   ```

4. Run
   `extensions.beamngVRControllerPoses.startGeluaCameraAnchorCapture()` and
   continue only if it returns `true`.
5. In VR: remain still five seconds; lean right/left/forward and return; move
   up/down; naturally turn; walk forward/backward; strafe; apply stick yaw and
   repeat a short walk; optionally move a vehicle and change camera mode; recenter
   once.
6. Exit VR and run:

   ```lua
   extensions.beamngVRControllerPoses.stopGeluaCameraAnchorCapture()
   dump(extensions.beamngVRControllerPoses.getGeluaCameraAnchorCaptureState())
   ```

7. Save `beamng.log`.

## Acceptance signature

| Action | Required candidate response |
|---|---|
| Physical lean/crouch | Position remains unchanged |
| Natural physical head yaw | Orientation remains unchanged |
| Stick walking/strafing | Position moves |
| Stick artificial yaw | Orientation rotates |
| Vehicle/game-camera movement | Pose follows vehicle/camera |
| VR recenter | Continuous or predictably re-established |

Reject the candidate if it follows physical HMD movement, stays near the OpenXR
origin, matches the predicted tracking-local pose, is not called in active VR, or
cannot be observed without changing camera behaviour. Live headset evidence plus
a dump/source showing the actual call boundary is still required before a safe
wrapper, interpretation, comparison sampling, throttled call-rate logs, or world
visuals can be implemented.
