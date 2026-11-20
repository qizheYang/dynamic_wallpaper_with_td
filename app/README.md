# Wallpaper Studio — local macOS preview

A native menu-bar application with a local video library, on-demand TD creation,
BYO model proposals, and optional weather/custom-data scenes. Ordinary video
playback does not launch TouchDesigner, Python, FFmpeg, a model request or a web
runtime. This is a development preview; the full release/performance gates and
Wallpaper Engine feature parity are not implied by a working local build.

## Build and open

Requires macOS and a Swift toolchain supporting Swift 5.9 packages. The package
targets macOS 13; actual compatibility is limited to machines/OS versions listed
in the latest [checkpoint](../agents_shared/README.md). No external Swift packages
are downloaded.

```sh
python3 app/build_app.py
open 'app/build/Wallpaper Studio.app'
```

To include an already validated local example:

```sh
python3 app/build_app.py --demo-bundle renders/<job>/bundles/<attempt>
```

The resulting `.app` is ad-hoc signed for local development, not notarized or a
public distribution build. The optional example retains its original TD usage
conditions; including it is not a commercial license or redistribution grant.

Close the control window to leave wallpaper playback and the menu bar available.
Use the wave menu icon to reopen controls, pause/resume, stop, import or quit.
Quitting removes this app's windows/resources. It does not change Apple's saved
desktop image, login items, screen-saver configuration or sleep settings.

## Use a completed wallpaper

1. Choose **导入素材包** and select a completed pipeline bundle directory containing
   `manifest.json`, `loop.mp4`, `cover.png`, and `frames.sha256.txt`.
2. Import checks the declared profile, completion checks and actual file hashes,
   and copies the content into the app-owned library. Source files may then move.
3. Choose **应用到桌面**. The player remains muted and click-through, with desktop
   icons above it. Screen selection currently activates one screen at a time.
4. Pause keeps a static presentation; a long pause releases the media queue after
   30 seconds. Resume is allowed only when all independent pause reasons clear.

The default policy is static on battery/UPS, Low Power Mode, serious/critical
thermal conditions, display sleep, inactive/unknown sessions, and complete
occlusion. Creation adds its own temporary pause reason. A missing display or bad
video releases the movie pipeline and keeps a cover where possible. Session and
occlusion signals are not a verified dedicated lock detector.

The library and settings live under `~/Library/Application Support/Wallpaper Studio`.
Credentials live in Keychain, separately from ordinary settings and assets.
Settings and the last selected asset survive relaunch; first launch starts with
no applied wallpaper. An optional bundled demo is imported for selection.

## Create and edit

The **制作壁纸** page offers a local `soft_gradient` template without a model key.
It currently supports a seed and 1–8 motion cycles, fixed dusk colors, 1280×720,
24 fps, 480 frames. Generation prepares a fresh job, runs a dedicated TD process,
finalizes the existing validated video pipeline, and imports the completed bundle.
Applying a result is a separate action. Cancellation stops owned work and cannot
publish an incomplete package.

Cancellation first allows the worker to clean up after SIGTERM, then escalates
to SIGKILL after 15 seconds. Completion waits for confirmed process exit; an OS
process stuck in an uninterruptible kernel wait has no guaranteed cleanup time.
Quit waits for active work and refuses to complete after 60 seconds if cleanup
is still pending, preserving ownership instead of treating that work as finished.

Configure installed Python, FFmpeg and ffprobe executable paths in settings.
TouchDesigner is needed only for creation/editing, currently at
`/Applications/TouchDesigner.app`. The calibrated template requires TD 2025.33230
and the reviewed output configuration; installation/license checks remain real
preconditions. Ordinary playback has none of these authoring dependencies.

The app ships a hashed `Authoring` resource kit. The first explicit creation/edit
action copies it to a writable, versioned app-owned workspace. No checkout or
globally installed skill is needed for default use. A repository override is
available for development; generation logs/frames remain in that workspace's
`renders/` for inspection.

**创建副本并在 TD 编辑** creates a unique editable project copy. It refuses to open
another project while TD is already running, preserving unrelated sessions. An
edited/new shader does not inherit the original calibration: follow
[the TD calibration procedure](../td/calibration/README.md) before production
export. The current Generate button uses the shipped calibrated template.

## Connect a model

Configure an Anthropic Messages endpoint or an OpenAI-compatible Chat Completions
endpoint, its exact model identifier and your API key. The app does not reuse a
Claude Code/Codex login. Model API cost belongs to your chosen provider/account.
Endpoint support is tested with protocol fixtures; actual provider compatibility
must be exercised with that provider/model and an explicitly supplied key.

The app sends the user's brief and a bounded, shipped context consisting of the
project skill, fixed job, shader and calibration profile. It requests a strictly
validated proposal: clarification questions, supported gradient parameters, or
an explicit new-template requirement. It never executes returned shell/Python
code. The app is not yet an unrestricted autonomous TD-network authoring agent.

