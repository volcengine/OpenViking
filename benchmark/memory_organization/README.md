# Memory Organization JSON vs Python A/B

This benchmark compares model behavior under the JSON and restricted-Python output
protocols while keeping the model, existing files, schema, instructions, retry budget,
and output-token cap identical.

## Autonomous decision suite

The primary hypothesis is tested without exposing expected topic names or fact partitions
to the model. The default suite contains exactly three cases:

- two paths differing only by directory case, where the schema generally requires
  case-insensitive lowercase normalization;
- one oversized `profile.md` containing durable profile facts mixed with preferences; all
  preference facts must move to `preferences` while valid profile facts remain in `profile.md`;
- one oversized broad `work_preferences.md` containing two coherent behavioral dimensions;
  after adding new facts it must become exactly two focused preference files, with every fact
  preserved exactly once and the broad source removed.

The expected organization exists only in the grader. `AutonomousProvider` subclasses the
production `SessionExtractContextProvider`, inherits its `instruction()` unchanged, and exposes
the complete enabled user-stage schema registry loaded from the production YAML files. The only
controlled fixture input is which existing files the production-format prefetch read results
contain; no benchmark-specific maintenance request is added. Run and report it with:

    python -m benchmark.memory_organization.run_autonomous_ab --repeat 20 --parallel 6 \
      --output benchmark/memory_organization/result/core_three_repeat10.jsonl
    python -m benchmark.memory_organization.report_autonomous \
      benchmark/memory_organization/result/core_three_repeat10.jsonl \
      --output benchmark/memory_organization/result/core_three_repeat10.summary.json

The report exposes two primary, orthogonal metrics for every case:

- `organization_action_success`: whether the required merge, move, or split happened;
- `information_integrity`: whether every expected fact remains present exactly once.

The action metric is deliberately structural. Missing, duplicated, or misclassified content is
reflected only by information integrity, keeping the two metrics independent.

For the oversized preference case, any split into two or more preference files is accepted when
the broad source file is removed. The number and names of child topics are not graded.

## Deterministic execution suite

The 12 fixtures cover four duplicate-file merges, four mixed-file splits, and four
combined merge-and-split plans. They are useful for deterministic protocol execution checks;
older guided result artifacts explicitly disclosed target groupings and must not be used as
evidence of autonomous planning quality. Likewise, `autonomous_*` artifacts without the
`production_` prefix predate exact production-prompt reuse and are superseded.
An additional stress suite uses 24–32 complete fact lines to test an oversized split,
an eight-file alias merge, and a large mixed reorganization separately from the pilot.

Facts contain immutable Fxx markers, so the grader checks final file placement,
retention, duplication, deletion, and canonical replacement deterministically. The
fixtures use full-field replacement content to isolate organization planning from
SEARCH/REPLACE matching noise.

## Smoke run

    python -m benchmark.memory_organization.run_ab --case merge_travel_aliases \
      --repeat 1 --output benchmark/memory_organization/result/smoke.jsonl
    python -m benchmark.memory_organization.report \
      benchmark/memory_organization/result/smoke.jsonl

## Paired experiment

Start with 5 repeats (120 model runs), then increase to 20 after inspecting smoke output:

    python -m benchmark.memory_organization.run_ab --repeat 5 \
      --output benchmark/memory_organization/result/ab_repeat5.jsonl
    python -m benchmark.memory_organization.report \
      benchmark/memory_organization/result/ab_repeat5.jsonl \
      --output benchmark/memory_organization/result/ab_repeat5.summary.json

Run the larger stress fixtures independently so they do not change the pilot population:

    python -m benchmark.memory_organization.run_ab \
      --cases benchmark/memory_organization/cases/organization_stress_cases.json \
      --repeat 5 --output benchmark/memory_organization/result/stress_repeat5.jsonl

The report separates two success metrics:

- `content_organization_success_rate`: final file partition, complete fact text,
  retention, placement, and deduplication all pass.
- `organization_success_rate`: content organization passes and canonical merge
  replacements preserve link inheritance correctly.

Paired counts and a two-sided exact McNemar p-value are reported for both metrics. A
protocol advantage requires repeated paired runs with more exclusive wins and acceptable
retry, token, and latency cost; one favorable sample is not sufficient.

