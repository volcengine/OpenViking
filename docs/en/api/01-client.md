# Client

The OpenViking client is the main entry point for all operations.

## Deployment Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Embedded** | Local storage, singleton instance | Development, small applications |
| **Service** | Remote storage services, multiple instances | Production, multi-process |

## API Reference

### OpenViking()

Create an OpenViking client instance.

**Signature**

```python
def __init__(
    self,
    path: Optional[str] = None,
    vectordb_url: Optional[str] = None,
    agfs_url: Optional[str] = None,
    user: Optional[str] = None,
    config: Optional[OpenVikingConfig] = None,
    **kwargs,
)
```

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| path | str | No* | None | Local storage path (embedded mode) |
| vectordb_url | str | No* | None | Remote VectorDB service URL (service mode) |
| agfs_url | str | No* | None | Remote AGFS service URL (service mode) |
| user | str | No | None | Username for session management |
| config | OpenVikingConfig | No | None | Advanced configuration object |

*Either `path` (embedded mode) or both `vectordb_url` and `agfs_url` (service mode) must be provided.

**Example: Embedded Mode**

```python
import openviking as ov

# Create client with local storage
client = ov.OpenViking(path="./my_data")
client.initialize()

# Use client...
results = client.find("test query")
print(f"Found {results.total} results")

client.close()
```

**Example: Service Mode**

```python
import openviking as ov

# Connect to remote services
client = ov.OpenViking(
    vectordb_url="http://vectordb.example.com:8000",
    agfs_url="http://agfs.example.com:8001",
)
client.initialize()

# Use client...
client.close()
```

**Example: Using Config Object**

```python
import openviking as ov
from openviking.utils.config import (
    OpenVikingConfig,
    StorageConfig,
    AGFSConfig,
    VectorDBBackendConfig
)

config = OpenVikingConfig(
    storage=StorageConfig(
        agfs=AGFSConfig(
            backend="local",
            path="./custom_data",
        ),
        vectordb=VectorDBBackendConfig(
            backend="local",
            path="./custom_data",
        )
    )
)

client = ov.OpenViking(config=config)
client.initialize()

# Use client...
client.close()
```

---

### initialize()

Initialize storage and indexes. Must be called before using other methods.

**Signature**

```python
def initialize(self) -> None
```

**Parameters**

None.

**Returns**

| Type | Description |
|------|-------------|
| None | - |

**Example**

```python
client = ov.OpenViking(path="./data")
client.initialize()  # Required before any operations
```

---

### close()

Close the client and release resources.

**Signature**

```python
def close(self) -> None
```

**Parameters**

None.

**Returns**

| Type | Description |
|------|-------------|
| None | - |

**Example**

```python
client = ov.OpenViking(path="./data")
client.initialize()

# ... use client ...

client.close()  # Clean up resources
```

---

### wait_processed()

Wait for all pending resource processing to complete.

**Signature**

```python
def wait_processed(self, timeout: float = None) -> Dict[str, Any]
```

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| timeout | float | No | None | Timeout in seconds |

**Returns**

| Type | Description |
|------|-------------|
| Dict[str, Any] | Processing status for each queue |

**Return Structure**

```python
{
    "queue_name": {
        "processed": 10,      # Number of processed items
        "error_count": 0,     # Number of errors
        "errors": []          # Error details
    }
}
```

**Example**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

# Add resources
client.add_resource("./docs/")

# Wait for processing to complete
status = client.wait_processed(timeout=60)
print(f"Processed: {status}")

client.close()
```

---

### reset()

Reset the singleton instance. Primarily used for testing.

**Signature**

```python
@classmethod
def reset(cls) -> None
```

**Parameters**

None.

**Returns**

| Type | Description |
|------|-------------|
| None | - |

**Example**

```python
# Reset singleton (for testing)
ov.OpenViking.reset()
```

---

### get_status()

Get system status including health status of all components.

**Signature**

```python
def get_status(self) -> SystemStatus
```

**Parameters**

None.

**Returns**

| Type | Description |
|------|-------------|
| SystemStatus | System status object |

**Return Structure**

```python
SystemStatus(
    is_healthy=True,                    # Overall system health
    components={                        # Component statuses
        "queue": ComponentStatus(...),
        "vikingdb": ComponentStatus(...),
        "vlm": ComponentStatus(...)
    },
    errors=[]                           # Error list
)
```

**Example**

```python
status = client.get_status()
print(f"System healthy: {status.is_healthy}")
print(f"Queue status: {status.components['queue'].is_healthy}")
```

---

### is_healthy()

Quick health check.

**Signature**

```python
def is_healthy(self) -> bool
```

**Parameters**

None.

**Returns**

| Type | Description |
|------|-------------|
| bool | True if all components are healthy, False otherwise |

**Example**

```python
if client.is_healthy():
    print("System OK")
else:
    status = client.get_status()
    print(f"Errors: {status.errors}")
```

---

## Singleton Behavior

In embedded mode, OpenViking uses singleton pattern:

```python
# These return the same instance
client1 = ov.OpenViking(path="./data")
client2 = ov.OpenViking(path="./data")
assert client1 is client2  # True
```

In service mode, each call creates a new instance:

```python
# These are different instances
client1 = ov.OpenViking(vectordb_url="...", agfs_url="...")
client2 = ov.OpenViking(vectordb_url="...", agfs_url="...")
assert client1 is not client2  # True
```

## Error Handling

```python
import openviking as ov

client = ov.OpenViking(path="./data")

try:
    client.initialize()
except RuntimeError as e:
    print(f"Initialization failed: {e}")

try:
    content = client.read("viking://invalid/path/")
except FileNotFoundError:
    print("Resource not found")

client.close()
```

## Related Documentation

- [Resources](resources.md) - Resource management
- [Retrieval](retrieval.md) - Search operations
- [Sessions](sessions.md) - Session management
- [Configuration](../configuration/configuration.md) - Configuration options
