# Debug

OpenViking provides debug and observability APIs for monitoring system health and component status.

## API Reference

### observer

Property that provides convenient access to component status through `ObserverService`.

**Signature**

```python
@property
def observer(self) -> ObserverService
```

**Returns**

| Type | Description |
|------|-------------|
| ObserverService | Service for accessing component status |

**Example**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

# Print component status directly
print(client.observer.vikingdb)
# Output:
# [vikingdb] (healthy)
# Collection  Index Count  Vector Count  Status
# context     1            55            OK
# TOTAL       1            55

client.close()
```

---

## ObserverService

`ObserverService` provides properties for accessing individual component status.

### queue

Get queue system status.

**Signature**

```python
@property
def queue(self) -> ComponentStatus
```

**Returns**

| Type | Description |
|------|-------------|
| ComponentStatus | Queue system status |

**Example**

```python
print(client.observer.queue)
# Output:
# [queue] (healthy)
# Queue                 Pending  In Progress  Processed  Errors  Total
# Embedding             0        0            10         0       10
# Semantic              0        0            10         0       10
# TOTAL                 0        0            20         0       20
```

---

### vikingdb

Get VikingDB status.

**Signature**

```python
@property
def vikingdb(self) -> ComponentStatus
```

**Returns**

| Type | Description |
|------|-------------|
| ComponentStatus | VikingDB status |

**Example**

```python
print(client.observer.vikingdb)
# Output:
# [vikingdb] (healthy)
# Collection  Index Count  Vector Count  Status
# context     1            55            OK
# TOTAL       1            55

# Access specific properties
print(client.observer.vikingdb.is_healthy)  # True
print(client.observer.vikingdb.status)      # Status table string
```

---

### vlm

Get VLM (Vision Language Model) token usage status.

**Signature**

```python
@property
def vlm(self) -> ComponentStatus
```

**Returns**

| Type | Description |
|------|-------------|
| ComponentStatus | VLM token usage status |

**Example**

```python
print(client.observer.vlm)
# Output:
# [vlm] (healthy)
# Model                          Provider      Prompt  Completion  Total  Last Updated
# doubao-1-5-vision-pro-32k      volcengine    1000    500         1500   2024-01-01 12:00:00
# TOTAL                                        1000    500         1500
```

---

### system

Get overall system status including all components.

**Signature**

```python
@property
def system(self) -> SystemStatus
```

**Returns**

| Type | Description |
|------|-------------|
| SystemStatus | Overall system status |

**Example**

```python
print(client.observer.system)
# Output:
# [queue] (healthy)
# ...
#
# [vikingdb] (healthy)
# ...
#
# [vlm] (healthy)
# ...
#
# [system] (healthy)
```

---

### is_healthy()

Quick health check for the entire system.

**Signature**

```python
def is_healthy(self) -> bool
```

**Returns**

| Type | Description |
|------|-------------|
| bool | True if all components are healthy |

**Example**

```python
if client.observer.is_healthy():
    print("System OK")
else:
    print(client.observer.system)
```

---

## Data Structures

### ComponentStatus

Status information for a single component.

| Field | Type | Description |
|-------|------|-------------|
| name | str | Component name |
| is_healthy | bool | Whether the component is healthy |
| has_errors | bool | Whether the component has errors |
| status | str | Status table string |

**String Representation**

```python
print(component_status)
# Output:
# [component_name] (healthy)
# Status table content...
```

---

### SystemStatus

Overall system status including all components.

| Field | Type | Description |
|-------|------|-------------|
| is_healthy | bool | Whether the entire system is healthy |
| components | Dict[str, ComponentStatus] | Status of each component |
| errors | List[str] | List of error messages |

**String Representation**

```python
print(system_status)
# Output:
# [queue] (healthy)
# ...
#
# [vikingdb] (healthy)
# ...
#
# [system] (healthy)
# Errors: error1, error2  (if any)
```
