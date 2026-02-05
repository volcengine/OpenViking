# Storage Observers

## Overview

The `observers` module provides observability capabilities for the OpenViking storage system. Observers allow monitoring and reporting the status of various storage components in real-time.

## Architecture

### BaseObserver

All observers inherit from `BaseObserver`, which defines the common interface:

```python
from openviking.storage.observers import BaseObserver

class MyObserver(BaseObserver):
    def get_status_table(self) -> str:
        """Format status information as a string."""

    def is_healthy(self) -> bool:
        """Check if observed system is healthy."""

    def has_errors(self) -> bool:
        """Check if observed system has any errors."""
```

### Available Observers

#### QueueObserver

Monitors queue system status (Embedding, Semantic, and custom queues).

**Location:** `openviking/storage/observers/queue_observer.py`

**Usage:**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
print(client.observer.queue)
# Output:
#     Queue  Pending  In Progress  Processed  Errors  Total
# Embedding        5            2          100       0      107
#  Semantic        3            1           95       1       99
#     TOTAL        8            3          195       1      206
```

#### VikingDBObserver

Monitors VikingDB collection status (index count and vector count per collection).

**Location:** `openviking/storage/observers/vikingdb_observer.py`

**Usage:**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
print(client.observer.vikingdb)
# Output:
#    Collection  Index Count  Vector Count Status
#    context            1            69     OK
#     TOTAL             1            69
```

## Best Practices

1. **Use `get_status_table()` for human-readable output**: Provides clean, formatted tables
2. **Check the table output**: Look at "Errors" column to detect issues early
3. **Use with sync or async client**: Works seamlessly with both `OpenViking` and `AsyncOpenViking`

## See Also

- [QueueFS Documentation](../queuefs/README.md)
- [Storage Documentation](../../docs/OpenViking存储.md)
- [API Documentation](../../docs/OpenViking接口文档.md)
