# Render jobs (schema version 1)

A job describes one deterministic render. It is validated strictly by [`pipeline/job.py`](../pipeline/job.py): unknown keys, wrong types, odd dimensions, unsupported color contracts and unsafe paths are rejected before anything is rendered.

| Field | Meaning |
| --- | --- |
| `schema_version` | Must be `1`. |
| `job_id` | `[a-z0-9][a-z0-9_-]{0,63}`. |
| `template` | `{id, version, path?}`. `path` is a project-relative `.toe`/`.tox` under `td/`. Jobs whose `id` starts with `synthetic` are for the synthetic stand-in renderer only. |
| `seed` | Integer `0 … 2³¹−1`. |
| `width`, `height` | Even integers (yuv420p). TD Non-Commercial renders at most 1280 px per side; the TD controller enforces this when the license is Non-Commercial or unknown. |
| `fps`, `frame_count` | Integers. Duration is derived: `frame_count / fps`. Frame `i` (0-based) renders phase `i / frame_count`. |
| `parameters` | Template parameters (JSON scalars). The TD controller rejects keys its template does not support. |
| `source` | `{format: "png", bit_depth: 8 or 16, color}`. `color` is a contract the renderer must already satisfy, not a conversion request. |
| `encode` | Optional. `{codec: "libx264", preset, crf}`; defaults `medium`, `18`. |
| `validation` | Optional. `seam_ratio_max` (default 3.0) and `roundtrip_mean_abs_max` (default 3.0, in 8-bit code values). |
| `output_dir` | Project-relative directory under `renders/` (ignored by Git). Each run creates a new `attempts/<attempt_id>/` inside it. |

## Supported color contracts

| Contract | The PNG values are… | Encoding |
| --- | --- | --- |
| `rgb_rec709_bt709trc_full` | full-range RGB, Rec.709 primaries, already encoded with the BT.709 transfer function | BT.709 matrix, limited range, all four color tags `bt709`/`tv` |

No sRGB or linear contract is accepted yet. The `soft_gradient` template encodes its output with the BT.709 OETF itself. Whether TouchDesigner writes those values unchanged must be confirmed with the chart job before a real export is trusted (see [td/README.md](../td/README.md)).

## Jobs in this directory

| Job | Purpose |
| --- | --- |
| `gradient_seed42_720p24.json` | The first real TD job: 1280×720, 24 fps, 480 frames (20 s), seed 42. |
| `soft_gradient_chart_check.json` | TD verification: known code values, to check TD's output contract. |
| `soft_gradient_index_check_720p24.json` | TD verification: binary frame index in every frame, to check cook timing and orientation. |
| `synthetic_smoke_720p24.json` | Disk pipeline smoke test with the synthetic renderer. Not a TD export. |
