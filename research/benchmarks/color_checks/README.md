# Color-command review check — 2026-11-17

This small FFmpeg check independently reproduced Claude review finding #4. It is not a TD export, AVPlayer visual check, or performance benchmark.

## Method and observations

Using FFmpeg 9.0.2/libx264, encode a 64×64 solid red, full-range 8-bit RGB PNG without ICC/gAMA/cICP metadata. Compare the original command, output color flags alone, Claude's filter, and an explicit matrix/range/filter chain. Decode one frame as `yuv420p`, read Y/U/V planes, inspect ffprobe fields, and inspect the H.264 signal description with `trace_headers`.

| Path | Decoded red Y/U/V | ffprobe color fields |
| --- | --- | --- |
| Original command | 81 / 90 / 240 | All four absent |
| Output color flags alone | 63 / 102 / 240 | Matrix/range present; primaries/transfer absent |
| Claude filter | 63 / 102 / 240 | All four present |
| Explicit candidate | 63 / 102 / 240 | All four present |

The explicit candidate is:

```text
scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv
```

The test asserts that input sample values already have Rec.709 primaries **and BT.709 transfer encoding**. The filter sets matrix/range and metadata; it does not establish or convert the original transfer function. Do not apply it indiscriminately to sRGB, linear RGB, or ACES. See the official [scale](https://ffmpeg.org/ffmpeg-filters.html#scale) and [setparams](https://ffmpeg.org/ffmpeg-filters.html#setparams) documentation.

A six-block chart also produced black `16/128/128`, white `235/128/128`, red `63/102/240`, green `173/42/26`, blue `32/240/118`, and RGB-128 gray `126/128/128`. The candidate's H.264 headers reported limited range and primaries/transfer/matrix identifiers of 1; the original had no video signal description.

## Evidence and reproduction

[2026-11-17.json](2026-11-17.json) retains the actual encode, decode, probe and header-inspection argument arrays, observed values, dimensions, hashes, and tool version. Recreate the stated RGB inputs and substitute `$COLOR_CHECK_DIR` with a new local scratch directory. Executables are represented as `ffmpeg` and `ffprobe` so the commands do not contain the author's installation path. Encode commands refuse overwrite.

The original JSON was preserved under ignored `research/benchmarks/results_private/color-check-2026-11-17/`. Public normalization replaces executable/scratch paths and nonsemantic trace logger addresses only; numeric observations and hashes are unchanged. No generated images or movies are committed here. The source images were synthetic; this check makes no claim about third-party media.

M3 must still establish TD's actual color contract and compare a real exported chart in TD and AVPlayerLayer. The original command's observed behavior on this FFmpeg build does not establish how AVPlayer interprets untagged HD media, nor guarantee defaults on other builds.
