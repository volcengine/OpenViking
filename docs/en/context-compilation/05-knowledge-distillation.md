# Example: Knowledge Distillation

Extract conclusions from one or more knowledge bases, organize them by topic, and retain source references and uncertainties.

For example, compare financial reports to analyze changes in revenue, profitability, and risk over time.

The output uses one directory per topic and one page per conclusion:

```text
revenue-quality/
  growth-shifted-from-volume-to-pricing.md
  overseas-growth-offset-domestic-slowdown.md
profitability/
  margin-recovered-but-cash-conversion-weakened.md
risk/
  customer-concentration-increased.md
```

> This is a shape example only — the real topics and findings come from the domain you supply. Note that page names state the **conclusion itself** (`growth-shifted-from-volume-to-pricing`), not a source title (`q2-report-summary`).

Skill source: [examples/compile/ov-compile-skills/knowledge-distillation](https://github.com/volcengine/OpenViking/tree/main/examples/compile/ov-compile-skills/knowledge-distillation)

Check the [prerequisites](01-overview.md#prerequisites) and run these commands from the OpenViking repository root. Replace the source directory with your own material.

## Step 1: Prepare the sources

```bash
ov add-resource ./finance-reports --to viking://resources/finance-reports --wait
ov ls -r viking://resources/finance-reports
```

## Step 2: Add the Skill

```bash
ov add-skill examples/compile/ov-compile-skills/knowledge-distillation -p viking://agent/skills --wait
ov skills list
# → viking://agent/skills/knowledge-distillation
```

## Step 3: Run compile

Spell out the **analytical question, comparison dimensions, baseline, and scope** in `--instruction` — it directly sets the direction of the distillation:

```bash
ov compile \
  --from viking://resources/finance-reports \
  --to viking://resources/finance-insights \
  --skill viking://agent/skills/knowledge-distillation \
  --instruction "Compare the last three years of reports; obtain changes and drivers in revenue quality, profitability, and risk"
```

`--from` accepts multiple sources for cross-knowledge-base comparison:

```bash
ov compile \
  --from viking://resources/finance-2024,viking://resources/finance-2025 \
  --to viking://resources/finance-insights \
  --skill viking://agent/skills/knowledge-distillation \
  --instruction "Compare the two yearly knowledge bases; surface changes and structural differences in key metrics"
```

The command returns a `task_id` immediately:

```bash
ov task status cmp_01abc      # progress and final result
ov task cancel cmp_01abc      # cooperative cancel
```

## Step 4: Inspect the output

List the output first, then read a generated conclusion page. The filename below is illustrative:

```bash
ov tree viking://resources/finance-insights
ov read viking://resources/finance-insights/revenue-quality/growth-shifted-from-volume-to-pricing.md
```

By default **no `index.md` is created** — a distillation is itself a set of conclusions, unless `--instruction` explicitly asks for a navigation page. Re-running refreshes the same analysis page and time-bounds any conclusion that may change.

## Related docs

- [Context Compilation Overview](./01-overview.md)
- [Daily Report example](./04-daily-report.md)
- [Agent Runtime API](../api/23-agent-runtime.md)
