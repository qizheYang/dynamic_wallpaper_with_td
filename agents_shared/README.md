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

Updated 2026-11-17. The public research repository and four short playback pilots are available. The initial gpt6astra review and Claude's 2026-11-13 review are complete. The user delegated the response and requested a Claude coding handoff; [the gpt6astra disposition](%5Bgpt6astra%5D%5B2026-11-17_15-59-58-0800%5D.md) addresses all nine findings and all four policy questions.

**Review decisions are complete; implementation and performance checks are not complete.** The former “awaiting user decisions” checkpoint is superseded. Accepted/modified findings now have implementation or validation actions in the plan; no full low-resource pass has been declared.

Final user goals: **a Mac app with TD-independent default playback, in-app agent APIs, editable TD templates and API-driven/interactive effects**, plus **a shared authoring skill**. Video export remains the first path; optional live modes declare dependencies. The user chose Wallpaper Engine **feature equivalence first, Workshop compatibility separately**. The [platform extension](../plan/agent-platform.md) adds M7–M10 without claiming those capabilities are implemented.

| Track | Current state / next owner |
| --- | --- |
| M3 asset pipeline | **Owner `claude`. Real TD exports validated** ([report](%5Bclaude%5D%5B2026-11-17_18-23-55-0800%5D.md)). TD 2025.33230 Non-Commercial, driven without MCP by `td/run_td.py`. Generated `td/wallpaper_template.toe` (no TDMCP). Chart check exact, index check 480/480 upright. Two `gradient_seed42_720p24` exports: all bundle checks pass, byte-identical frames and `loop.mp4`. TD's constant sRGB/gAMA PNG tags are replaced with `cICP` 1/1/0/1 under a guard; **awaiting gpt6astra review**. Open: AVPlayer color/loop review by eye. |
| M1 measurement / lock feasibility | **Owner `gpt6astra`. Measurement foundations implemented:** bounded sustained mode, shared monotonic timestamps, process identity checks, non-root footprint sampling, and schema-2 summaries. **90 tests passed at the M1 checkpoint**; Claude's subsequent TD report records 93 tests. A 30-second native smoke completed; sanitized evidence and limitations are in [the checkpoint report](%5Bgpt6astra%5D%5B2026-11-17_17-53-36-0800%5D.md). Baseline qualification, sampler overhead, helper attribution, sustained measurements, and actual lock/sleep remain open. |
| M2 playback decision | Still depends on controlled evidence and candidate screening. No existing-player performance ranking or final architecture chosen. |
| M4 application | Not implemented/selected yet. A probe run is not a usable-app delivery; final launch and playback must work without TD or its automation connection. |
| M6 agent skill | **Owner `gpt6astra`. Draft delivered:** project-local [skill](../skills/td-wallpaper/SKILL.md) with requirements and handoff guidance, linked from both agent entry points. Official format validation and two independent read-only request scenarios passed; see [the skill checkpoint](%5Bgpt6astra%5D%5B2026-11-17_18-01-32-0800%5D.md). Real Claude/Codex TD-export and app-playback trials remain open; no global installation or end-to-end completion claimed. |
| Resource acceptance | Whole-system memory/GPU, sustained stability, and actual lock/sleep remain unverified. Missing data stays inconclusive. |
| M7–M10 expanded platform | **Design owner `gpt6astra`.** Feature research, renderer/provider/data separation, editable-template workflow, weather `.env.example`, and updated skill completed; read-only weather scenario and format checks passed. App coordinator, connectors, loader, live backends and full parity remain unimplemented/unverified. See [scope checkpoint](%5Bgpt6astra%5D%5B2026-11-17_18-25-12-0800%5D.md). |
| Color correction | Small FFmpeg synthetic checks reproduced the matrix/tag issue. PNG validation now rejects known metadata that conflicts with the BT.709 source contract; untagged inputs still require renderer evidence. TD transfer and AVPlayer visual checks remain open. |

Settled defaults: one 720p24 H.264 profile first; static on battery/Low Power Mode; optional user-started system samplers only, never a privileged playback/TD process. No unrelated TD project edits. Start with ordinary disk code when TD is unavailable.

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
