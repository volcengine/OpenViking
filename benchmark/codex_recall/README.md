# Codex auto-recall regression benchmark

This benchmark measures the user-visible Codex `UserPromptSubmit` hook rather
than retrieval in isolation. It records end-to-end hook latency, irrelevant
memory injection, known-positive recall, and injected-context tokens.

Input is JSONL with `id`, `query`, and `expected` (`accept` or `abstain`). A
positive case may provide `gold_uri` or `gold_uris`; the run counts a hit only
when the injected context cites at least one expected URI. Keep
deployment-specific positives in an untracked file. The checked-in fixture is
synthetic and contains no user memory or profile data.

Run a controlled low-latency profile against a local server:

```bash
python benchmark/codex_recall/run.py \
  --input /path/to/private-cases.jsonl \
  --output /tmp/codex-recall-main.json \
  --label main \
  --repeat 5 \
  --max-p50-ms 700 \
  --max-p95-ms 1500 \
  --max-false-injection-rate 0.02 \
  --min-positive-recall-rate 0.90 \
  --max-injection-p95-tokens 900
```

The default profile disables compression and query expansion, caps assembly at
800 tokens, disables cross-turn deduplication, and uses a unique session per
sample. Override the corresponding CLI options to measure another profile.
Credentials are passed to the child hook but never included in output.
Candidate-only tuning can be passed with repeatable
`--variant-env OPENVIKING_NAME=VALUE`. Only recall-related tuning names are
accepted, and their non-secret values are recorded for reproducibility.

Run the same fixture and profile from a candidate checkout, then reject a
reverse optimization with:

```bash
python benchmark/codex_recall/compare.py \
  --baseline /tmp/codex-recall-main.json \
  --candidate /tmp/codex-recall-candidate.json
```

Both runs must use the same fixture, repetitions, timeout, and tokenizer. Their
strategy settings may differ intentionally. Quality and injected-token
regressions are strict. Latency allows 5% plus 25ms by default to absorb local
scheduling jitter; both tolerances are configurable.
The reports contain case IDs, decisions, aggregate metrics, and a fixture hash,
but omit query text, memory content, gold URIs, server URLs, credentials, and
hook paths.
