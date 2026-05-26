# API Documentation Writing Guide

This document defines the unified structure and writing conventions for API module documentation in the `docs/en/api/` directory.

## Directory Structure

API documentation is organized by module, with one file per module, using a two-digit numerical prefix.

## Unified File Structure

Each API module documentation should follow the structure below:

````markdown
# Module Name

Brief introduction explaining the main features and purpose of this module.

## Optional Concepts/Introduction Section

(If needed, explain core concepts, workflows, etc., related to this module)

## API Reference

### API Method Name 1

#### 1. API Implementation Introduction

Explain the purpose of this API, point to the corresponding code entry, and briefly describe the principles and workflow.

**Code Entry**:
- `openviking/<module>/<file>.py:<ClassName>.<MethodName>` - Core implementation
- `openviking/server/routers/<router-file>.py` - HTTP router
- `openviking_cli/commands/<command-file>.py` - CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| <param-name> | <type> | <yes/no> | <default> | <detailed description> |
| ... | ... | ... | ... | ... |

**Optional Supplementary Section**

(If needed, explain special behaviors, considerations, usage scenarios, etc.)

#### 3. Usage Examples

**HTTP API**

```
<HTTP Method> <Path>
```

```bash
<curl example>
```

**Python SDK**

```python
<SDK call example>
```

**CLI**

```bash
<CLI command example>
```

**Response Example**

```json
<JSON response example>
```


#### 4. Response Example, Error Handling, and Exception Handling (Optional)

---

### API Method Name 2

(Repeat the above structure)

---

## Optional Additional Sections

## Related Documentation

- [Document Title](<relative-path>) - <brief description>
````

## Structure Details

### Title and Introduction

- The level 1 heading is the module name
- The introduction uses a paragraph to explain the purpose and main features of the module

### API Reference Section

Each API is organized in the following three parts:

#### 1. API Implementation Introduction

- Explain the purpose of this API
- Provide code entry paths for readers to reference the source code
- Briefly describe the implementation principles and processing workflow

**Code Entry Notes**:
- Core implementation: points to the main business logic code
- HTTP router: points to the FastAPI route definition
- CLI command: points to the CLI command implementation (if available)

#### 2. Interface and Parameter Description

- Parameter table: includes parameter name, type, required status, default value, description
- Supplementary notes (optional): special behaviors, considerations, usage scenarios, etc.

#### 3. Usage Examples

Provide in order:
- HTTP API (method + path + curl example)
- Python SDK example
- CLI example
- Response example

## Example: Complete API Documentation

````markdown
### add_resource()

#### 1. API Implementation Introduction

Add resources to the knowledge base, supporting various sources such as local files, directories, URLs, etc.

**Processing Workflow**:
1. Identify resource type (local file/directory/URL)
2. Call corresponding parser to parse content
3. Build directory tree and write to AGFS
4. Asynchronously generate L0/L1 semantic abstracts
5. Build vector index

**Code Entry**:
- `openviking/core/client.py:OpenViking.add_resource()` - SDK entry
- `openviking/resource/importer.py:ResourceImporter.import_resource()` - Core implementation
- `openviking/server/routers/resources.py` - HTTP router
- `openviking_cli/commands/resources.py` - CLI command

#### 2. Interface and Parameter Description

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| path | str | Yes | - | Local path, directory path, or URL |
| to | str | No | None | Target Viking URI (must be within the resources scope) |
| reason | str | No | "" | Reason for adding this resource |
| wait | bool | No | False | Whether to wait for semantic processing completion |

**Notes**

- SDK/CLI can directly pass local paths; raw HTTP requires `temp_upload` first
- When `to` is specified and the target already exists, an incremental update process is used

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/resources
```

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "path": "https://example.com/guide.md",
    "reason": "User guide documentation",
    "wait": true
  }'
```

**Python SDK**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

result = client.add_resource(
    "./documents/guide.md",
    reason="User guide documentation"
)
print(f"Added: {result['root_uri']}")

client.wait_processed()
```

**CLI**

```bash
openviking add-resource ./documents/guide.md --reason "User guide documentation" --wait
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "status": "success",
    "root_uri": "viking://resources/documents/guide.md",
    "source_path": "./documents/guide.md",
    "errors": []
  },
  "time": 0.123
}
```

---
````

## Documentation Maintenance Checklist

When adding or modifying API documentation, please check:

- [ ] Implementation introduction is clear and code entry paths are correct
- [ ] Parameter table is complete and accurate
- [ ] Example code is concise and runnable
- [ ] HTTP method and path are correct
- [ ] Response example matches actual output
