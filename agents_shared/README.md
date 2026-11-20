# Shared agent reports

This directory is the persistent handoff log for the project. Read the latest relevant reports before work and review the intended milestone in [the plan](../plan/README.md).

## Naming

Use literal square brackets:

```text
[agent_name][YYYY-MM-DD_HH-mm-ss±HHMM].md
[gpt6astra][2026-11-10_18-45-00-0800].md
[claude][2026-11-10_19-10-00-0800].md
```

The examples illustrate the format; they do not claim that those reports or reviews exist. `gpt6astra` is the identity requested for this assistant. Other agents use their own identity. Dates and times use `America/Los_Angeles`; the offset handles daylight-saving changes and makes handoffs unambiguous.

## When to write

Use judgment to report after an initial plan review, architecture decision, meaningful experiment, milestone completion, significant scope change, blocker, or handoff. Routine file reads and small edits do not each need a report. Finalized reports are append-only: add a later report to correct or supersede one.

## Report structure

1. **Checkpoint and status:** what stage has been reached.
2. **Scope and artifacts:** files changed and relevant commits/hashes.
3. **Decisions:** what was chosen and why.
4. **Verification:** commands/checks actually run, outcomes, and meaningful limitations.
5. **Review:** findings, severity, disposition, and review still pending.
6. **Open items and next actions:** what another agent needs to continue.

Reports may use English or Chinese, but should remain concise and understandable without the preceding chat. Keep secrets and personal environment details out of this public directory. Link to repository-relative evidence instead.

## Current review state

Updated 2026-11-19. The native **Wallpaper Studio local preview** is now implemented alongside the pipeline, research and skill. See [build/use instructions](../app/README.md) and [ADR 001](../plan/adr-001-native-preview.md). The initial gpt6astra review and Claude's 2026-11-13 review are complete; [the disposition](%5Bgpt6astra%5D%5B2026-11-17_15-59-58-0800%5D.md) addresses all nine findings and four policy questions. The user subsequently authorized continued application implementation.

Latest handoff: [Native application, creation and data preview](%5Bgpt6astra%5D%5B2026-11-19_22-03-30-0800%5D.md), including actual app/TD runs, the successful weather retry and the retained offline result.

**Review decisions are complete; implementation and performance checks are not complete.** The former “awaiting user decisions” checkpoint is superseded. Accepted/modified findings now have implementation or validation actions in the plan; no full low-resource pass has been declared.

Final user goals: **a Mac app with TD-independent default playback, in-app agent APIs, editable TD templates and API-driven/interactive effects**, plus **a shared authoring skill**. Video export remains the first path; optional live modes declare dependencies. The user chose Wallpaper Engine **feature equivalence first, Workshop compatibility separately**. The [platform extension](../plan/agent-platform.md) tracks M7–M10; the implemented subset below does not complete the entire matrix.

