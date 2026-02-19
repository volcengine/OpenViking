# OpenViking Tests

Unit tests and integration tests for OpenViking.

## Directory Structure

```
tests/
├── conftest.py                      # Global fixtures
├── client/                          # Client API tests
├── server/                          # Server HTTP API & SDK tests
├── session/                         # Session API tests
├── vectordb/                        # VectorDB tests
├── misc/                            # Miscellaneous tests
├── engine/                          # C++ engine tests
└── integration/                     # End-to-end workflow tests
```

## Prerequisites

### Configuration

Set the `OPENVIKING_CONFIG_FILE` environment variable to point to your `ov.conf` file, which manages VLM, Embedding, and other model settings in one place:

```bash
export OPENVIKING_CONFIG_FILE="/path/to/ov.conf"
```

See [docs/en/guides/configuration.md](../docs/en/guides/configuration.md) for the config file format.

### Dependencies

```bash
pip install pytest pytest-asyncio
```

## Running Tests

### Python Tests

```bash
# Run all tests
pytest tests/client tests/server tests/session tests/vectordb tests/misc tests/integration -v

# Run with coverage
pytest tests/client tests/server tests/session tests/vectordb tests/misc tests/integration --cov=openviking --cov-report=html
```

### Running Specific Tests

```bash
# Run a specific test module
pytest tests/client/test_lifecycle.py -v

# Run a specific test class
pytest tests/client/test_lifecycle.py::TestClientInitialization -v

# Run a specific test function
pytest tests/client/test_lifecycle.py::TestClientInitialization::test_initialize_success -v

# Run tests matching a keyword
pytest tests/ -k "lifecycle" -v
pytest tests/ -k "initialize" -v

# Run tests with print output visible
pytest tests/client/test_lifecycle.py -v -s
```

### Common Test Scenarios

```bash
# Test client lifecycle (init, close, reset)
pytest tests/client/test_lifecycle.py -v

# Test resource add and processing
pytest tests/client/test_resource_management.py -v

# Test skill management
pytest tests/client/test_skill_management.py -v

# Test semantic search
pytest tests/client/test_search.py -v

# Test server HTTP API
pytest tests/server/ -v

# Test server SDK end-to-end
pytest tests/server/test_http_client_sdk.py -v

# Test session management
pytest tests/session/ -v

# Test vector database operations
pytest tests/vectordb/ -v

# Test full end-to-end workflow
pytest tests/integration/test_full_workflow.py -v
```

### C++ Engine Tests

```bash
cd tests/engine
mkdir build && cd build
cmake ..
make
./test_index_engine
```

## Test Modules

### client/

Tests for the OpenViking client API (`AsyncOpenViking` / `SyncOpenViking`).

| File | Description | Key Test Cases |
|------|-------------|----------------|
| `test_lifecycle.py` | Client lifecycle management | `initialize()` success and idempotency, `close()` cleanup, `reset()` singleton clearing, embedded mode singleton behavior |
| `test_resource_management.py` | Resource operations | `add_resource()` with sync/async modes, custom target URI, file not found handling; `wait_processed()` for single and batch resources |
| `test_skill_management.py` | Skill operations | `add_skill()` from SKILL.md file, YAML string, MCP tool dict, skill directory with auxiliary files; skill search |
| `test_filesystem.py` | Virtual filesystem | `ls()` with simple/recursive modes; `read()` file content; `abstract()` L0 summary; `overview()` L1 overview; `tree()` directory structure |
| `test_search.py` | Semantic search | `find()` fast vector search with limit/threshold/target_uri; `search()` with intent analysis and session context |
| `test_relations.py` | Resource linking | `link()` single/multiple URIs with reason; `unlink()` existing/nonexistent; `relations()` query |
| `test_file_operations.py` | File manipulation | `rm()` file/directory with recursive; `mv()` rename/move; `grep()` content search with case sensitivity; `glob()` pattern matching |
| `test_import_export.py` | Import/Export | `export_ovpack()` file/directory; `import_ovpack()` with force/vectorize options; roundtrip verification |

### server/

Tests for the OpenViking HTTP server API and AsyncHTTPClient SDK.

