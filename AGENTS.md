# Shared project instructions

## Goal and current stage

Build a low-overhead macOS dynamic-wallpaper workflow using offline TouchDesigner generation and measured native playback. Memory, CPU, GPU, and power behavior are acceptance criteria. Read `README.md`, `plan/README.md`, and `research/05-performance-feasibility.md` before substantive work.

This checkpoint prepares a public research repository and a reviewed implementation plan. The existing Swift program is a measurement probe. Product implementation is not part of the current publication request. Claude's independent review is pending until a real review is recorded; follow the user's next implementation instruction without inventing repeated permission gates.

## Before implementation

- Read the latest relevant reports in `agents_shared/` and the milestone in `plan/README.md`.
- Review scope, dependencies, assumptions, resource budgets, and failure/recovery paths before editing product code. Record important findings and their disposition.
- Keep completed observations, hypotheses, proposed budgets, and unrun tests distinct. A low player CPU number is not evidence of low total GPU, helper memory, or power use.
- Honor the selected playback profile and architecture decision. Avoid adding private APIs, web runtimes, persistent system changes, or real-time TD work without a task-specific reason and review.
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
