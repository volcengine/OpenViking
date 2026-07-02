# Upgrades and Migrations

This guide collects the recovery steps for the upgrade-time blockers that
have surfaced most often in real deployments. If your container exits at
boot after pulling a newer image, start here before filing an issue.

## When to read this guide

- You are upgrading an existing OpenViking deployment between minor
  versions.
- The server fails to start after the upgrade (the container exits or
  the health check never goes green).
- You see `ModuleNotFoundError: No module named 'openviking.console.bootstrap'`
  in the container logs.
- You see `EmbeddingRebuildRequiredError` in the server logs.

## Before you upgrade

A few minutes of preparation makes every other step in this guide
recoverable. Do all of these before pulling a new image.

- **Snapshot your data directory.** This is the directory mounted into
  the container at `/app/.openviking` (typically `~/.openviking` on the
  host). The two paths that matter for retrieval are the AGFS root and
  `vectordb/`. A simple `cp -a` or `tar` of the whole directory while
  the server is stopped is enough; you do not need a live backup tool.
- **Note your current `ov.conf`.** Embedding model, provider, and
  dimension are the fields most likely to drift between versions and
  to break startup. Keep a copy of the file you were running with so
  you can roll back if the upgrade fails.
- **Stop the server gracefully.** Use `docker stop <container>` (or
  `docker compose down`). Avoid `docker kill -9` / `SIGKILL`: the
  vector index relies on a clean shutdown to release locks under
  `vectordb/<collection>/store/LOCK`, and a hard kill can leave a
  stale lock that blocks the next start.

## Common breaking transitions

The two failures below account for the majority of upgrade reports
between v0.3.15 and the v0.3.x series after it. They can happen
together — the server may exit on the first one, and only after you
fix it do you see the second one — so read both before changing
anything.

### v0.3.15 → v0.3.19+ : `openviking.console.bootstrap` removed

- **Symptom.** The container exits immediately after start. The log
  shows `ModuleNotFoundError: No module named 'openviking.console.bootstrap'`,
  often coming from a `python -m openviking.console.bootstrap ...`
  line in your `command:` override.
- **Cause.** Web Studio used to ship as a separate process started by
  `python -m openviking.console.bootstrap`. Starting in v0.3.19 the
  Studio assets are bundled into `openviking-server`, and the
  standalone `openviking.console.bootstrap` module no longer exists
  (see PR #2320). Any custom `command:` that still launches it will
  fail with `ModuleNotFoundError`.
- **Fix.** In your `docker-compose.yml` (or whatever you use to run
  the container), drop the `python -m openviking.console.bootstrap`
  invocation. The default entrypoint already runs `openviking-server`,
  which now serves both the API on port `1933` and the Studio UI.
- **Worked example.**

  Before — two processes, one of them now-removed:

  ```yaml
  services:
    openviking:
      image: ghcr.io/volcengine/openviking:latest
      command: |
        openviking-server &
        python -m openviking.console.bootstrap --host 0.0.0.0 --port 8020
  ```

  After — single process, default entrypoint:

  ```yaml
  services:
    openviking:
      image: ghcr.io/volcengine/openviking:latest
      # no `command:` override needed — the image entrypoint runs
      # openviking-server, which now also serves Web Studio.
  ```

  If you still want to keep an explicit `command:`, set it to
  `command: openviking-server` and remove the bootstrap line.

### Any version with `EmbeddingRebuildRequiredError`

- **Symptom.** The server logs `EmbeddingRebuildRequiredError:
  Existing collection embedding dimension (...) does not match current
  configuration (...)` or
  `EmbeddingRebuildRequiredError: Existing collection embedding metadata
  does not match current configuration`. Startup aborts before the
  HTTP server is ready.
- **Cause.** The vector collection on disk records which embedding
  provider, model, and dimension were used to build it. When the
  embedding section of `ov.conf` changes (different provider, different
  model, or — most importantly — a different vector dimension) the
  existing vectors are no longer comparable to new ones. The server
  refuses to start rather than mix incompatible vectors.
- **Choose one path.** Both paths preserve your business data; they
  differ only in whether you keep the old vectors or rebuild them.

  **Path A — keep your data, restore the old embedding config.** Roll
  the embedding section of `ov.conf` back to the values the existing
  collection was built with (the values you noted in *Before you
  upgrade*). The server will start. Schedule the embedding-model
  change as a deliberate migration via Path B during a maintenance
  window. If the only change between old and new config is provider
  or model name and the dimension is identical, you can also set
  `embedding.allow_metadata_override = true` in `ov.conf` to keep the
  existing vectors and just rewrite the recorded metadata.

  **Path B — rebuild embeddings under the new config.** This
  re-embeds every resource, memory, and skill. The cost is one full
  embed pass over your indexed content, billed against whatever
  embedding provider you have configured.

  1. **Back up `vectordb/context/`.** Inside your data directory
     (host: `~/.openviking`, container: `/app/.openviking`), rename
     `data/vectordb/context/` to something like
     `data/vectordb/context.bak-<date>/`, or copy it elsewhere. Do
     **not** delete it yet — you want a fallback if the rebuild fails
     halfway.
  2. **Delete only `data/vectordb/context/`.** Do not delete other
     directories under `data/`. The AGFS tree (resources, memories,
     skills, sessions) lives outside `vectordb/` and is what we are
     trying to preserve. Removing anything else risks losing the very
     data you are rebuilding embeddings for.
  3. **Start the server with the new `ov.conf`.** It will create a
     fresh `vectordb/context/` collection that matches the new
     embedding configuration. The server should now come up and pass
     `/health`.
  4. **Reindex your namespaces.** Use the CLI to re-embed the content
     that previously had vectors:

     ```bash
     ov reindex viking://resources --mode vectors_only --wait true
     ov reindex viking://user/memories --mode vectors_only --wait true
     ov reindex viking://agent/memories --mode vectors_only --wait true
     ov reindex viking://agent/skills --mode vectors_only --wait true
     ```

     Run only the namespaces you actually use. `--mode vectors_only`
     re-embeds against the existing semantic summaries (L0/L1) and is
     the right choice when only the embedding configuration changed.
     If your semantic-summary configuration also changed, use
     `--mode semantic_and_vectors` instead — that re-runs L0/L1
     summarization as well and costs additional VLM calls.
  5. **Verify search works.** Run a query you know the answer to
     against a representative URI:

     ```bash
     ov find "<known-string>" --target-uri viking://resources/
     ```

     Once you are satisfied, delete the `context.bak-<date>/` backup.

## Sanity checks after a successful upgrade

Run these against the upgraded container before pointing production
traffic at it.

- `curl http://localhost:1933/health` returns a healthy response.
- `ov tree viking://resources -L 1` lists the resources you expect to
  see — confirms the AGFS tree survived the upgrade.
- `ov find <known-string>` returns the hits you expect — confirms the
  vector index is populated and queryable.
- The Studio UI loads at the same port you used before (default
  `1933` for direct access, or `1934` if you go through Caddy).

## What to do if you are stuck

If none of the above resolves the failure, file an issue with:

- The full server logs from the start of the failing run (everything
  from container start through the first stack trace).
- Your `ov.conf`, with API keys and other secrets redacted.
- The exact version you upgraded **from** and **to** (image tag is
  fine).
- The output of `ls data/vectordb/` from the data directory you are
  pointing at.

Tag the issue with `upgrade` so the maintainers can route it. See
also the related migration note for the User / Peer model in
[migration/01-user-peer-model.md](../migration/01-user-peer-model.md)
if you are crossing the 0.3.x → 0.4.0 boundary.
