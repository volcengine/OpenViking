# Memory Consolidation

Besides compiling source material into Wikis, knowledge graphs, daily reports, and other artifacts with a Skill, `ov compile` has a special mode: **memory consolidation**. Instead of producing a new knowledge shape, it cleans up the **existing memories** in OpenViking in place — deduplicating, merging, splitting, and compacting them — while strictly conforming to each memory type's original schema.

Unlike regular compile, memory consolidation **does not go through VikingBot**; it runs directly inside OpenViking on the memory framework.

## When to use it

Memories accumulate session after session, and over time you get:

- **Duplicates**: the same person or event recorded multiple times with slightly different wording;
- **The same entity split across files**: extraction in different batches failed to recognize them as one object (e.g. "Ah-Zhen" and "Chen Jingxian" are actually the same person);
- **Mixed content**: unrelated objects crammed into one memory;
- **Verbosity**: repetitive phrasing that could be tighter.

Running memory consolidation over a memory directory makes such a collection clean and non-redundant again, without losing facts.

## How to use it

Set `--skill` to the sentinel value **`memory`** and point `--to` at a **memory-type directory**:

```bash
# Consolidate entities (dedup / merge / normalize in place)
ov compile \
  --to viking://user/<user_id>/memories/entities \
  --skill memory \
  --instruction "Merge clearly duplicate entities, but do not merge distinct ones; keep each entity's unique facts"
```

You can consolidate other memory types too, e.g. preferences:

```bash
ov compile \
  --to viking://user/<user_id>/memories/preferences \
  --skill memory
```

The command returns a `cmp_...` task ID immediately. Use `ov task status <id>` to check the result and `ov task cancel <id>` to stop it.

## Parameters

| Parameter | Description |
|-----------|-------------|
| `--skill memory` | The fixed sentinel value that triggers memory-consolidation mode (it is not resolved as a real Skill). |
| `--to` | Required, and must be a **memory-type directory** (e.g. `.../memories/entities`), not just the `.../memories` root. Consolidation happens in place inside this directory. |
| `--from` | **Not accepted** in memory mode — consolidation pulls in no external sources; it only reorganizes memories already under `--to`. |
| `--instruction` | Optional. Passed to the model as a consolidation instruction (a soft hint). Use it to make merges the model cannot infer on its own, e.g. "Ah-Zhen is Chen Jingxian, please merge them." |

## Behavior

- **Single type**: only the schema of the memory type inferred from `--to` is loaded; consolidation stays within that one type and never touches others.
- **In place**: `--from` and `--to` are the same space and no external source is introduced, so there is no cross-identity leakage. The space being consolidated (the current user's own *self* space, or a `peers/{peer_id}` space) is determined by the `--to` URI.
- **Conservative merging**: only memories that are clearly the same identity are merged; distinct entities are kept separate even when they share a topic, category, or attributes. A merge the model cannot infer from content (e.g. two different names that are actually one person) is performed only when `--instruction` spells it out.
- **No fabrication**: it only reorganizes existing memories; it never invents new facts.
- **Facts preserved**: merges and compaction keep every distinct atomic fact and only compress duplicate wording.

## Result

A memory-mode task result includes the **list of changed files** for the run, with fields following the same semantics as the archived `memory_diff.json`:

| Field | Meaning |
|-------|---------|
| `adds` / `total_adds` | Newly created memory files (e.g. new entities produced by a split) |
| `updates` / `total_updates` | Modified memory files (a merge target, an in-place compaction) |
| `deletes` / `total_deletes` | Removed / merged-away memory files |
| `trace_id` | The trace of this consolidation run, for troubleshooting |

The list contains file URIs only, not content — a single run may touch many files, and their bodies are not returned in the result.

A typical merge (folding "Ah-Zhen" into "Chen Jingxian"):

```json
{
  "memory_type": "entities",
  "trace_id": "…",
  "adds": [],
  "updates": ["viking://user/xiaomei/memories/entities/person/陈静娴.md"],
  "deletes": ["viking://user/xiaomei/memories/entities/person/阿珍.md"],
  "total_adds": 0,
  "total_updates": 1,
  "total_deletes": 1,
  "errors": []
}
```

## Prerequisites

- A running OpenViking service (memory consolidation runs inside the service process; enabling Bot is not required). The default endpoint is `http://localhost:1933`; remote use needs an API Key — see [Authentication](../guides/04-authentication.md).
- The `ov` CLI configured with a connection (`~/.openviking/ovcli.conf` or `OPENVIKING_*` environment variables).
- The memory directory pointed to by `--to` already contains memories (typically extracted from sessions by session commit).

## Related docs

- [Context Compilation Overview](./01-overview.md) — the overall introduction to `ov compile`
- [Agent Runtime API](../api/23-agent-runtime.md) — full reference for creating, inspecting, and cancelling Compile tasks
