# Claude implementation entry point

Read [AGENTS.md](AGENTS.md), [the current shared status](agents_shared/README.md#current-review-state), the latest review disposition, and [the implementation plan](plan/README.md).

The final outputs are an app with TD-independent default playback and agent/template/data-driven creation, plus a shared authoring skill. Read [the platform extension](plan/agent-platform.md) for the later live-renderer and API scope, and [the canonical td-wallpaper skill](skills/td-wallpaper/SKILL.md) for authoring. Reuse settled requirements; clarify material gaps and verify the selected runtime. New scope does not cancel the current M3 assignment or allow overwriting another agent's work.

The original scoped assignment was [M3: minimal TD video production](plan/claude-implementation-prompt.md). The user subsequently authorized continued application implementation. Start from the native preview in [`app/`](app/README.md), its current report and [ADR 001](plan/adr-001-native-preview.md); preserve the working export/calibration path and any uncommitted work. Do not restart completed M3 work or repeat superseded approval questions. Performance/lifecycle acceptance and broader platform capabilities remain explicit follow-up work.

Use your own agent identity for timestamped reports. Record actual tests, TD availability, and remaining checks honestly; successful asset production does not establish whole-system playback performance. Keep live status in `agents_shared/README.md`.
