# VikingDB path_glob Benchmark

Benchmark suite for OpenViking's **remote VikingDB glob** feature (`feat: support
remote vikingdb glob`). It validates the `glob.engine="auto"` path (which pushes
glob down to VikingDB `path_glob`) against the local `glob.engine="fs"` path, on
both **effectiveness** (missed/extra recall) and **performance** (latency).

Start `openviking-server` before running any import, verify, or benchmark step.

## What A New Runner Must Know

This benchmark is path-only. It is not validating parsing quality, file content
search, embedding quality, or vector semantic recall. The expected behavior is:

- `engine=fs` walks the local AGFS tree and matches filenames locally.
- `engine=auto` uses VikingDB `path_glob` when the configured threshold is met.
- Both engines must return the same URI set for every default pattern.

Before running, make sure:

1. `openviking-server` is configured with a VikingDB-backed vector store.
2. The server can write to its configured workspace.
3. The benchmark root `viking://resources/benchmark/glob` is either empty or is
   intentionally being reused with the same manifest.
4. Switch `glob.engine` in `ov.conf` and restart the server between `fs` and
   `auto` runs. For `auto`, set `switch_to_remote_threshold=1` to force remote
   `path_glob` for every non-empty benchmark subtree.

## Layout

```
vikingdb_path_glob/
├── common/                       # shared, engine-agnostic helpers
│   ├── dataset.py                # scale buckets, monorepo-like tree spec, manifest I/O
│   ├── glob_match.py             # globset-aligned matcher -> ground truth
│   ├── patterns.py               # bounded default patterns + opt-in stress patterns
│   └── inspect_dataset.py        # in-memory sample: depth distribution + match ratios
├── performance/                  # latency (fs vs auto)
│   ├── step0_prepare_data.py     # generate 1M one-byte files + manifest
│   ├── step1_add_resource.py     # import (vectors_only), resumable
│   ├── step2_verify.py           # poll until both glob faces are populated
│   └── step3_benchmark.py        # latency per scale; fs vs auto compare
└── effectiveness/                # recall/precision vs manifest ground truth
    └── step2_quality.py          # per-scale FN/FP for fs and auto
```

## Core idea

Glob only matches on the file **path/URI** — never on file content or vector
values. This has three consequences that shape the whole suite:

1. **One dataset feeds both engines.** A single `add_resource` with
   `processing_mode="vectors_only"` populates *both* glob faces at once: the
   on-disk AGFS tree (used by `engine=fs`) and the VikingDB collection `uri`
   records (used by `engine=auto`). So `fs` and `auto` are compared on the
   *identical* URI set.
2. **Files are one byte.** Empty (0-byte) files are skipped by the importer, so
   each generated file holds a single filler byte. 1,000,000 files cost ~1 MB of
   content.
3. **Ground truth is the manifest, not an engine.** `step0` writes a manifest of
   every path; `common/glob_match.py` matches patterns against it using the
   glob subset both engines agree on. Both `fs` and `auto` are scored against
   this same truth, so effectiveness surfaces missed recall (FN) and extra
   recall (FP) independently for each engine.

## Scales from one dataset

The dataset is split into disjoint top-level scale buckets, so globbing a bucket
path yields an exact volume, and globbing the root spans everything:

| Scale | Glob root (uri) | Files |
|------|-----------------|------:|
| 10   | `.../glob/scale_500/scale_10`  | 10 |
| 50   | `.../glob/scale_500/scale_50`  | 50 |
| 100  | `.../glob/scale_500/scale_100` | 100 |
| 200  | `.../glob/scale_500/scale_200` | 200 |
| 500  | `.../glob/scale_500` | 500 |
| 1k   | `.../glob/scale_1k`  | 1,000 |
| 2k   | `.../glob/scale_2k`  | 2,000 |
| 5k   | `.../glob/scale_5k`  | 5,000 |
| 10w  | `.../glob/scale_10w` | 100,000 |
| 20w  | `.../glob/scale_20w` | 200,000 |
| 50w  | `.../glob/scale_50w` | 500,000 |
| 100w | `.../glob` (root)    | 1,000,000 |

Base URI: `viking://resources/benchmark/glob`.

