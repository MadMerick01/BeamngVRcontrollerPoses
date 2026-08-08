# PR #40 camera-context rebase

## Accepted PR #39 headset result

The dark-blue candidate was confirmed correct for recenter alignment, natural
physical head translation, natural head rotation, stick-controlled movement,
stick-controlled rotation, and combined continuous movement.

The remaining observed fault was that changing BeamNG camera context (for
example, vehicle to on-foot) retained the old camera attachment and displaced
dark blue; one VR recenter restored alignment. This evidence isolates the fault
to camera-context lifecycle handling, so PR #39's continuous pose calculation
is intentionally unchanged.

## Context identity and cut detection

The stable context key is `active camera mode|object=<controlled object
ID>|level=<level/mission identity>`. Its inputs are the repository API-dump
entries `core_camera.getActiveCamName()`, `getPlayerVehicle(0)` (with the
returned object's ID accessor), and `getCurrentLevelIdentifier()`. All calls
are optional and protected with `pcall`; an unavailable field is represented
literally as `unavailable`, rather than by a guessed API.

A changed key schedules one atomic rebase at the next frame with valid orange
and captured game-anchor poses. A captured game-anchor step beyond the existing
5 metre or 120 degree validated discontinuity thresholds is treated as a camera
cut. Ordinary physical HMD motion never enters either detector, while normal
stick motion continues through the unchanged PR #38/#39 delta calculation.

The rebase is internal: it sets the artificial attachment to identity, seeds
the previous anchor from the current anchor, and publishes complete orange as
dark blue on that frame. It never calls `OpenXR.centerNow()`.

## Controller parent

In `baselineRigidRebasedArtificialCamera`, valid dark blue is the single parent
used by both existing controller compositions. If dark blue is unavailable or
awaiting re-establishment, the complete orange pose is selected instead. Violet
remains the parent only when its explicit mode is selected.
