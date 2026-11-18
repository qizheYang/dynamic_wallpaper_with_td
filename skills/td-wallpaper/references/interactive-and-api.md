# Live effects, APIs and app-hosted agents

Read this for weather/custom API wallpapers, interactive effects, BYO model APIs, or the “open a TD template” workflow. The [platform plan](../../../plan/agent-platform.md) is the maintained architecture; [research/06](../../../research/06-interactive-agent-platform.md) records verified upstream capabilities. Check implementation and current ownership before running anything. The [weather example](../../../examples/weather-los-angeles/README.md) is a design/configuration proposal, not an implemented connector.

## Understand behavior before selecting a renderer

Recover the location, style, intended display/use and known preferences from context. Clarify only gaps that change delivery: periodic state changes versus continuous input response, the data endpoint/authentication the user wants, and whether a selected optional runtime is acceptable. Explain dependency/cost consequences in plain language; do not ask for keys in chat. A named city needs no device-location permission.

- Predetermined movement: existing video job/export path.
- Weather/daylight selects an appearance: authored video variants plus a native data broker can work with TD closed.
- Continuous cloud/wind/particle or audio/mouse effects: a supported native scene, Web or TouchEngine/live backend is required. Verify the actual supported property/input contract.
- TouchEngine can load `.tox` without an editor UI but requires installed runtime and suitable paid licensing. Do not describe it as runtime-free compilation. External TD/TouchPlayer mode also declares its running-process dependency.

Keep video defaults when they satisfy the brief; do not replace an explicit interactive request with an MP4. Do not promise an arbitrary TD network will convert to Metal/Web or that a Wallpaper Engine `.pkg` will become editable TD source. The user chose feature parity first and separate Workshop-compatibility evaluation.

## Keep three boundaries distinct

**Creation agent:** the host reads the pinned skill and relevant project sources, calls the chosen model provider and executes validated local tools. An API key is not an installed Claude/Codex client, and the model has no automatic access to local TD/files. If that coordinator/provider adapter is absent, say which implementation is missing. Never invent an app command to fill the gap.

**Data service:** the connector fetches/normalizes fields on a bounded cadence and publishes typed values. Ordinary weather updates do not call a model or trigger rendering/export again. Include units, source timestamps, TTL, backoff, stale/offline fallback and range validation. Share identical requests across screens; stop unnecessary work during sleep. User-defined APIs may require a real adapter for authentication/response shape.

**Visual runtime:** consume declared properties only. An API payload is data, not executable instructions; neither the renderer nor agent context receives secret keys. Interactive regions and click-through behavior must be tested alongside desktop icons. Audio-device input does not prove that system playback audio is available.

## Configuration and production

Use `.env.example` for nonsecret documentation and named credential placeholders. A local `.env` is only read when explicitly selected for the task; parse it as data and keep secret values out of model prompts, exported bundles and logs. The app's planned Keychain connection profiles associate each credential with its service. Agent and weather keys are separate. Do not strip an incompatible license/color/runtime declaration to make a validation step pass.

Keep brief/data/app-package descriptions outside the current strict video job JSON. Use only implemented schemas and commands; propose a versioned contract if capabilities are missing. Do not manually forge pipeline manifests or `complete` states. For manual TD authoring, duplicate a suitable template into the user's project, preserve original source and unrelated sessions, then export through the normal validation path.

## Verify the requested integration

For the LA weather slice, test recorded clear/rain/night/stale/malformed/rate-limited cases, then an actual supported provider request. Label recorded responses as fixtures, not current observations. Verify visual changes, freshness/fallback, no refresh-triggered model calls, and successful app relaunch with the declared dependencies. Then measure the active mode's entire resource footprint. A video-mode pass does not establish a Web/TouchEngine pass.

Handoff names the provider adapter, connector/schema version, renderer, dependencies, editable source, completed package, tests actually run, and missing checks. Secret values are never needed in that handoff. Do not claim the whole Wallpaper Engine feature set from a successful single weather example.
