# Shared project instructions

## Goal and current stage

Deliver a macOS wallpaper app with TD-independent default playback, on-demand agent API creation, editable TD templates, and data/interactive effects, plus a shared authoring skill. Preserve offline generation as the first low-resource mode; optional live backends declare dependencies. Read `README.md`, `plan/README.md`, `plan/agent-platform.md`, and relevant performance evidence before substantive work. Memory, CPU, GPU, and power behavior remain acceptance criteria.

For wallpaper requirements, authoring, or export tasks, read the project [td-wallpaper skill](skills/td-wallpaper/SKILL.md). Its canonical source lives in this repository; it does not imply global installation or an available TD connection. Reuse known user choices and ask only for missing inputs that materially change the requested wallpaper.

`research/benchmarks/WallpaperProbe.swift` is a measurement probe; the separate native application is in `app/`. Read `app/README.md` for actual app capabilities and `agents_shared/README.md` for authoritative live status, current assignments, and review dispositions. Other entry points link there instead of repeating approval states. The user delegated review decisions and requested continued implementation. The original scoped M3 handoff remains in `plan/claude-implementation-prompt.md`; do not treat it as the only current scope or repeat superseded permission questions.

## Before implementation

- Read the latest relevant reports in `agents_shared/` and the milestone in `plan/README.md`.
- Review scope, dependencies, assumptions, resource budgets, and failure/recovery paths before editing product code. Record important findings and their disposition.
- Keep completed observations, hypotheses, proposed budgets, and unrun tests distinct. A low player CPU number is not evidence of low total GPU, helper memory, or power use.
- Honor the selected playback profile and architecture decision. Avoid adding private APIs, web runtimes, persistent system changes, or real-time TD work without a task-specific reason and review.
- The expanded user scope authorizes design and staged implementation of agent/data/live capabilities; it does not make any backend tested or installed. Preserve current file ownership. Use explicit new contracts rather than inserting arbitrary fields into strict video jobs. Keep model credentials, data credentials and public asset packages separate; read `.env.example`, not unrelated secret files.
- Keep TD cooking/rendering and video encoding separate in time from controlled playback measurements. Inspect the current TD project before edits; never alter an unrelated or unsaved project. Complete independent disk work when a connection or project switch is unavailable.
- Preserve unrelated user changes. Coordinate file ownership with other agents; do not overwrite another agent's work.

## Milestone reporting

Write an append-only report in `agents_shared/` at meaningful checkpoints: plan review, architecture decision, milestone completion, significant experiment, material change of approach, unresolved blocker, or handoff. Do not create a report for every small tool action.

Use `[agent_name][YYYY-MM-DD_HH-mm-ss±HHMM].md` with literal brackets. Use the user's `America/Los_Angeles` timezone unless they change it; include the UTC offset to disambiguate daylight-saving transitions. This assistant's agent name is `gpt6astra`; other agents use their own identity and must not claim another model's review.

Each report should state the checkpoint, scope, files/artifacts changed, decisions and reasons, checks actually run, results and limitations, review findings/disposition, open questions, and next actions. Include relevant commit IDs or artifact hashes when available. Read prior reports before continuing, and create a new report to supersede a completed one rather than silently rewriting its history. See `agents_shared/README.md`.

## Evidence and publication

- Put new raw runs in ignored `research/benchmarks/results_private/`. Publish reviewed copies under `results/` using the sanitization tool and a provenance note.
- Preserve measured numeric values. Normalize identifying paths, process IDs, and exact wall-clock metadata in public copies; do not fabricate measurements or credentials.
- Keep CPU scale, RSS, physical footprint, system GPU counters, and attribution limitations explicit.
- Exclude binaries, caches, generated movies, credentials, and private originals from public commits. Check the staged file list before pushing.
- Use `gh` for GitHub operations, as requested by the user. Do not use Chrome for repository administration.
- Cite primary documentation and pinned source commits for technical claims. Record version-dependent or unverified behavior.

The user's explicit instructions in the conversation take precedence over these repository conventions.
