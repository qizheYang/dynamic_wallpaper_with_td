---
name: td-wallpaper
description: Create or revise wallpapers in dynamic_wallpaper_with_td, from requirements and editable TouchDesigner templates through validated video export, supported weather/API scenes, and Wallpaper Studio playback. Use for this repository's authoring workflow and in-app agent integration, not general TouchDesigner tutoring.
---

# TD wallpaper production

Produce artwork matching the user's intended behavior. Default video playback works with TouchDesigner closed; optional live modes declare their actual runtime/license dependencies. TD and FFmpeg are production tools for the video path. Treat usable playback, live interaction and measured low resource use as separate claims.

## Find the project and its actual capabilities

This skill is versioned at `<repository>/skills/td-wallpaper`. Resolve the checkout from this location or the user's existing workspace; do not hard-code a personal path. If using a copied skill, locate the checkout before resolving the repository links below. Read the repository instructions and preserve existing work and active agent ownership.

| Read | Purpose |
| --- | --- |
| [README](../../README.md), [AGENTS](../../AGENTS.md), [shared status](../../agents_shared/README.md) | Product boundaries, current capabilities/owners, latest relevant checkpoint. Claude also reads [CLAUDE.md](../../CLAUDE.md). |
| [Plan](../../plan/README.md), [performance findings](../../research/05-performance-feasibility.md) | Scope, defaults, acceptance criteria, and evidence limitations. |
| [Platform extension](../../plan/agent-platform.md), [live/API reference](references/interactive-and-api.md) | Read for model-provider integration, weather/custom APIs, manual template editing, live behavior or Wallpaper Engine feature requests. |
| [Jobs](../../jobs/README.md) and `pipeline/job.py` | Current schema and examples; avoid copying a second schema into this skill. |
| [TD guide](../../td/README.md) | Dedicated project, shader/controller, actual build/export procedure and diagnostic jobs. |
| [Pipeline guide](../../pipeline/README.md) | Implemented commands, validation, cancellation, publication, and bundle contents. |
| [Application guide](../../app/README.md), `app/Sources/WallpaperCore/`, `app/Sources/WallpaperStudio/` | Local app build/import, native playback, scoped model proposals, creation worker, Keychain, weather/custom-data scenes and current preview limitations. |
| [Benchmark guide](../../research/benchmarks/README.md) | Controlled measurements, only when requested or needed for acceptance. |

Confirm what exists before promising delivery. Source files, an existing `.toe`, and fake-adapter tests do not establish a validated export or an exercised adapter. `research/benchmarks/WallpaperProbe.swift` is a temporary measurement probe, not the product application. Read live status and relevant current artifacts to determine which host, renderer, connector and export steps are implemented; do not invent commands or present design examples as working features. Synthetic exports establish disk-tooling behavior only.

## Turn intent into a small brief

Reuse the user's references, prior answers, chosen profile, and existing authorization. Infer what is already clear, then ask only unresolved questions that materially change the artwork or delivery: visual direction/motion, intended display/framing, or personal versus commercial/distributed use. An open question need not block independent disk work. Do not repeat approval questions at every stage.

Use [the brief and handoff template](references/brief-and-handoff.md) when helpful. If the user delegates choices, state reasonable assumptions and proceed: a calm, slowly moving abstract visual; one screen; 1280×720, 24 fps, 20 seconds, 480 frames, seed 42, SDR H.264 without audio. These are starting defaults, not reasons to override an explicit brief. Adapt composition/cropping to the intended screen without automatically increasing rendering resolution. Preserve the selected power policy; default battery/Low Power Mode behavior is static unless the current plan or user says otherwise.

## Choose the delivery path

Use a video export for predetermined animation. Wallpaper Studio's optional native weather scene can respond to LA conditions and bounded custom JSON properties without TD/model calls during refresh. For a request specifically requiring TD-authored weather variants, the multi-clip mapping path still needs implementation; an MP4 alone cannot fetch data. If input continuously changes particles, geometry, audio response or mouse effects, follow the live/API reference and choose an actually supported renderer. Do not flatten an explicit interactive requirement into a video and call it satisfied. Preserve editable TD sources separately from playback assets.

The app's current model service reads this shipped skill and scoped template context, then returns clarification questions, fixed-template parameters, or a new-template requirement. It does not execute model-produced code or autonomously build arbitrary networks. A coding agent with repository/TD tools may implement a new template, but must complete its source/output calibration and real export before advertising it as supported. Keep these capabilities accurate when responding to a user's brief.