The 10/50/100/200 roots are disjoint deterministic slices nested inside
`scale_500`; the remaining 140 files retain their regular monorepo paths.
`step0_prepare_data.py` writes this final layout directly, so one import of
`scale_500` makes all five scales available without a post-import move.

## Data layout (close to real usage)

Each bucket is a synthetic multi-service monorepo whose paths vary in depth,
drawn from weighted path templates so directory nesting has real variance
rather than one fixed shape:

- **shallow**: `tools/x.sh` (1 level), `apps/web/x.ts` (2 levels)
- **medium**: `libs/core/src/x.py` (3), `services/auth/src/mod_03/x.go` (4)
- **deep**: `services/auth/src/api/mod_03/handlers/x.py` (6),
  `services/auth/src/main/python/acme/platform/mod_03/x.py` (8)
- plus `docs/<topic>/x.json` config docs and `services/<svc>/tests/test_x.py` tests

Sampled stats for regular single buckets: depth spans **1–8 levels**. The exact
subscales add one directory level to their selected `scale_500` paths, so the
whole `scale_500` view spans **1–9 levels**. The 100w root view adds the bucket
name prefix and spans **2–10 levels**. Verify yourself with
`python3 -m vikingdb_path_glob.common.inspect_dataset`.

## Query patterns (bounded result sets)

The default patterns cover empty results, absolute-count landmarks, filename
windows, and the structural / safe-syntax subset. Each pattern has no more than
1,000 manifest matches at any scale, so latency is not dominated by transferring
a huge response and effectiveness is not obscured by a remote result ceiling.
Each carries a `tier` and `target` label that scripts print alongside the exact
manifest truth:

| Tier | Pattern | Target match |
|------|---------|-------------|
| absolute | `**/definitely-missing/**/*.py` | 0 |
| absolute | `**/migrations/*.sql` | exactly 10 (100w root only) |
| absolute | `**/legacy-gateway/**/*.go` | exactly 100 (100w root only) |
| window | `**/file_0000000.*` | 0–7 |
| window | `**/file_000000?.*` | 7–70 |
| window | `**/file_00000??.*` | 73–645 |
| window | `**/file_00000??.py` | 15–179 |
| window | `**/file_00000??.{json,yaml}` | 4–50 |
| structure | `**/tests/test_file_00000??.py` | 6–55 |
| structure | `**/docs/**/file_00000??.json` | 1–23 |
| structure | `**/mod_0[0-4]/**/file_0000???.py` | 19–268 |
| structure | `**/mod_0?/**/file_0000???.ts` | 22–227 |
| structure | `**/services/auth/**/file_0000???.py` | 6–87 |

The two landmark subtrees exist only in the padding bucket, so their exact
10 / 100 counts appear under the 100w root view and are zero at single-bucket
scales. Broad patterns such as `**/*.py` remain available in
`STRESS_PATTERNS` for explicit result-limit and transfer tests, but are not run
by the default effectiveness or performance commands.

## Usage

### Quickstart

From this directory:

```bash
cd benchmark/retrieval/glob/vikingdb_path_glob/performance

# 1. Generate the dataset and manifest.
python3 step0_prepare_data.py

# 2. Import progressively; this command can be rerun to resume.
python3 step1_add_resource.py --buckets scale_500

# 3. Verify the imported slice.
python3 step2_verify.py --scales 10 50 100 200 500

# 4. After importing all desired buckets, test quality and performance once
#    with glob.engine=fs and once with glob.engine=auto.
python3 ../effectiveness/step2_quality.py --engine-label fs
python3 step3_benchmark.py --engine-label fs
python3 ../effectiveness/step2_quality.py --engine-label auto
python3 step3_benchmark.py --engine-label auto --compare step3_result_fs.json
```

### 1. Generate data

```bash
cd performance/
python3 step0_prepare_data.py            # full 1,000,000 files + manifest
python3 step0_prepare_data.py --smoke    # tiny dataset for a dry run
```

### 2. Import (vectors_only)

Start `openviking-server`, then:

```bash
python3 step1_add_resource.py            # resumable at package granularity
```

For progressive validation, import one or more buckets first, verify them, then
continue with the rest. Progress is recorded per import unit, so rerunning the
command skips completed units:

