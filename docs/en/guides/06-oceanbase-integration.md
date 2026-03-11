# OpenViking and OceanBase Integration

This guide describes how to use [OceanBase](https://www.oceanbase.com/) as the vector store backend for OpenViking, with **official step-by-step tutorials** you can run from scratch.

---

## Overview

- Connection is via [pyobvector](https://github.com/oceanbase/pyobvector) and OceanBase’s vector tables and HNSW index.
- Set `storage.vectordb.backend` to `"oceanbase"` and fill in the `oceanbase` connection block in config.
- Suitable when you want context indexes in a relational/HTAP database or to reuse an existing OceanBase deployment.

**Prerequisites**: OceanBase 4.3.3.0+ (with vector type and index support); install `pyobvector` or `openviking[oceanbase]`.

---

## Installation and configuration

### Install

```bash
# Option 1: pyobvector only (if openviking is already installed)
pip install pyobvector

# Option 2: openviking with oceanbase extra (recommended)
pip install openviking[oceanbase]
```

### Configuration example (ov.conf)

In `~/.openviking/ov.conf`, set `storage.vectordb` and the `oceanbase` block:

```json
{
  "storage": {
    "vectordb": {
      "name": "context",
      "backend": "oceanbase",
      "distance_metric": "l2",
      "oceanbase": {
        "uri": "127.0.0.1:2881",
        "user": "root@test",
        "password": "",
        "db_name": "openviking"
      }
    }
  }
}
```

> **Note**: When using Docker OceanBase (slim mode), set `distance_metric` to `"l2"`. For OceanBase versions that support cosine, you can use `"cosine"`. See [Configuration](./01-configuration.md#vectordb) for full options.

OpenViking creates the collection and index on first write or server start; no manual table creation is required.

---

## Tutorial 1: Run OpenViking + OceanBase in 5 minutes

This tutorial mirrors the [Quick Start](../getting-started/02-quickstart.md): from starting OceanBase to performing one semantic search, with runnable steps and expected output.

### Step 1: Start OceanBase (Docker)

If you don’t have OceanBase installed, start a single-node instance with Docker (first run may pull the image; boot can take a few minutes):

```bash
# Start container (port 2881)
docker run -d -p 2881:2881 --name oceanbase-ce -e MODE=slim oceanbase/oceanbase-ce

# Wait until boot completes (look for "boot success!" in logs)
docker logs oceanbase-ce 2>&1 | tail -5

# Create database (root@test tenant)
docker exec -it oceanbase-ce mysql -h127.0.0.1 -P2881 -uroot@test -e "CREATE DATABASE IF NOT EXISTS openviking;"
```

If you use an existing OceanBase instance, ensure the database in `oceanbase.db_name` exists and that the host can connect with the given user and password.

### Step 2: Prepare configuration

Ensure `~/.openviking/ov.conf` includes **embedding** (same as [Quick Start](../getting-started/02-quickstart.md)) and **storage.vectordb (OceanBase)**. Minimal example (replace embedding api_key, model, etc. with your values):

```json
{
  "embedding": {
    "dense": {
      "api_base": "<your-embedding-endpoint>",
      "api_key": "<your-api-key>",
      "provider": "<volcengine|openai|...>",
      "dimension": 1024,
      "model": "<your-embedding-model>"
    }
  },
  "storage": {
    "vectordb": {
      "name": "context",
      "backend": "oceanbase",
      "distance_metric": "l2",
      "oceanbase": {
        "uri": "127.0.0.1:2881",
        "user": "root@test",
        "password": "",
        "db_name": "openviking"
      }
    }
  }
}
```

Full examples per provider are in [Configuration guide - Examples](./01-configuration.md#configuration-examples).

### Step 3: Create the example script

Create `example_oceanbase.py` (same flow as Quick Start, with vector store set to OceanBase):

```python
import openviking as ov

# Uses default config ~/.openviking/ov.conf (vectordb backend = oceanbase)
client = ov.OpenViking(path="./data")

try:
    client.initialize()

    # Add resource (URL, file, or directory)
    add_result = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"
    )
    root_uri = add_result["root_uri"]

    # List resource structure
    ls_result = client.ls(root_uri)
    print(f"Directory structure:\n{ls_result}\n")

    # Wait for semantic processing (vectors written to OceanBase)
    print("Waiting for semantic processing...")
    client.wait_processed()

    # Get abstract and overview
    abstract = client.abstract(root_uri)
    overview = client.overview(root_uri)
    print(f"Abstract:\n{abstract}\n\nOverview:\n{overview}\n")

    # Semantic search (vector search is performed against OceanBase)
    results = client.find("what is openviking", target_uri=root_uri)
    print("Search results:")
    for r in results.resources:
        print(f"  {r.uri}  (score: {r.score:.4f})")

    client.close()

except Exception as e:
    print(f"Error: {e}")
```

### Step 4: Run

```bash
python example_oceanbase.py
```

### Step 5: Expected output

```
Directory structure:
...

Waiting for semantic processing...
Abstract:
...

Overview:
...

Search results:
  viking://resources/... (score: 0.xxxx)
  ...
```

You have now run OpenViking with OceanBase as the vector store. Content is still stored in local AGFS (`path="./data"`); vectors and metadata are stored in OceanBase.

---

## Tutorial 2: Enterprise knowledge base (batch import + scoped search)

This tutorial shows: importing multiple resources, then searching by natural language within a URI prefix—suitable for internal docs, Wiki, or knowledge bases.

### Step 1: Prepare content and config

- OceanBase is running and the database exists (same as Tutorial 1).
- `ov.conf` has `storage.vectordb.backend` set to `oceanbase` and valid embedding and oceanbase connection settings.

### Step 2: Batch import and search

Create `example_knowledge_base.py`:

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

# Batch add resources (local directory or URL)
client.add_resource("/path/to/your/wiki")         # local directory
client.add_resource("https://example.com/doc.md") # or URL
client.wait_processed()

# Semantic search within a URI prefix (useful for multi-tenant or per-project scope)
results = client.find(
    "user login and authentication flow",
    target_uri="viking://resources/",
    limit=5
)

print(f"Total: {results.total} result(s)")
for ctx in results.resources:
    print(f"  {ctx.uri}")
    print(f"    score={ctx.score:.3f}  abstract={ctx.abstract[:80]}...")
    print()

client.close()
```

Replace `/path/to/your/wiki` with your doc root or remove that line to use only the URL. Then run:

```bash
python example_knowledge_base.py
```

Using `target_uri="viking://resources/"` limits search to the resource tree; different tenants can use different prefixes (e.g. `viking://resources/tenant-a/`) for logical isolation.

---

## Docker quick reference

| Step | Command |
|------|---------|
| Start OceanBase | `docker run -d -p 2881:2881 --name oceanbase-ce -e MODE=slim oceanbase/oceanbase-ce` |
| Wait for ready | `docker logs oceanbase-ce 2>&1 \| tail -1` until you see `boot success!` |
| Create DB | `docker exec -it oceanbase-ce mysql -h127.0.0.1 -P2881 -uroot@test -e "CREATE DATABASE IF NOT EXISTS openviking;"` |

Set `oceanbase.uri: "127.0.0.1:2881"` and `oceanbase.db_name: "openviking"` in config.

---

## Distance metrics and versions

| distance_metric | Description |
|----------------|-------------|
| `cosine` | Mapped to neg_ip where supported by your OceanBase version |
| `l2` / `ip` | Map directly to OceanBase L2 / IP distance |

If you see "this type of vector index distance algorithm is not supported", set `distance_metric` to `"l2"` (recommended for Docker slim mode).

---

## Running integration tests

OceanBase tests in this repo start OceanBase via Docker by default; no local OceanBase installation is required:

```bash
# Requires Docker; will pull and start oceanbase/oceanbase-ce
pytest tests/vectordb/test_oceanbase_live.py -v -s
# or
python -m unittest tests.vectordb.test_oceanbase_live -v
```

---

## See also

- [Configuration](./01-configuration.md) — `storage.vectordb` and common options for all backends
- [Storage architecture](../concepts/05-storage.md) — role of the vector store in OpenViking
- [Quick Start](../getting-started/02-quickstart.md) — get started with OpenViking (default local vector store)
- [OceanBase integration (Chinese)](../../zh/guides/06-oceanbase-integration.md) — 中文版本文档
