# Brief and delivery notes

Use these as compact working notes, not a questionnaire or another job schema. Keep them outside job JSON: use only the existing job schema's supported keys and let the pipeline generate its manifest. Fill from the conversation first; leave out irrelevant fields. Only ask about a missing item when the answer materially changes the result.

## Brief

```text
Visual: mood, palette/reference, forms, and areas that should remain quiet for icons.
Motion: pace/amplitude, desired loop feel, elements to avoid.
Display/use: selected screen/aspect and framing; personal or commercial/distributed use.
Delivery: selected profile, duration, app/bundle destination, relevant power policy.
Behavior/runtime: fixed video, data-selected variants, or continuous input; optional runtime dependencies.
Data: provider/endpoint, needed fields and units, refresh/fallback; credential reference only.
Known decisions: choices already supplied by the user or current project plan.
Assumptions: proposed defaults for delegated choices; any genuinely unresolved constraint.
```

Example when the user says “make a calm dusk wallpaper; choose the rest”: retain dusk and calm motion, propose a gentle abstract gradient with limited contrast, and use the project's initial 720p24/20-second profile. Do not ask the user to choose a seed, encoder preset, or every operator. Check a missing usage/license constraint before depending on a license entitlement; continue source/job work meanwhile.

## Handoff or completion

```text
Outcome: usable in the app / candidate asset awaiting a named check / source work only.
Artwork: one sentence describing the result and accepted assumptions.
Reproduce: job, dedicated template/source version, attempt and completed bundle paths.
Evidence: real TD build/license capability, chart/index checks, frame/encode checks,
          repeat comparison, observed loops/color, app launch/playback in the declared mode,
          and actual data/input integration where requested.
Resources: measurement/report link and applicable scope, or “not yet measured”.
Use: exact existing app import/open action and pause/quit action, if implemented.
Remaining: specific missing capability or failed check, owner/next runnable action.
```

Link existing manifests, reports, and guides rather than copying their contents. Label every test as actually run, not run, or inconclusive. Identify synthetic fixtures explicitly. Never fill a missing TD/app result with a test-double result or mark a proposed resource budget as measured.
