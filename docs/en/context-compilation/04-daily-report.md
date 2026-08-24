# Example: Daily Report

Compile timestamped conversation logs, agent sessions, IM messages, collaborative documents, meeting notes, and task records into concise, evidence-grounded **daily reports**: one page per day.

Skill source: [examples/compile/ov-compile-skills/daily-report](https://github.com/volcengine/OpenViking/tree/main/examples/compile/ov-compile-skills/daily-report)

## Step 1: Prepare the sources

Daily-report sources are usually sessions, messages, or documents already in OpenViking. To import a batch of records from local:

```bash
ov add-resource ./work-logs --to viking://resources/work-logs --wait
ov ls -r viking://resources/work-logs
```

## Step 2: Add the Skill

```bash
ov add-skill examples/compile/ov-compile-skills/daily-report --wait
ov skills list
# → viking://agent/skills/daily-report  (or viking://user/<you>/skills/daily-report)
```

## Step 3: Run compile

Spell out the **date, timezone, report subject, and emphasis** in `--reason` — the Skill uses it to scope and prioritize:

```bash
ov compile \
  --from viking://resources/work-logs \
  --to viking://resources/daily-report \
  --skill viking://agent/skills/daily-report \
  --reason "Daily report for 2026-08-20, focused on my outcomes and decisions" \
  --wait
```

For several days at once, put the date range in `--reason` (each day is still its own page):

```bash
ov compile \
  --from viking://resources/work-logs \
  --to viking://resources/daily-report \
  --skill viking://agent/skills/daily-report \
  --reason "One daily report per day for 2026-08-18 to 2026-08-20" \
  --wait
```

Drop `--wait` to get a `task_id` back immediately:

```bash
ov task status cmp_01abc      # progress and final result
ov task cancel cmp_01abc      # cooperative cancel
```

## Step 4: Inspect the output

Reports are plain Markdown — just read them:

```bash
ov tree viking://resources/daily-report
ov read viking://resources/daily-report/2026-08-20.md
```


## Related docs

- [Context Compilation Overview](./01-overview.md)
- [Knowledge Distillation example](./05-knowledge-distillation.md)
- [VikingBot API → compile()](../api/24-vikingbot.md#compile)
