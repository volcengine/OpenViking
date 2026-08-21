# Example: Knowledge Distillation

**Distill** one or more knowledge bases or document collections into topic-organized, evidence-grounded high-level knowledge: cross-source findings, trends, changes, drivers, comparisons, implications, and uncertainties.

Typical uses: distill a knowledge base, compare multiple collections, or derive higher-order insights such as "changes across financial reports."

The output is a shallow, topic-organized artifact tree where each topic directory is a durable semantic area and each page is one independently useful high-level conclusion:

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

## Step 1: Prepare the sources

```bash
ov add-resource ./finance-reports --to viking://resources/finance-reports --wait
ov ls -r viking://resources/finance-reports
```

## Step 2: Add the Skill

```bash
ov add-skill examples/compile/ov-compile-skills/knowledge-distillation --wait
ov skills list
# → viking://agent/skills/knowledge-distillation  (or viking://user/<user_name>/skills/knowledge-distillation)
```

## Step 3: Run compile

Spell out the **analytical question, comparison dimensions, baseline, and scope** in `--reason` — it directly sets the direction of the distillation:

```bash
ov compile \
  --from viking://resources/finance-reports \
  --to viking://resources/finance-insights \
  --skill viking://agent/skills/knowledge-distillation \
  --reason "Compare the last three years of reports; obtain changes and drivers in revenue quality, profitability, and risk" \
  --wait
```

`--from` accepts multiple sources for cross-knowledge-base comparison:

```bash
ov compile \
  --from viking://resources/finance-2024,viking://resources/finance-2025 \
  --to viking://resources/finance-insights \
  --skill viking://agent/skills/knowledge-distillation \
  --reason "Compare the two yearly knowledge bases; surface changes and structural differences in key metrics" \
  --wait
```

Drop `--wait` to get a `task_id` back immediately:

```bash
ov task status cmp_01abc      # progress and final result
ov task cancel cmp_01abc      # cooperative cancel
```

## Step 4: Inspect the output

Read the topic tree first, then drill into a specific conclusion page:

```bash
ov tree viking://resources/finance-insights
ov read viking://resources/finance-insights/revenue-quality/growth-shifted-from-volume-to-pricing.md
```

By default **no `index.md` is created** — a distillation is itself a set of conclusions, unless `--reason` explicitly asks for a navigation page. Re-running refreshes the same analysis page and time-bounds any conclusion that may change.

## Related docs

- [Context Compilation Overview](./01-overview.md)
- [Daily Report example](./04-daily-report.md)
- [VikingBot API → compile()](../api/24-vikingbot.md#compile)