| Track | Current state / next owner |
| --- | --- |
| M3 asset pipeline | **Initial implementation `claude`; hardening owner `gpt6astra`. F1–F5 fixed and verified.** [Latest checkpoint](%5Bgpt6astra%5D%5B2026-11-19_19-28-17-0800%5D.md): 120 tests pass; real TD chart and 480-frame export pass with the calibrated live-identity guard. Actual frames/video match the previous validated export; no TD process remains. Finite deadlines, owned-child cancellation, result/exit validation and calibration provenance are enforced. Open: AVPlayer color/loop observation and whole-system resource gates. |
| M1 measurement / lock feasibility | **Owner `gpt6astra`. Measurement foundations implemented:** bounded sustained mode, shared monotonic timestamps, process identity checks, non-root footprint sampling, and schema-2 summaries. **90 tests passed at the M1 checkpoint**; Claude's subsequent TD report records 93 tests. A 30-second native smoke completed; sanitized evidence and limitations are in [the checkpoint report](%5Bgpt6astra%5D%5B2026-11-17_17-53-36-0800%5D.md). Baseline qualification, sampler overhead, helper attribution, sustained measurements, and actual lock/sleep remain open. |
| M2 playback decision | **Native engineering preview selected in ADR 001**, based on pinned source screening and the expanded product requirements. M1/M2 resource and lock gates still block release acceptance; no existing-player benchmark ranking claimed. |
| M4 application | **Owner `gpt6astra`. Local native app built and exercised.** App-owned verified library, video/cover, pause/release, menu controls, selected screen and persistence. Normal battery 90-second smoke stayed unloaded/static; explicit bounded diagnostic 90-second run advanced/looped, released during long pause and resumed, with no errors. [Process-only evidence](../app/evidence/preview-smoke.json). Actual GUI app-triggered TD export produced and imported the same validated MP4 hash, then TD exited. Full lock/sleep/Spaces/display and sustained resource acceptance remain open. |
| M6 agent skill | **Owner `gpt6astra`.** Canonical [skill](../skills/td-wallpaper/SKILL.md) now maps the actual app, pipeline and supported data/model paths; included in a hashed app authoring kit. Official format validation passes. Existing read-only request trials remain recorded; app generation is exercised, but independent fresh Claude/Codex briefs through novel-template creation and real app playback still need acceptance. No global installation claimed. |
| Resource acceptance | Whole-system memory/GPU, sustained stability, and actual lock/sleep remain unverified. Missing data stays inconclusive. |
| M7 creation/connections | **Preview subset implemented.** Anthropic/OpenAI-compatible adapters, bounded skill-context proposals, origin-bound Keychain profiles, selected literal `.env`, serial cancellable local generation and editable TD copies. Real app generation/import passed. Model transport/credential behavior uses fixtures; no actual paid model call or real Keychain round trip claimed. Unsupported styles explicitly require a new template. |
| M8 data / M9 native scene | **Preview subset implemented.** LA Open-Meteo connector, timestamp/units/freshness/backoff policy, bounded custom HTTPS JSON mapping, native gradients/clouds/precipitation with TD closed. One actual public weather fetch passed. Custom connection retry/cache deadlines survive pause/resume; renderer resource acceptance remains open. TD weather video variants are not generated yet. |
| M9–M10 broader coverage | System audio, mouse interaction, generic Web/TouchEngine renderers, simultaneous multi-screen animation, playlists/schedules, Workshop and full Wallpaper Engine parity remain unimplemented. No public notarized release or cross-machine acceptance. |
| Color correction | **F4 closed for the pinned 720p configuration.** [Calibration profile and retained evidence](../td/calibration/README.md) bind correction to TD 2025.33230, exact source/template and live output identity. A fresh real chart passed after API verification; unknown/modified configurations fail closed. Actual AVPlayer color appearance remains unverified. |

Settled defaults: one 720p24 H.264 profile first; static on battery/Low Power Mode; optional user-started system samplers only, never a privileged playback/TD process. No unrelated TD project edits. Start with ordinary disk code when TD is unavailable.

Latest automated checks: **120 Python tests, 32 portable Swift core checks and 3 focused Studio lifecycle checks passed**. The CLT installation lacks XCTest; `app/run_checks.py` runs the same test method bodies with an explicit assertion adapter, and is not an XCTest runtime result. The Studio checks verify busy import admission, callbacks queued before scene stop and bounded diagnostic options without creating windows. Real UI/TD, process smoke and public-weather checks are separate observations. Local app binaries, private logs, generated frames and movies remain ignored.

Agent identities: `gpt6astra`; `claude` (Claude Code, which has reported TD MCP access in its own environment). Each agent rechecks actual connection and project state before use. This section is the single live review/assignment status; historical reports are append-only.

## Reports

- [Plan review and publication preparation](%5Bgpt6astra%5D%5B2026-11-10_18-44-12-0800%5D.md)
- [Public repository verification and handoff](%5Bgpt6astra%5D%5B2026-11-13_17-53-56-0800%5D.md)
- [Independent Claude plan review](%5Bclaude%5D%5B2026-11-13_18-02-02-0800%5D.md)
- [Review disposition and Claude coding handoff](%5Bgpt6astra%5D%5B2026-11-17_15-59-58-0800%5D.md)
- [M3 disk pipeline delivered; TD integration waiting](%5Bclaude%5D%5B2026-11-17_17-01-16-0800%5D.md)
- [M1 measurement foundations and M3 regression fixes](%5Bgpt6astra%5D%5B2026-11-17_17-53-36-0800%5D.md)
- [Final product goals and shared agent skill draft](%5Bgpt6astra%5D%5B2026-11-17_18-01-32-0800%5D.md)
- [M3 real TouchDesigner exports validated](%5Bclaude%5D%5B2026-11-17_18-23-55-0800%5D.md)
- [Agent/API platform scope, video export and TouchEngine cost](%5Bgpt6astra%5D%5B2026-11-17_18-25-12-0800%5D.md)
- [Independent review of Claude's real TD export results](%5Bgpt6astra%5D%5B2026-11-17_18-38-08-0800%5D.md)
- [M3 runner/calibration hardening and real TD regression](%5Bgpt6astra%5D%5B2026-11-19_19-28-17-0800%5D.md)
- [Native application, app-owned TD generation, weather and regression checks](%5Bgpt6astra%5D%5B2026-11-19_22-03-30-0800%5D.md)
