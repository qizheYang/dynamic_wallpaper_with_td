# Claude implementation entry point

Read [AGENTS.md](AGENTS.md), [the current shared status](agents_shared/README.md#current-review-state), the latest review disposition, and [the implementation plan](plan/README.md).

The final outputs are an app with TD-independent default playback and agent/template/data-driven creation, plus a shared authoring skill. Read [the platform extension](plan/agent-platform.md) for the later live-renderer and API scope, and [the canonical td-wallpaper skill](skills/td-wallpaper/SKILL.md) for authoring. Reuse settled requirements; clarify material gaps and verify the selected runtime. New scope does not cancel the current M3 assignment or allow overwriting another agent's work.

The scoped coding assignment is [M3: minimal TD video production](plan/claude-implementation-prompt.md). The user delegated review decisions and requested this implementation handoff. Follow the prompt, implement the independent disk pipeline first, then integrate the dedicated TD project when available. Preserve uncommitted work and do not repeat superseded approval questions.

Use your own agent identity for timestamped reports. Record actual tests, TD availability, and remaining checks honestly; successful asset production does not establish whole-system playback performance. Keep live status in `agents_shared/README.md`.
