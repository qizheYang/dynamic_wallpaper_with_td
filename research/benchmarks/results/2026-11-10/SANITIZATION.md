# Public measurement provenance

These are public copies of four short local pilot runs collected on 2026-11-10. Publication processing changes identifying metadata, not the measured resource values. It does not improve the experimental controls or establish any additional performance claim.

## Changes made for publication

- Original absolute project paths become `$PROJECT_ROOT`; original fixture-directory paths, including their `/tmp` alias, become `$FIXTURE_DIR`. Replace these placeholders with local directories when reproducing a command.
- `pid` values become anonymous integer identifiers, mapped consistently across the JSON and JSONL files within each run directory. They are not real operating-system process IDs. Do not compare their identities across different runs.
- Exact wall-clock fields `started_unix_seconds` and `unix_seconds` are removed. Relative timestamps, run durations, phase boundaries, cumulative CPU times, and all resource measurements remain unchanged.
- Empty stdout/stderr logs, executables, caches, and fixture media are excluded from Git. Hardware/display specifications and software versions remain because they are needed to interpret the measurements; no serial number or hardware UUID is included.

Before processing, the complete original directory was copied to ignored `research/benchmarks/results_private/2026-11-10/`. File hashes confirmed that the private originals were unchanged. [`sanitize_results.py`](../../sanitize_results.py) produced a separate directory containing 15 JSON/JSONL files; the reviewed copies were then placed here.

The four runs were summarized from both the private originals and the public copies using [`summarize.py`](../../summarize.py). Every run summary was equal. This check includes the WindowServer CPU calculation that depends on stable process identity. JSON numeric measurements and file hashes are retained, without reformatting their numerical meaning.

## Preserved limitations

The first 720p24 run's display pixel metadata used an API that returned logical dimensions on this machine. Those original fields have not been corrected retroactively; see [`hardware.json`](hardware.json) for the verified display mode and the [benchmark guide](../../README.md) for context. Fields absent from an original record, including that first run's metadata video hash, have not been invented; fixture hashes are available in [`fixtures.json`](fixtures.json).

These runs remain short, single-profile pilots with uncontrolled background load. System GPU counters and all-decoder RSS are not attributable wallpaper cost. Publication processing does not establish a whole-system performance pass. See the [performance report](../../../05-performance-feasibility.md).

The sanitizer handles the known JSON schema fields and supplied path prefixes. It is not a general anonymizer or secret detector; future batches require a fresh content review and their own provenance note.