API keys are origin-bound Keychain entries. Cross-origin credential redirects,
plain HTTP, URL-embedded credentials and oversized responses are rejected.
Prompts, ordinary settings and asset packages never contain the key. The optional
**读取所选 .env** action reads only a user-selected file as literal `KEY=value`
data; it never executes a shell or expands variables. See [.env.example](.env.example).

## Weather and custom data

**洛杉矶 · 天气天空** uses Open-Meteo's current *model-derived* conditions for Los
Angeles, with explicit Celsius, m/s, mm and `America/Los_Angeles`. No location
permission, model call or TD launch is involved. It refreshes about every 15
minutes, retains recent good data on transient failure, marks stale conditions
and uses a static offline scene after expiry. Sleep/session inactivity cancels
network work; wake rechecks freshness. Attribution: [Open-Meteo](https://open-meteo.com/)
and its [API documentation](https://open-meteo.com/en/docs). Check provider terms
before commercial distribution or changing the usage profile.

The optional native scene uses CoreAnimation gradients/clouds/precipitation.
Animation permission and data freshness are separate; battery/power/session
rules remain in force. The requested 24 fps preference is an OS hint, not a
measured hard GPU cap. This backend does not execute `.toe`/`.tox` files.

Custom data accepts an explicit HTTPS JSON endpoint and optional Bearer token.
Map numeric fields into bounded properties and/or `#RRGGBB` color strings:

```json
{
  "cloud": {"path": ["current", "cloud_cover"], "inputMin": 0, "inputMax": 100, "outputMin": 0, "outputMax": 1},
  "wind": {"path": ["current", "wind_speed"], "inputMin": 0, "inputMax": 30, "outputMin": 0, "outputMax": 1},
  "colorPath": ["theme", "color"]
}
```

Supported numeric destinations are `daylight`, `cloud`, `wind`, `intensity`.
Mappings are field-name arrays, not code, JavaScript or expressions. Payloads are
capped at 256 KiB. The initial custom connector supports HTTPS JSON + optional
Bearer auth, not arbitrary OAuth protocols or WebSocket plugins.

## Validation

On an Xcode-equipped machine: `swift test --package-path app`.
On this CLT-only setup, XCTest is absent; the checked-in portable runner executes
the **same test method bodies**, with a small explicit assertion compatibility
layer and per-test setup/teardown:

```sh
python3 app/run_checks.py
python3 app/run_studio_checks.py
python3 -m unittest discover -s tests -v
```

It is not XCTest. Unsupported tests/assertion constructs cause failure instead
of silently disappearing. Tests include no live model calls or TD sessions;
real app/TD/provider/weather experiments are recorded separately.

The additional Studio harness uses the real coordinator/desktop bodies without
opening windows. It covers busy import admission, queued display callbacks after
stop, and the explicit diagnostic override's bounds. It does not simulate an
actual display removal, lock or sleep transition.

Bounded product smoke, using an isolated data directory and a new event log:

```sh
'app/build/Wallpaper Studio.app/Contents/MacOS/WallpaperStudio' \
  --smoke-test renders/<job>/bundles/<attempt> \
  --data-dir app/build/local-smoke --event-log app/build/local-smoke.jsonl \
  --duration 90 --no-window
```

Normal battery policy can keep this entire test static. The explicit
`--diagnostic-battery-playback` switch is restricted to video smoke tests of at
most 120 seconds; it removes only the battery pause for that process, records
the real observed reason, and is never persisted. It does not prove normal
power-policy acceptance or a sustained resource pass. `--weather-smoke` exercises
the data/native scene instead. Diagnostics sampling exists only when requested.

Remaining release evidence includes real lock/unlock, sleep/wake, external-display
removal, repeated controlled system baselines, helper/GPU attribution, long-run
memory, actual provider trials and cross-machine coverage. Fullscreen/Spaces and
desktop-icon behavior require actual UI observation, not pure policy tests.

## Source map

- `Sources/WallpaperCore/`: validated library, player/policy/lifecycle, Keychain,
  model proposals, creation worker, weather/custom API and native scene.
- `Sources/WallpaperStudio/`: controls, menu bar, preferences, coordination,
  desktop scene host and opt-in product diagnostics.
- `build_app.py`: reproducible local bundle and hashed authoring-kit assembly.
- `run_checks.py`, `TestSupport/`, `Tests/`: CLT runner and standard XCTest sources.

Broader live renderers, system-audio/mouse interaction, playlists, simultaneous
multi-screen animation, distribution/signing, and Steam Workshop interoperability
remain separate work. Track their actual state in [shared status](../agents_shared/README.md),
not as a completed claim of Wallpaper Engine parity.

Reviewed process-only results from the two initial 90-second smoke runs are in
[`evidence/preview-smoke.json`](evidence/preview-smoke.json). Their raw logs remain
private/ignored. The first used the normal battery policy; the second explicitly
overrode only that reason for the bounded diagnostic. These runs establish basic
play/pause/release behavior, not a whole-system resource pass.
