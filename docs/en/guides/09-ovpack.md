# OVPack Import and Export

OVPack is OpenViking's packaging format for exporting/importing context subtrees (e.g., resources and memories) for backup, migration, and sharing.

## Quick Start

### Export Resources

Export OpenViking resources to an `.ovpack` file.

**CLI**
```bash
openviking export viking://resources/my-project/ ./exports/my-project.ovpack
```

**Python SDK**
```python
from openviking import AsyncOpenViking

async def export_example():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        exported_path = await client.export_ovpack(
            uri="viking://resources/my-project/",
            to="./exports/my-project.ovpack"
        )
        print(f"Export successful: {exported_path}")
    finally:
        await client.close()
```

### Import Resources

Import an `.ovpack` file into OpenViking.

**CLI**
```bash
# Basic import
openviking import ./exports/my-project.ovpack viking://resources/imported/

# Force overwrite
openviking import ./exports/my-project.ovpack viking://resources/imported/ --force

# Skip vectorization (faster)
openviking import ./exports/my-project.ovpack viking://resources/imported/ --no-vectorize
```

**Python SDK**
```python
from openviking import AsyncOpenViking

async def import_example():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        imported_uri = await client.import_ovpack(
            file_path="./exports/my-project.ovpack",
            parent="viking://resources/imported/",
            force=True,
            vectorize=True
        )
        print(f"Import successful: {imported_uri}")
        await client.wait_processed()
    finally:
        await client.close()
```

**HTTP API**
```bash
# Step 1: Upload the local ovpack file
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@./exports/my-project.ovpack' \
  | jq -r '.result.temp_file_id'
)

# Step 2: Import using temp_file_id
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://resources/imported/\",
    \"force\": true,
    \"vectorize\": true
  }"
```

## Memory Import and Export

OpenViking memories are stored under fixed directory structures:

- User memories: `viking://user/{user_space}/memories/`
- Agent memories: `viking://agent/{agent_space}/memories/`

When migrating memories with OVPack, you must import the `.ovpack` into the parent of the corresponding space (not an arbitrary directory). Otherwise you may end up with paths like `.../memories/memories/...`, and OpenViking will not be able to access and use them as memories.

### Export/Import User Memories (CLI)

```bash
# Export the whole user memories subtree
openviking export viking://user/default/memories/ ./exports/user-memories.ovpack

# Import into the user space root (imports to viking://user/default/memories/)
openviking import ./exports/user-memories.ovpack viking://user/default/ --force
```

### Export/Import Agent Memories (CLI)

```bash
openviking export viking://agent/default/memories/ ./exports/agent-memories.ovpack
openviking import ./exports/agent-memories.ovpack viking://agent/default/ --force
```

### Export/Import Memories (Python SDK)

```python
from openviking import AsyncOpenViking

async def export_import_user_memories():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        await client.export_ovpack(
            uri="viking://user/default/memories/",
            to="./exports/user-memories.ovpack",
        )

        await client.import_ovpack(
            file_path="./exports/user-memories.ovpack",
            parent="viking://user/default/",
            force=True,
            vectorize=True,
        )
    finally:
        await client.close()

async def export_import_agent_memories():
    client = AsyncOpenViking()
    await client.initialize()
    try:
        await client.export_ovpack(
            uri="viking://agent/default/memories/",
            to="./exports/agent-memories.ovpack",
        )
        await client.import_ovpack(
            file_path="./exports/agent-memories.ovpack",
            parent="viking://agent/default/",
            force=True,
            vectorize=True,
        )
    finally:
        await client.close()
```

### Export/Import Memories (HTTP API)

```bash
# Export user memories
curl -X POST http://localhost:1933/api/v1/pack/export \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://user/default/memories/",
    "to": "./exports/user-memories.ovpack"
  }'

# Import user memories (upload first, then import via temp_file_id)
TEMP_FILE_ID=$(
  curl -sS -X POST http://localhost:1933/api/v1/resources/temp_upload \
    -H "X-API-Key: your-key" \
    -F 'file=@./exports/user-memories.ovpack' \
  | jq -r '.result.temp_file_id'
)
curl -X POST http://localhost:1933/api/v1/pack/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d "{
    \"temp_file_id\": \"$TEMP_FILE_ID\",
    \"parent\": \"viking://user/default/\",
    \"force\": true,
    \"vectorize\": true
  }"
```

### Vectorization on Import

- Vectorization is enabled by default (useful for `find/search`).
- For faster restore, you can disable it and process later with `--no-vectorize`:

```bash
openviking import ./exports/user-memories.ovpack viking://user/default/ --force --no-vectorize
```

## Use Cases

### Resource Backup
```bash
DATE=$(date +%Y%m%d)
openviking export viking://resources/ ./backups/backup_${DATE}.ovpack
```

### Resource Migration
```bash
# Export on Machine A
openviking export viking://resources/my-project/ ./migration.ovpack

# Import on Machine B
openviking import ./migration.ovpack viking://resources/ --force
```

### Resource Sharing
```bash
# Export
openviking export viking://resources/shared-docs/ ./shared-docs.ovpack

# Recipient imports
openviking import ./shared-docs.ovpack viking://resources/team-shared/
```

## FAQ

**Q: Can I manually extract and view OVPack files?**
A: Yes! OVPack is a standard ZIP format and can be opened with any compression tool.

**Q: What if large OVPack imports are slow?**
A: Use `--no-vectorize` for fast import, then vectorize later.

**Q: How to handle duplicate resources during import?**
A: Use `--force` to overwrite existing resources.