```bash
# small smoke slice
python3 step1_add_resource.py --buckets scale_500
python3 step2_verify.py --scales 10 50 100 200 500

# partial import inside a larger bucket
python3 step1_add_resource.py --buckets scale_10w --max-units 20
python3 step2_verify.py --scales 10w --timeout 60

# medium slices
python3 step1_add_resource.py --buckets scale_1k scale_2k scale_5k
python3 step2_verify.py --scales 1k 2k 5k

# continue remaining buckets; already imported units are skipped
python3 step1_add_resource.py
```

For large runs, shard the deterministic import unit list across parallel
processes. Each shard imports a disjoint round-robin slice and writes its own
progress file, e.g. 24 workers:

```bash
for i in $(seq 0 23); do
  python3 step1_add_resource.py --shard-count 24 --shard-index "$i" &
done
wait
```

### 3. Verify both faces are populated

The remote face lags (async vectorization). Verify per engine:

```bash
# ov.conf: glob = {"engine": "fs"}   -> restart server
python3 step2_verify.py
# ov.conf: glob = {"engine": "auto", "switch_to_remote_threshold": 1} -> restart
python3 step2_verify.py --heal
```

`--heal` first runs `check_consistency` on the benchmark subtree and, if any
record made it into AGFS but not the remote VikingDB face (a rare per-record
drop during bulk import), reindexes to fill the gap before verifying.

### 4. Effectiveness (FN/FP vs manifest)

```bash
# ov.conf: glob = {"engine": "fs"} -> restart
python3 ../effectiveness/step2_quality.py --engine-label fs

# ov.conf: glob = {"engine": "auto", "switch_to_remote_threshold": 1} -> restart
python3 ../effectiveness/step2_quality.py --engine-label auto
```

`switch_to_remote_threshold=1` forces every non-empty subtree to remote VikingDB
path_glob. Each pattern reports Recall / Precision / F1 and dumps FN/FP URIs.
All default patterns fit within the configured `node_limit`, so any FN or FP is
treated as a correctness failure.

### 5. Performance (fs vs auto)

```bash
# ov.conf: glob = {"engine": "fs"} -> restart
python3 step3_benchmark.py --engine-label fs

# ov.conf: glob = {"engine": "auto", "switch_to_remote_threshold": 1} -> restart
python3 step3_benchmark.py --engine-label auto --compare step3_result_fs.json
```

The `--compare` run prints an fs-vs-auto latency/speedup table and flags any
pattern whose match count differs between engines.

## Outputs And Metrics

Generated data and manifests live under:

```text
~/.openviking/data/benchmark/glob_synthetic_v2/
├── files/                  # generated one-byte source files
├── manifest/               # one path manifest per bucket + index.json
└── effectiveness/<engine>/ # quality results and FN/FP dumps
```

Performance outputs are written in the current working directory:

```text
step3_result_fs.json
step3_result_auto.json
```

Effectiveness fields to watch:

- `truth_count`: manifest-computed expected file matches.
- `found_count`: files returned by OpenViking `glob`.
- `recall`, `fn`: missed expected files. Recall should be `1.0` and `fn=0` for
  recall-critical patterns.
- `precision`, `fp`: extra files. Precision should be `1.0` and `fp=0`.
- `miss/*.json`: first FN/FP URIs for debugging mismatches.

Performance fields to watch:

- `avg_ms`, `min_ms`, `max_ms`: latency over repeated requests for one
  `(scale, pattern)` pair.
- `matches`: returned file count with the benchmark `node_limit`.
- comparison `speedup`: `fs_avg_ms / auto_avg_ms`; values above `1.0x` mean
  `auto` is faster, below `1.0x` mean `auto` is slower.
- a `*` in the comparison table means the two engines returned different match
  counts, so treat the latency row as suspect until quality is understood.

## Common Checks

- If `step2_verify.py` times out in `auto` mode, the remote VikingDB face may
  not have caught up yet. Re-run later, or use `--heal` to run consistency
  repair before polling.
- If a default pattern is truncated, confirm that `node_limit` is at least
  2,000. Broad patterns in `STRESS_PATTERNS` may still hit remote result limits.
- If `auto` is unexpectedly not used, confirm `glob.engine=auto`,
  `switch_to_remote_threshold=1`, and that the collection schema has a `uri`
  field.
- If `step1_add_resource.py` stops midway, re-run it with the same arguments.
  Completed import units are skipped from the progress files.
