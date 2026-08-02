# Recall admission regression

This lightweight runner measures whether `/api/v1/search/recall` admits or
abstains for a JSONL query set. It is intentionally separate from the broader
RAG benchmarks: its job is to catch context-injection regressions, especially
irrelevant queries that still produce memory.

Start with shadow mode so the server returns its legacy recall result while
also reporting what the admission policy would have done:

```bash
python benchmark/recall_admission/run.py \
  --input benchmark/recall_admission/fixtures/public_negative_queries.jsonl \
  --url http://127.0.0.1:1933 \
  --admission-mode shadow \
  --type-min-score events=0.50 \
  --type-min-score entities=0.50 \
  --type-min-score preferences=0.50 \
  --other-peer-score-delta 0.08
```

Each input line requires `id`, `query`, and `expected`, where `expected` is
`accept` or `abstain`. Keep deployment-specific positive queries in an
untracked file: a positive case must refer to memory that actually exists in
the target server. The checked-in fixture contains only synthetic negative
queries and no user data.

The runner deliberately omits query text, memory content, and URIs from its
output. It prints one aggregate record per case and a summary containing:

- `false_injection_rate`: expected-abstain cases that the policy admitted.
- `missed_recall_rate`: expected-accept cases that the policy rejected.
- latency percentiles for the complete recall endpoint request.

Use `--admission-mode enforce` only after the shadow results meet your target.
The process exits non-zero when a case fails, which makes the same file useful
as a CI or release regression gate.
