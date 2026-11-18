# Asset pipeline

`python3 -m pipeline` turns a [job](../jobs/README.md) into a validated bundle: `loop.mp4`, `cover.png`, `frames.sha256.txt` and `manifest.json`. It uses only the Python standard library plus `ffmpeg`/`ffprobe` on `PATH`. The code is Python 3.11 compatible, so TouchDesigner's embedded Python can import it.

## Flow

```text
prepare  → attempts/<id>/ in state "rendering", with job.json, frames/, work/
renderer → frames/frame_000000.png … frame_{N-1}.png, then render.json   (TD controller or render-synthetic)
finalize → encoding: sequence check, ffmpeg
           validating: ffprobe, full decode, color round trip, loop seam
           publish: bundle.partial/ renamed to bundles/<id>/, state "complete"
```

States are `queued → preparing → rendering → encoding → validating → complete`; any stage can end in `failed`. Only `finalize` publishes, and only after every check passes. A failed, cancelled (Ctrl-C or SIGTERM) or interrupted attempt keeps its frames and logs for inspection and is never reused. Run the job again to get a new attempt.

On macOS/POSIX, state changes and the final rename/state update share a per-attempt advisory file lock. Cancellation recorded before this commit prevents publication; cancellation arriving after it reports an already-complete attempt. A handled commit exception restores the staging directory and prior state. SIGKILL, power loss, or an unsuccessful filesystem rollback require manual inspection; this is not a crash-safe transaction across two paths. Consumers must require both a complete attempt state and its bundle. The `cancel` command prevents publication but does not terminate an encoder already running; Ctrl-C/SIGTERM to the finalize process stops its encoder.

## Commands (from the repository root)

```sh
python3 -m pipeline validate-job jobs/synthetic_smoke_720p24.json
python3 -m pipeline prepare jobs/synthetic_smoke_720p24.json          # prints the attempt path
python3 -m pipeline render-synthetic renders/synthetic_smoke_720p24/attempts/<id>
python3 -m pipeline finalize renders/synthetic_smoke_720p24/attempts/<id>
python3 -m pipeline status   renders/synthetic_smoke_720p24/attempts/<id>
python3 -m pipeline compare  <attempt-a> <attempt-b>                  # repeatability
python3 -m pipeline cancel   <attempt>                                # mark failed; a running TD export stops
python3 -m pipeline verify-chart <attempt>                            # TD chart job: frame 0 code values
python3 -m pipeline verify-index <attempt>                            # TD index job: frame k carries index k
```

Exit codes: `0` success, `2` invalid input, `3` pipeline failure, `130` cancelled.

Expected `finalize` output for the synthetic smoke job: `loop_seam: "continuous"`, and a `not_verified` list that starts with `Not a TouchDesigner export (renderer: synthetic)`. On the development machine (FFmpeg 9.0.2), rendering took about 2 s and finalize about 2.5 s. Two attempts produced identical frames and identical `loop.mp4` hashes.

## What finalize checks

| Check | Fails when |
| --- | --- |
| Sequence | Missing or out-of-range frames, wrong names, bad PNG signature/CRC/zlib stream/size, wrong dimensions or bit depth, mixed PNG color types or color chunks, or unsupported/conflicting color metadata. |
| Encode | `ffmpeg` exits non-zero. The command is stored in the manifest with attempt-relative paths. |
| Probe | Not exactly one H.264 `yuv420p` stream at the job size; `r_frame_rate`/`avg_frame_rate` ≠ `fps/1`; decoded frame count ≠ N; duration off by more than half a frame; any color tag differs from the contract. |
| Decode | A full `-xerror` decode reports anything. |
| Round trip | Frames 0 and N/2, decoded back to RGB with the contract's matrix, differ from the source PNG by more than `roundtrip_mean_abs_max` on average. A BT.601/BT.709 mismatch gives about 9 at 720p; a correct encode gives about 0.7. |
| Loop seam | On a 96-px-wide RGB analysis copy: the last→first change exceeds both `seam_ratio_max` × median adjacent change and the largest adjacent change (`seam_discontinuity`), or the last frame duplicates the first (`duplicate_endpoint`). |

The seam check is a necessary condition, not proof of a smooth loop. A one-frame cook delay shifts every frame equally and still passes it, which is why TD renders are also checked with `verify-index`. Alpha in RGBA frames is dropped by the encoder; the manifest records `alpha_present`.

The only source contract requires full-range RGB samples already encoded with the BT.709 transfer function. Untagged PNGs are accepted as the renderer's declaration, still subject to the external TD/chart verification. Matching RGB `cICP` (1/1/0/1) and Rec.709/D65 `cHRM` are accepted. `sRGB`, `gAMA`, and unparsed ICC profiles (`iCCP`) are rejected, as are conflicting `cICP`/`cHRM` values. Convert and verify unsupported sources before exporting; simply removing their tags does not convert their samples. The RGB round-trip check cannot establish the source transfer function.

One renderer case is handled explicitly. TouchDesigner 2025.33230 writes the same `sRGB`/`gAMA`/Rec.709 `cHRM` chunks into every PNG, whatever its settings, and stores the samples unchanged. The TD controller replaces only those two wrong tags with `cICP` 1/1/0/1 via `png.redeclare_color()`, and only after it confirms that the chunks are exactly that known constant. The pixel data is copied byte for byte, and `render.json` records the change. This is a metadata correction. It does not convert anything: the template's samples are BT.709-OETF by construction, and the chart check confirms they pass through TD unchanged. See [the TD guide](../td/README.md#td-facts-this-depends-on-observed-on-202533230).

## Tests

```sh
python3 -m unittest discover -s tests -v
```

The tests cover the success path, missing, renamed, extra, corrupt, truncated and mis-sized frames, an unfinished render, encoder failure, cancellation during encoding (the encoder process must be gone) and validation, interprocess cancellation at publication, publication rollback, conflicting source metadata, finalize-twice, seam discontinuity, duplicated endpoint, a deliberate color-matrix mismatch, repeatability, controller stepping with a fake TD adapter, and the debug-render readers. They use tiny synthetic frames and say nothing about TouchDesigner itself.
