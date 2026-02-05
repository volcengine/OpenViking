# 调试

OpenViking 提供调试和可观测性 API，用于监控系统健康状态和组件状态。

## API 参考

### observer

提供便捷访问组件状态的属性，返回 `ObserverService`。

**签名**

```python
@property
def observer(self) -> ObserverService
```

**返回值**

| 类型 | 说明 |
|------|------|
| ObserverService | 用于访问组件状态的服务 |

**示例**

```python
import openviking as ov

client = ov.OpenViking(path="./data")
client.initialize()

# 直接打印组件状态
print(client.observer.vikingdb)
# 输出:
# [vikingdb] (healthy)
# Collection  Index Count  Vector Count  Status
# context     1            55            OK
# TOTAL       1            55

client.close()
```

---

## ObserverService

`ObserverService` 提供访问各个组件状态的属性。

### queue

获取队列系统状态。

**签名**

```python
@property
def queue(self) -> ComponentStatus
```

**返回值**

| 类型 | 说明 |
|------|------|
| ComponentStatus | 队列系统状态 |

**示例**

```python
print(client.observer.queue)
# 输出:
# [queue] (healthy)
# Queue                 Pending  In Progress  Processed  Errors  Total
# Embedding             0        0            10         0       10
# Semantic              0        0            10         0       10
# TOTAL                 0        0            20         0       20
```

---

### vikingdb

获取 VikingDB 状态。

**签名**

```python
@property
def vikingdb(self) -> ComponentStatus
```

**返回值**

| 类型 | 说明 |
|------|------|
| ComponentStatus | VikingDB 状态 |

**示例**

```python
print(client.observer.vikingdb)
# 输出:
# [vikingdb] (healthy)
# Collection  Index Count  Vector Count  Status
# context     1            55            OK
# TOTAL       1            55

# 访问具体属性
print(client.observer.vikingdb.is_healthy)  # True
print(client.observer.vikingdb.status)      # 状态表格字符串
```

---

### vlm

获取 VLM（视觉语言模型）token 使用状态。

**签名**

```python
@property
def vlm(self) -> ComponentStatus
```

**返回值**

| 类型 | 说明 |
|------|------|
| ComponentStatus | VLM token 使用状态 |

**示例**

```python
print(client.observer.vlm)
# 输出:
# [vlm] (healthy)
# Model                          Provider      Prompt  Completion  Total  Last Updated
# doubao-1-5-vision-pro-32k      volcengine    1000    500         1500   2024-01-01 12:00:00
# TOTAL                                        1000    500         1500
```

---

### system

获取系统整体状态，包含所有组件。

**签名**

```python
@property
def system(self) -> SystemStatus
```

**返回值**

| 类型 | 说明 |
|------|------|
| SystemStatus | 系统整体状态 |

**示例**

```python
print(client.observer.system)
# 输出:
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

快速健康检查。

**签名**

```python
def is_healthy(self) -> bool
```

**返回值**

| 类型 | 说明 |
|------|------|
| bool | 所有组件健康返回 True |

**示例**

```python
if client.observer.is_healthy():
    print("系统正常")
else:
    print(client.observer.system)
```

---

## 数据结构

### ComponentStatus

单个组件的状态信息。

| 字段 | 类型 | 说明 |
|------|------|------|
| name | str | 组件名称 |
| is_healthy | bool | 组件是否健康 |
| has_errors | bool | 组件是否有错误 |
| status | str | 状态表格字符串 |

**字符串表示**

```python
print(component_status)
# 输出:
# [component_name] (healthy)
# 状态表格内容...
```

---

### SystemStatus

系统整体状态，包含所有组件。

| 字段 | 类型 | 说明 |
|------|------|------|
| is_healthy | bool | 整个系统是否健康 |
| components | Dict[str, ComponentStatus] | 各组件状态 |
| errors | List[str] | 错误信息列表 |

**字符串表示**

```python
print(system_status)
# 输出:
# [queue] (healthy)
# ...
#
# [vikingdb] (healthy)
# ...
#
# [system] (healthy)
# Errors: error1, error2  (如果有)
```
