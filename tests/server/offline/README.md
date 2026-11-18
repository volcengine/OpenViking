# Offline API contracts

Run `pytest -q -o addopts='' tests/server/offline/`.

The API workflow runs this suite on Linux, macOS, and Windows before starting
the credentialed server, including for fork PRs without model secrets. Each
test uses an isolated local configuration, the real ASGI routes, processing
queues, and local vector store. The parent server fixtures replace only model
calls. HTTPX transports and socket connections are rejected. Constant vectors
verify indexing, filtering, and response contracts; they do not measure
semantic relevance or ranking quality.

| Original API scenario excluded from the live-model phase | Offline coverage |
| --- | --- |
| `skills/test_add_skill.py` | dict and MCP conversion, `wait=True`, stored `SKILL.md`, name/description, L0/L1/L2 indexing |
| `filesystem/test_fs_cp.py` | file content and vector copying, source preservation, overwrite, recursive tree/empty directories, file/directory conflicts |
| `filesystem/test_fs_read_write.py` | writes with processing completion, exact content reads (also exercised by the copy tests) |
| `retrieval/test_find.py`, `retrieval/test_search.py` | response types, scoped retrieval of indexed files, limits, levels, scores |
| `resources/test_add_resource.py` | HTTP resource import, stored content, searchable L2 index |
| `scenarios/resources_retrieval/test_delete_sync.py` | deletion removes both files and indexed hits; the original did not assert index absence |
| `scenarios/resources_retrieval/test_resource_swap.py`, `test_pack_consistency.py` | their current bodies only import and search a resource, covered by the offline import/index test; they do not perform incremental updates or pack export/import |
| `scenarios/resources_retrieval/test_intent_extended_search.py` | session message reaches planner, expanded query reaches embedder, query plan and indexed resource returned |

The original scenarios remain available for manual live-model smoke tests.
Other CI categories were checked as well:

- Semantic retrieval and watch-update scenarios assert relevance or depend on
  generated abstracts. They remain in the credentialed phase; fixed vectors
  cannot establish their current assertions.
- Media/document ingestion and API effect tests retain their current live-model
  execution. Pure parser/content contracts in those larger suites are further
  migration candidates; assertions about generated output and provider
  integration still need separate live-model coverage.
- Session commit involves memory extraction. The current API acceptance checks
  are candidates for a separate offline migration, but that should preserve
  queued completion and memory persistence coverage, not only task acceptance.
- CLI model queries still use the external server. They are candidates for a
  separate mock-server phase that preserves the actual CLI subprocess and
  authentication. This ASGI suite does not replace CLI compatibility coverage.

This change removes quota dependencies from the listed API contracts. It does
not change production retries or eliminate limits and closed endpoints from
the remaining live-model/effect tests.