| File | Description | Key Test Cases |
|------|-------------|----------------|
| `test_server_health.py` | Server infrastructure | `/health` endpoint, `/api/v1/system/status`, `x-process-time` header, structured error responses, 404 for unknown routes |
| `test_auth.py` | API key authentication | Valid X-API-Key header, valid Bearer token, missing/wrong key returns 401, no auth when API key not configured, protected endpoints |
| `test_api_resources.py` | Resource management | `add_resource()` with/without wait, file not found, custom target URI, `wait_processed()` |
| `test_api_filesystem.py` | Filesystem endpoints | `ls` root/simple/recursive, `mkdir`, `tree`, `stat`, `rm`, `mv` |
| `test_api_content.py` | Content endpoints | `read`, `abstract`, `overview` |
| `test_api_search.py` | Search endpoints | `find` with target_uri/score_threshold, `search` with session, `grep` case-insensitive, `glob` |
| `test_api_sessions.py` | Session endpoints | Create, list, get, delete session; add messages; compress; extract |
| `test_api_relations.py` | Relations endpoints | Get relations, link single/multiple targets, unlink |
| `test_api_observer.py` | Observer endpoints | Queue, VikingDB, VLM, system observer status |
| `test_error_scenarios.py` | Error handling | Invalid JSON, missing fields, not found, wrong content type, invalid URI format |
| `test_http_client_sdk.py` | AsyncHTTPClient SDK E2E | Health, add resource, wait, ls, mkdir, tree, session lifecycle, find, full workflow (real HTTP server) |

### session/

Tests for session management (`Session` class).

| File | Description | Key Test Cases |
|------|-------------|----------------|
| `test_session_lifecycle.py` | Session creation and persistence | Create new session, create with custom ID, multiple sessions; `load()` existing session, load nonexistent |
| `test_session_messages.py` | Message management | `add_message()` user/assistant roles, TextPart/ContextPart/ToolPart; `update_tool_part()` status transitions (running→completed/failed) |
| `test_session_usage.py` | Usage tracking | `used()` record context URIs, record skill usage, record both; multiple usage records per session |
| `test_session_commit.py` | Session commit | `commit()` success status, memory extraction trigger, message archiving, empty session handling, multiple commits, usage record persistence |
| `test_session_context.py` | Context for search | `get_context_for_search()` with max_messages/max_archives limits; context after commit with archived summaries |

### vectordb/

Tests for the vector database layer (`VikingVectorIndex`).

| File | Description | Key Test Cases |
|------|-------------|----------------|
| `test_bytes_row.py` | Binary row storage | Row serialization/deserialization, binary data handling |
| `test_collection_large_scale.py` | Large scale operations | Collection creation with many vectors, batch insert performance, query latency at scale |
| `test_crash_recovery.py` | Crash recovery | WAL replay, index reconstruction, data integrity after crash |
| `test_filter_ops.py` | Filter operations | Metadata filtering (eq, ne, gt, lt, in, contains), compound filters, filter with vector search |
| `test_project_group.py` | Project/group management | Project isolation, group operations, cross-project queries |
| `test_pydantic_validation.py` | Data validation | Schema validation, type coercion, validation error handling |
| `reproduce_bugs.py` | Bug reproduction | Scripts for reproducing and verifying bug fixes |

### misc/

Miscellaneous tests.

| File | Description | Key Test Cases |
|------|-------------|----------------|
| `test_vikingdb_observer.py` | Database observer | State change notifications, observer registration/unregistration, event filtering |
| `test_code_parser.py` | Code repository parser | `ignore_dirs` compliance, `ignore_extensions` compliance, file type detection, symbolic link handling |
| `test_config_validation.py` | Configuration validation | Config schema validation, required fields, type checking |
| `test_debug_service.py` | Debug service | Debug endpoint tests, service diagnostics |
| `test_extract_zip.py` | Zip extraction security (Zip Slip) | Path traversal prevention (`../`), absolute path rejection, symlink entry filtering, backslash traversal, UNC path rejection, directory entry skipping, normal extraction |
| `test_mkdir.py` | VikingFS.mkdir() fix verification | mkdir calls agfs.mkdir, exist_ok=True skips existing, exist_ok=True creates missing, default creation, parent-before-target ordering |
| `test_port_check.py` | AGFS port check socket leak fix | Available port no leak, occupied port raises RuntimeError, occupied port no ResourceWarning |

### engine/

C++ tests for the index engine (GoogleTest).

| File | Description | Key Test Cases |
|------|-------------|----------------|
| `test_common.cpp` | Common utilities | Memory management, string operations, error handling |
| `test_index_engine.cpp` | Index engine | Vector indexing, similarity search, index persistence, concurrent access |

### integration/

End-to-end workflow tests.

| File | Description | Key Test Cases |
|------|-------------|----------------|
| `test_full_workflow.py` | Complete workflows | Resource→vectorize→search flow; Session conversation→commit→memory extraction; Export→delete→import roundtrip; Full E2E with all components |
