# Schema-2 measurement smoke: provenance and limitations

This is a reviewed public copy of the local `results_private/m1-2026-11-17` batch. It contains a one-shot sampler capability check and one 30-second native probe smoke. It is **not a controlled resource-acceptance batch**. See the [checkpoint report](../../../../agents_shared/%5Bgpt6astra%5D%5B2026-11-17_17-53-36-0800%5D.md) and [benchmark guide](../../README.md).

## Recorded experiment

- Schedule: `idle:4,play:8,cover:4,uncover:6,pause:4,idle:4`; explicit cover and pause-on-occlusion enabled.
- System sampling requested every 2 seconds, footprint every 10 seconds; analysis excludes 1 second at each phase boundary.
- Fixture: synthetic 1280×720, 24 fps, 480-frame/20-second H.264 bundle from the M3 disk pipeline. It is not a TD export. SHA-256: `beb17016cc013582f81ecd4b3dd5c542fd8dad44638fe3d2cf6c56ef57308b4b`.
- Probe binary SHA-256: `43b9c35d5a53ed2558ebe0f4500e23335165d4ee14c4643756e7c5586768f9c5`. Python source hashes are in `smoke/metadata.json`.
- Probe exited with code 0 after 30.075 seconds and logged `schedule_complete`. The machine reported battery power. This probe exercises measurements; it does not implement the product's static-on-battery policy.
- Playback advanced and paused/resumed, but ordinary desktop activity occluded it at about 7.81 seconds, before the scheduled cover at about 12 seconds. The retained play phase has 2 visible samples out of 5; uncover has 3 out of 3. This is not continuous full-visibility evidence, and the cover action did not cause the first observed pause.
- No completed video loop or actual lock, session transition, or display-sleep event was observed in this smoke.
- Three footprint snapshots contain 80 identity-checked numeric rows in the raw records. **No footprint snapshot fits wholly inside a retained phase window** at this cadence and schedule, so all phase footprint results remain missing/inconclusive. The WindowServer memory gate is not covered.
- Fifteen sampler calls took approximately 0.288 seconds mean wall time and 1.118 seconds maximum. This is invocation latency, not sampler CPU cost or an overhead calibration.

The capability snapshot recorded one GPU device, 25 selected processes and 25 footprint rows, with no sampler errors. `top MEM` is rounded physical-footprint accounting, separate from `ps` RSS. Decoder processes and global GPU counters are not attributed to the player. An empty activity watchlist is not proof that background work was absent.

## Transformation and integrity

`sanitize_results.py` copied only JSON/JSONL into a new directory. Private originals and original logs are unchanged. The exact project root became `$PROJECT_ROOT`; the exact fixture bundle directory became `$FIXTURE_DIR`, removing its timestamped directory name. Within each run directory, PIDs (including `probe_pid`) map to stable small integers and process `start_time` strings map to `anonymous-start-N`. Distinct start times remain distinct, preserving PID-reuse checks. Capability and smoke identities are separate groups and must not be joined across files in different run directories.

Exact Unix wall-clock fields are omitted. Monotonic/elapsed durations, phase boundaries, counters, samples, errors, numeric measurements, and hashes are preserved. Monotonic values describe a boot-relative clock, not wall-clock dates. No screenshots, full process arguments, credentials, host serial numbers, or original home paths are included.

Recomputing the smoke summary from the private and public records produced identical results. A separate read-only review checked the phase windows and reported metrics against the raw records. All acceptance remains **inconclusive** pending the repeated controlled protocol, attribution, sampler calibration, and lifecycle tests.

To repeat the transformation, point `--fixture-root` at the exact local bundle directory containing `loop.mp4`, and use a new output directory. Review every output before committing; the sanitizer removes known fields/prefixes and is not a general secret scanner.
