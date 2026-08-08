# VR node-grabber candidate selection

`extensions.vrNodeGrabber` is a world-space **candidate selector**, not a physical
grabber. It consumes the compact final-pose API owned by
`extensions.beamngVRControllerPoses`, searches active JBeam physics objects only
on a grip press (or an explicit/preview request), and retains the nearest node
inside the configured 20 cm radius. It applies no force and creates no link.

## Design and API

The pose extension remains the sole owner of OpenXR input and camera/controller
composition. `getControllerWorldPose('left'|'right')` supplies only validity,
age, final world position/orientation, and update counter. The node extension
does not reconstruct a camera transform and does not fetch the large diagnostic
state each frame.

For every active JBeam object, selection safely reads its ID, origin, node count,
and vehicle-relative node positions. Node world position is:

```text
vehicle:getPosition() + vec3(vehicle:getNodePosition(nodeId))
```

Distance comparisons remain squared until one winner is known. Objects without
nodes, invalid/inactive objects, non-finite coordinates, and the current player
vehicle (by default) are ignored. The selection stores IDs rather than a vehicle
reference and reacquires the object while validating it.

Public commands are:

```lua
extensions.vrNodeGrabber.getState()
extensions.vrNodeGrabber.getHandState('left')
extensions.vrNodeGrabber.findNearestNode('right')
extensions.vrNodeGrabber.clearCandidate('right')
extensions.vrNodeGrabber.setGripState('left', 0.0) -- temporary test boundary
extensions.vrNodeGrabber.setNearbyNodePreviewEnabled(false)
```

Grip values must be between 0 and 1. The default press/release thresholds are
0.65/0.35. This hysteresis issues one search on a rising press, performs no new
search while held, and clears the candidate on release. Invalid/stale poses,
despawn/replacement, reset, unreadable nodes, explicit clear, and unload also
clear the candidate.

Valid hands are orange. A selected candidate is a small yellow marker; the hand
stays orange because no attachment exists. Invalid hands are hidden. Legacy blue
controller spheres are suppressed while this extension is loaded by default and
can be restored with `legacyControllerSpheresVisible`. Nearby-node preview is
off by default; when enabled it refreshes at 10 Hz and caps each hand at 32
markers. It never enables BeamNG's global native node display.

## First headset test

1. Load and configure the proven PR #41 tracking mode:
   ```lua
   extensions.load('beamngVRControllerPoses')
   extensions.beamngVRControllerPoses.startGeluaCameraAnchorCapture()
   extensions.beamngVRControllerPoses.setHmdTranslationMode('baselineRigidRebasedArtificialCamera')
   extensions.load('vrNodeGrabber')
   dump(extensions.vrNodeGrabber.getState())
   ```
2. Put the right hand more than 20 cm from a vehicle or JBeam prop and run
   `extensions.vrNodeGrabber.setGripState('right', 1)`. Expect no candidate.
3. Release with `setGripState('right', 0)`, move within 20 cm of a JBeam node,
   and press again. The orange hand should remain orange, one yellow marker
   should identify the nearest node, and `getHandState('right')` should report
   its vehicle ID, node ID, and distance. Nothing should move.
4. Release and repeat with the left hand.
5. Change BeamNG cameras and confirm selection follows the correctly reattached
   controller poses.
6. Hold either grip and confirm there is no repeated search log or visible
   performance stutter.

This test establishes proximity identification only. Physical grabbing, force
APIs, mass limits, and one- or two-handed links are deliberately deferred.
