# SAG2 lexical retrieval and multi-hop expansion

SAG2 is an opt-in local retrieval backend backed by SQLite records and FTS5/BM25. It does not require an embedding model. The default backend remains `local`; existing vector collections are not migrated automatically, and failures never trigger a backend switch or hybrid recall.

## Configuration

```json
{
  "storage": {
    "workspace": "./data",
    "vectordb": {
      "backend": "sag2",
      "sag2": {
        "candidate_k": 50,
        "seed_k": 10,
        "expansion_term_limit": 20,
        "protected_k": 5,
        "max_hops": 1,
        "max_context_tokens": 32000,
        "total_timeout_s": 30
      }
    }
  }
}
```

The database is `<workspace>/sag2/<collection>.sqlite3`. This version supports one OV service instance per workspace. Multiple writers, NFS, and object-storage-backed SQLite are unsupported.

The default `max_judgments` is `B + H * (B + K + F)`: 115 for B50/H1 and 330 for B100/H2. This is a scoring budget per TypedQuery, not a guarantee that every hop runs. If the next complete scoring pool exceeds the budget, SAG2 returns the previous complete round. It never truncates that pool, adds a final scoring pass, or returns partial scores. Each TypedQuery in a parent `search()` has its own budget.

## Retrieval modes

| Public entrypoint | Behavior |
| --- | --- |
| `find()` and `search_type="keywords"` | One BM25 round without a model |
| Semantic QUICK | One BM25 round without a model |
| Semantic THINKING with H=0 | BM25 plus one JEV necessary-evidence scoring round |
| Semantic THINKING with only L0/L1 levels | Up to `max_hops` lexical expansions |
| Semantic THINKING with `level=None` or L2 | Preserve all requested levels and force H=0 |

QUICK exposes `-bm25` (higher is better). These scores are not comparable with vector or JEV scores, so previous vector thresholds must be recalibrated. THINKING exposes only JEV scores in `[0,1]`.

THINKING uses the existing JEV configuration with `provider="jev"` and `mode="noul"`. Without a usable reranker, automatic mode selects QUICK. Other rerankers and JEV Choice also select QUICK in automatic mode; explicitly requesting SAG2 THINKING with them fails fast. Model errors, incomplete responses, oversized passages, and index-version conflicts fail the TypedQuery. SAG2 never falls back to vector scores, truncates evidence, or returns partial success.

## Storage, authorization, and consistency

- Writes reuse OV queues, record IDs, FieldPatch, merge, rename/copy/delete, and acknowledgements. Only embedding inference is skipped; summary generation is unchanged.
- Initial recall, every expansion, and title/alias repair share one account/ACL/target/context/level/tags/filter scope. ROOT requests are also bound to their request account.
- L0 and L1 records with the same URI remain separate inside the evidence chain. URI deduplication happens only before public result conversion.
- Expansion uses the algorithm's collection+account N/DF statistics. Titles and aliases use only the canonical scope.
- Index metadata includes the tokenizer, Jieba version, schema, and record chunk policy. Incompatible indexes require a rebuild.
- Retrieval-related writes advance the account epoch in the same transaction. A concurrent content, ACL, or scalar change during multi-round retrieval raises `IndexChanged`; OV does not automatically repeat billable JEV calls.
- Exact grep stays on the filesystem engine. SAG2 tokenization removes stopwords and punctuation and therefore cannot provide a complete exact-grep candidate set.

Migrate with a blue/green workspace and rebuild records/FTS from OVPack or the source-of-truth content. Do not switch an existing workspace in place or require dense snapshots. Once the target accepts new writes, rollback requires stopping writes and migrating the increment back to the source before switching endpoints.

This change implements the P0–P3 functional path. Large-scale performance, soak tests, real-JEV quality, production migration, canary rollout, and rollback exercises remain P4/P5 acceptance work and are not claimed by unit or mocked-JEV tests.
