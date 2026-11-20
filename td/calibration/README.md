# Reviewed PNG metadata correction

This directory authorizes one narrow correction: `soft_gradient` v1, the pinned
`td/wallpaper_template.toe` and embedded/disk shader, TouchDesigner **2025.33230**,
1280×720 RGBA8 output, and the recorded two-frame cook delay. It does not approve
arbitrary TD projects, resolutions, color spaces, future builds, or display color.

- `soft_gradient-v1-td2025.33230.json` pins the source, live network/output identity,
  correction, and evidence hash. The controller reads actual live DAT text and
  connected TOPs; matching a job name or PNG tag is insufficient.
- `soft_gradient-v1-evidence.json` records the original November 18 captures,
  attempt/report hashes, and rerun numeric chart checks. This is an inventory of
  existing evidence, not a new TD calibration or a claim of AVPlayer validation.
- `raw-chart.png` and `corrected-chart.png` are exact original frame-zero files
  from attempts `20261118T021418Z-fd71a9` and `20261118T021835Z-102417`. Both contain
  the same compressed pixel payload. All eight bars match their expected code
  values. Loading the profile verifies evidence/sample hashes and that the
  approved metadata-only rewrite reproduces the corrected file byte for byte.

The source shader encodes artwork with its explicit BT.709 OETF. Chart bars are
already encoded test values. Their unchanged samples justify replacing this
writer's contradictory `sRGB`/`gAMA` declarations with `cICP 1/1/0/1` for this
configuration. A chart alone cannot establish that an unknown shader has applied
the right transfer function. This correction never performs a transfer conversion.

Preflight checks run before export ownership/writes. Live identity and file hashes
are checked again immediately before every synchronous PNG save and before the
final render report. Unknown or changed provenance stops the export; reports
include the profile, evidence hashes, and checked live identity. The existing
per-frame PNG-chunk check remains an additional guard.

## New templates or TD builds

An unknown configuration cannot use the production export to grant itself
calibration. In a dedicated scratch project, first use the raw `probe-color`
workflow in [`../README.md`](../README.md) to retain uncorrected samples and the
actual build/shader/output facts. For a new template, its calibration harness must
render known encoded chart values through that template's actual output path;
the existing gradient probe is not evidence for a different network. Review the
source's transfer/alpha contract, raw pixel values and writer tags, then add or
revise an explicit profile with those evidence hashes. There is no force/approval
flag. This is an engineering verification step, not repeated user permission.
After a reviewed profile exists, run the production chart, index and repeated
artwork checks described in the TD guide; playback color/loop review is separate.

The live-inspection guard was confirmed by a fresh 24-frame chart export on
TD 2025.33230 on 2026-11-19 (America/Los_Angeles); all eight bars matched exactly.
The first run exposed that `ParMode` is not a member of the imported `td` module;
it refused before writing frames and exited cleanly. The adapter now compares
against `EXPRESSION` on the returned mode object's type, verified by the retry
and a regression test. Unsupported APIs still fail closed. API references:
[OP identity/wiring/flags](https://derivative.ca/UserGuide/OP_Class),
[TOP pixelFormatName](https://derivative.ca/UserGuide/TOP_Class),
[Par evaluation and modes](https://derivative.ca/UserGuide/Par_Class),
[GLSL Multi shader bindings](https://derivative.ca/UserGuide/GLSL_Multi_TOP).
