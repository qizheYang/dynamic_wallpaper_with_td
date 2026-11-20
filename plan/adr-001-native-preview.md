# ADR 001: Native application implementation, with explicit release gates

Date: 2026-11-19. Implementation choice: AppKit desktop host, AVQueuePlayer / AVPlayerLooper / AVPlayerLayer for video, SwiftUI controls, event-driven lifecycle policy. This authorizes an engineering preview; it does not declare M1/M2 resource or lock acceptance complete.

The user asked to continue the complete application. Keep non-privileged application engineering moving while performance and real system lifecycle validation remain explicit gates. This refines the earlier strictly sequential M2→M4 workflow: a local preview may be built and exercised before a release-quality resource decision, with no public performance claim or binary release implied.

## Candidate screen and choice

The pinned [existing-player source review](../research/04-existing-apps-performance.md) is the comparison evidence. LiveDesk demonstrates native local-video playback but its reviewed pause policy lacks the required independent power/session/thermal states. Mural has a small native video path, but the reviewed version has lifecycle/recovery gaps and retains players on pause. Aerial adds private wallpaper APIs and a more complex reader/extension lifecycle. None is accepted unchanged as this application's host. We have not benchmarked these applications, so this is a maintainability/behavior decision, not a performance ranking.

Implement the required small public-API host ourselves; reuse the established AVFoundation mechanism, not copied third-party source. Keep generation, model requests, data polling and optional live effects separate and unloadable. Basic video launch uses no TD, Python, FFmpeg, model endpoint, web view, private extension or network request.

## Concrete implementation increment

- A local `.app` with bundle import, app-owned library, static cover, start/pause/stop, menu controls, screen selection and relaunch persistence.
- Independent power, thermal, display/session and visibility reasons; long-pause media release and stale-load protection. Actual lock behavior is unverified until an actual lock/unlock trial passes; session inactivity is not renamed lock detection.
- On-demand BYO model configuration, origin-bound Keychain credentials, bounded structured authoring proposals using the shipped skill; explicit generation through the existing fixed calibrated TD template. Unsupported art directions require a new template instead of silently substituting a gradient.
- Editable TD project copies on explicit request. Preserve source templates and unrelated TD sessions.
- Typed weather/custom-data inputs and a constrained native weather scene as an optional backend; this is not a generic `.toe` runtime or full Wallpaper Engine compatibility.

## Validation and release boundaries

Run policy/import/provider/data tests, actual app launch/import/play/pause/release/relaunch, actual TD generation through the creation worker, and a bounded public weather integration. Measure the product process and available system counters separately from compilation/rendering/encoding. Real lock/sleep/display-removal tests require real events; mocks do not pass those gates. A missing model credential prevents a real provider trial, but does not justify asking for or inspecting unrelated secrets.

The first build is a local development preview. Signing/notarization, paid provider authorization, full live-renderer parity, multi-screen animation, audio capture, Workshop compatibility and cross-machine acceptance remain separately tracked until implemented and verified. Do not label pending work complete merely because the local preview works.
