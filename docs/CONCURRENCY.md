# OpenXR layer concurrency and ownership

## Pre-change audit (merge of PR #3)

The implementation had one project mutex, the global `mu`, plus the internal
locks used by the C++ runtime, Winsock and stdio. Every lifecycle interceptor,
the unknown-function branch of `xrGetInstanceProcAddr`, `xrSyncActions`, and
`xrLocateSpace` acquired `mu`. There were no shared mutexes or condition
variables. The publisher did not acquire `mu`.

The old locate sequence was: acquire `mu`; look up `spaces`; follow
`Space.session` through `sessions` to `dispatches`; call BeamNG's downstream
`xrLocateSpace`; read the action-space hand, session VIEW space, and dispatch;
call downstream `xrLocateSpace` again for VIEW; calculate the relative pose;
read/merge/write the global sample double buffer; release `mu`; return the first
downstream result unchanged. Consequently both downstream locate calls occurred
under the global mapping lock. The lifecycle hooks also called downstream create,
destroy, string/path, sync, and action functions while holding it. Instance
destruction could even join the publisher while holding it.

The mapping reads reachable from locate were `spaces[space]`,
`sessions[space.session]`, `dispatches[session.instance]`, and the selected
session VIEW handle. Their writers were action/reference-space creation and
destruction, action destruction (which cleared hand metadata), session creation
and destruction, path discovery, and instance creation/destruction. Actions were
owned by an action set/instance; action spaces by a session/action; sessions by an
instance. The selected VIEW handle was whichever application VIEW space had most
recently been created, so an application destroy could invalidate the assumption.

No log, socket send, or file operation ran directly in locate. Pose publication
only copied fixed-size objects. The publisher thread separately formatted JSON,
sent UDP, opened its five-second log, and slept. Snapshot/reference counting was
not present. The ordinary double buffer was also not a rigorous MPSC structure if
a runtime invoked locate concurrently.

## Current immutable snapshot model

The new implementation has two project mutexes, neither reachable from locate.
Lifecycle hooks are writers. They update `registry` under `writerMutex`, make a
complete immutable `Registry`, and release-publish a `shared_ptr` through the
C++20 atomic shared-pointer specialization. Container copies, string copies,
reference-count control-block allocation, and snapshot destruction therefore
occur off the locate path. An atomic load in locate increments/decrements a
control-block reference count but does not allocate. It is not replaced with a
raw pointer.

Locate atomically loads one snapshot and reads its action-space, session,
dispatch, hand, lifetime, and layer VIEW metadata. It increments the session's
atomic in-flight count, verifies `active` again, then performs the original
downstream locate. A qualifying hand performs the VIEW locate with the identical
`baseSpace` and `XrTime`, validates both position and orientation, does the same
relative-pose math, and attempts a fixed-capacity MPSC queue write. It returns the
application locate result unchanged. There is no mutex, wait, allocation,
logging, formatting, socket operation, file operation, or blocking I/O.

Session destruction marks the lifetime inactive and publishes a snapshot without
the session/spaces while holding only the writer lock. After releasing the lock,
it waits for the already admitted atomic in-flight count to reach zero, destroys
the layer VIEW through that session's captured downstream dispatch, and finally
destroys the session. A late reader either fails admission or is included in the
count. Its snapshot keeps C++ metadata alive, and the counter keeps native handles
alive. No downstream call occurs under a project lock.

Each successful session creation attempts to create an identity-offset VIEW
reference space through that session's downstream dispatch. Failure is optional
and leaves a null handle, so application session creation still succeeds. The
space belongs to the same session, is never inserted in the application's space
map or returned to BeamNG, is only located against the controller's base/time,
and is destroyed with the same captured dispatch. Application VIEW-space
lifetime is no longer an input.

`publisherMutex` only serializes publisher thread start/stop; start/stop are
called without `writerMutex`. The background publisher alone performs UDP, formatting, sleeping and file
logging. Its bounded sequence queue is allocation-free and producers drop rather
than wait when full. An absent UDP receiver remains harmless. Shutdown joins the
publisher only after releasing `writerMutex`, eliminating lock inversion.

## Remaining validation

The automated Windows job is the authoritative clean MSVC x64 build, PE/export,
manifest/package, hash, and Python-test check. This is not VDXR or Quest 3 runtime
validation. The first in-headset procedure, controller axes/scale, recentering,
stereo diagnostic spheres, and orderly shutdown still require the documented
manual run.