The user chose feature equivalence as the primary Wallpaper Engine goal; direct Steam Workshop compatibility is a separate evaluation. A request for comparable effects does not imply permission or capability to copy/convert third-party Workshop content.

## Make a video asset

1. **Brief → job.** Adapt a suitable versioned job under `jobs/`, using the schema and supported parameters in the current code. Inspect required tools/builds and the TD license type/capabilities without reading keys. Check whether the intended use is permitted as well as the output resolution; external encoding does not remove TD license restrictions. Validate the job and prepare a new attempt using the documented pipeline commands. Reuse neither a failed attempt nor a published output directory.

2. **Job → dedicated TD template.** Inspect the actual TD connection and open project before editing. Preserve unrelated or unsaved projects; if switching would displace one, finish independent disk work and request that concrete project switch. Work in the repository's dedicated template, with external shader/controller sources and a small verified network. When available, load `td-general` first, then relevant planning, working-mode, operator-family, review and cleanup skills. Those skills are optional support, not a requirement to have a particular MCP server. Use available tools or documented TD procedures; consult installed-build help/official Derivative documentation for unfamiliar APIs rather than inventing operators, parameters, or tool calls. Honor the user's authorized build scope without turning planning guidance into repeated approvals.

3. **Template → real numbered frames.** Drive every frame by `i/N`, for `i = 0…N−1`, with fixed seeds and reset initial state; omit the duplicate endpoint. Prefer stateless periodic artwork for the first version. Do not use wall time or `absTime` as the render clock. Follow the controller procedure in the TD guide, let the event loop progress, and verify actual cook/save completion. Preserve render failure/cancellation instead of manufacturing completion reports. Run the documented chart and index/orientation jobs before trusting a new or changed template/adapter, then the real art job. Record TD build, template/source identity, job, attempt, and actual checks.

4. **Frames → validated bundle.** Follow the pipeline guide for `finalize`; inspect the checks and published manifest, not just an output movie. The source contract is `rgb_rec709_bt709trc_full`: full-range RGB with Rec.709 primaries and values already encoded with the BT.709 transfer function. Tags do not convert linear or sRGB samples. Do not strip conflicting metadata to bypass validation; establish a correct source/conversion and rerun checks. Untagged acceptance is a renderer declaration, not proof of transfer correctness. For this opaque-video output, author an opaque final composition; the current encoder drops alpha. Check the static cover as well as the video. Repeat the real task in a fresh attempt and use the documented comparison; distinguish same-build reproducibility from a cross-machine bit-exact guarantee.

Use the actual command names and arguments from the linked guides and `python3 -m pipeline --help`. Do not substitute `render-synthetic` for the real TD step. Keep generated movies, frames, caches, and local raw measurements in the repository's ignored output locations.

## Put it on the desktop and verify

Find the product application's implemented import/open procedure in the current repository docs. Import only a complete validated bundle using that procedure. If the product host or selected data/live backend is absent, deliver the candidate and a precise integration handoff; do not claim a finished wallpaper or use the probe as a disguised replacement application.

For video/variant or runtime-free native/Web modes, save the dedicated TD project, finish encoding, and close TD for playback. Preserve unrelated projects or sessions; if closing TD would affect unsaved unrelated work, coordinate that specific action. Verify app launch/playback without opening TD or FFmpeg. Optional TouchEngine/external-live tests must instead record the declared runtime, license and cleanup. For video, observe several loop boundaries, framing, static cover, and color-chart appearance through the actual playback path. Establish the TD reference before closing it, and report any comparison still needing visual confirmation. Numerical checks alone cannot establish appearance or smooth playback.

Check the app's documented pause/resume, fallback and quit behavior within the requested scope. Do not claim automatic lock/sleep behavior from synthetic notifications or manual pause. Keep rendering/encoding separate from controlled playback measurements. Low bitrate, low app CPU, native APIs, or small files alone do not establish low total CPU/GPU/memory; use the plan's whole-system methodology and keep missing/noisy results inconclusive. If needed, produce a genuinely lower-fps/resolution rendition at the same intended speed, not merely a slower player rate.

If TD access, a safe project switch, a connector or the host is unavailable, complete useful source/validation work, identify the exact unavailable step, and hand off the runnable next action. Final delivery requires actual playback and the requested behavior in its declared mode; planned manifests or `.env.example` alone are not a working integration. Record evidence and remaining gaps using the repository's checkpoint convention; do not install this skill globally or change app/login/system settings as a side effect of using it.
