# Message Decision E2E Benchmark

This benchmark runs the complete message-decision path:

```text
mock Lark ingress
  -> daemon prompt and real model
  -> Agent delivery tools
  -> freshness and covers validation
  -> fake Lark delivery
  -> decisions ledger
```

It sends no real chat messages and publishes nothing externally.

## Scenarios

- `combined`: one reply handles two inputs; the newest input is the fence and both inputs are covered.
- `older-with-newest-fence`: the newest input fences a reply that handles only the older input; the newest input receives its own silence decision.
- `separate`: two independent questions both receive explicit decisions.
- `concurrent-update`: a second input arrives after the model turn starts; the final decisions must still use correct attribution. A stale attempt is recorded when the timing causes one, but is not required because the model may see the steered update before its first delivery.

## Smoke

The smoke run uses the scripted fake Codex runtime but still calls the real Agent action socket, fake Lark connector, outbox, and decision ledger:

```sh
cd superagent
node benchmark/message-decision-e2e/run.mjs \
  --smoke \
  --out /tmp/message-decision-e2e-smoke
```

## Real TraeX Run

Each scenario gets a fresh daemon and isolated TraeX home:

```sh
cd superagent
node benchmark/message-decision-e2e/run.mjs \
  --model GPT-5.6-Sol \
  --timeout 120 \
  --out /tmp/message-decision-e2e-real
```

Run one scenario with `--scenario combined`. Override the local runtime with
`--bin` and `--auth-file`.

## Evidence

The output root contains `summary.json`; each scenario contains `result.json`.
The scenario result preserves:

- `toolCalls`: actual final tool arguments, including `decision_id`, `response_to_id`, and `covers`;
- `deliveries`: durable outbox rows;
- `decisions`: exact `outcome` and `covered_by` values;
- `activities` and `staleDecisions`;
- deterministic `assessment.checks`.

The scorer does not use an LLM judge. A timeout or model transport error is
saved with the evidence instead of discarding the run.
