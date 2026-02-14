# Cron 模块设计

## 概述

Cron 模块提供定时任务调度功能，支持 cron 表达式、固定时间执行和间隔执行。

## 模块结构

```
vikingbot/cron/
├── __init__.py
├── service.py     # Cron 服务
└── types.py       # Cron 类型定义
```

## 核心组件

### 1. CronService (定时任务服务)

**文件**: `vikingbot/cron/service.py`

**职责**:
- 管理定时任务
- 支持 cron 表达式、at、every 调度
- 任务持久化
- 定时器管理

**接口**:

```python
class CronService:
    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None
    ):
        """
        初始化 Cron 服务
        
        Args:
            store_path: 任务存储文件路径
            on_job: 任务执行回调函数
        """
        pass
    
    async def start(self) -> None:
        """启动 Cron 服务"""
        pass
    
    def stop(self) -> None:
        """停止 Cron 服务"""
        pass
    
    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """
        列出所有任务
        
        Args:
            include_disabled: 是否包含禁用的任务
        """
        pass
    
    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        """
        添加新任务
        
        Args:
            name: 任务名称
            schedule: 调度配置
            message: 要发送的消息
            deliver: 是否投递到聊天通道
            channel: 目标通道
            to: 目标用户 ID
            delete_after_run: 执行后是否删除
            
        Returns:
            创建的任务
        """
        pass
    
    def remove_job(self, job_id: str) -> bool:
        """
        删除任务
        
        Args:
            job_id: 任务 ID
            
        Returns:
            True 如果已删除
        """
        pass
    
    def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
        """
        启用或禁用任务
        
        Args:
            job_id: 任务 ID
            enabled: 是否启用
            
        Returns:
            更新后的任务或 None
        """
        pass
    
    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """
        手动运行任务
        
        Args:
            job_id: 任务 ID
            force: 是否强制运行（即使禁用）
            
        Returns:
            True 如果已运行
        """
        pass
    
    def status(self) -> dict:
        """
        获取服务状态
        
        Returns:
            状态字典
        """
        pass
```

### 2. Cron Types (定时任务类型)

**文件**: `vikingbot/cron/types.py`

**职责**:
- 定义定时任务数据结构

**数据类**:

#### CronSchedule

**描述**: 调度配置

```python
@dataclass
class CronSchedule:
    kind: str  # "at", "every", "cron"
    at_ms: int | None = None  # "at" 调度的执行时间（毫秒）
    every_ms: int | None = None  # "every" 调度的间隔（毫秒）
    expr: str | None = None  # cron 表达式
    tz: str | None = None  # 时区
```

#### CronPayload

**描述**: 任务负载数据

```python
@dataclass
class CronPayload:
    kind: str  # 负载类型
    message: str  # 要发送的消息
    deliver: bool = False  # 是否投递到聊天通道
    channel: str | None = None  # 目标通道
    to: str | None = None  # 目标用户 ID
```

#### CronJobState

**描述**: 任务状态

```python
@dataclass
class CronJobState:
    next_run_at_ms: int | None = None  # 下次执行时间（毫秒）
    last_run_at_ms: int | None = None  # 上次执行时间（毫秒）
    last_status: str | None = None  # 上次执行状态（ok/error）
    last_error: str | None = None  # 上次错误信息
```

#### CronJob

**描述**: 定时任务

```python
@dataclass
class CronJob:
    id: str  # 任务唯一 ID
    name: str  # 任务名称
    enabled: bool = True  # 是否启用
    schedule: CronSchedule  # 调度配置
    payload: CronPayload  # 任务负载数据
    state: CronJobState  # 任务状态
    created_at_ms: int = 0  # 创建时间（毫秒）
    updated_at_ms: int = 0  # 更新时间（毫秒）
    delete_after_run: bool = False  # 执行后是否删除
```

## 调度类型

### 1. Cron 表达式

使用标准的 cron 表达式：

```
* * * * *  # 每小时执行
0 9 * * *  # 每天早上 9 点执行
0 */2 * *  # 每两小时执行
```

### 2. At 调度

在指定时间执行一次：

```python
CronSchedule(
    kind="at",
    at_ms=17394768000000,  # 2026-02-13 12:00:00
)
```

### 3. Every 调度

按固定间隔执行：

```python
CronSchedule(
    kind="every",
    every_ms=3600000,  # 每小时执行一次
)
```

## 任务执行流程

### 1. 添加任务

```python
# 创建任务
job = cron_service.add_job(
    name="daily_reminder",
    schedule=CronSchedule(kind="cron", expr="0 9 * * *"),
    message="Good morning!",
    deliver=True,
    channel="telegram",
    to="user_id"
)
```

### 2. 任务执行

1. Cron 服务检查到期的任务
2. 调用 `on_job` 回调执行任务
3. 更新任务状态（last_run_at_ms, last_status, last_error）
4. 计算下次执行时间
5. 对于一次性任务，禁用或删除

### 3. 手动运行

```python
# 立即运行任务
await cron_service.run_job(job_id="abc123", force=True)
```

## 存储格式

### Cron Store

**文件**: `~/.vikingbot/cron.json`

**格式**:

```json
{
  "version": 1,
  "jobs": [
    {
      "id": "abc12345",
      "name": "daily_reminder",
      "enabled": true,
      "schedule": {
        "kind": "cron",
        "atMs": null,
        "everyMs": null,
        "expr": "0 9 * * *",
        "tz": "America/Los_Angeles"
      },
      "payload": {
        "kind": "agent_turn",
        "message": "Good morning!",
        "deliver": true,
        "channel": "telegram",
        "to": "123456789"
      },
      "state": {
        "nextRunAtMs": 17394768000000,
        "lastRunAtMs": 1739391600000,
        "lastStatus": "ok",
        "lastError": null
      },
      "createdAtMs": 1739305200000,
      "updatedAtMs": 1739391600000,
      "deleteAfterRun": false
    }
  ]
}
```

## 设计模式

### 定时器管理

- 使用 asyncio 创建定时任务
- 动态调整下次执行时间
- 支持任务启停用

### 任务持久化

- JSON 格式存储在 `~/.vikingbot/cron.json`
- 每次修改后立即保存
- 版本字段支持未来迁移

### 回调机制

- `on_job` 回调由 AgentLoop 提供
- 支持任务执行后返回响应消息

## 扩展点

### 添加自定义任务类型

可以扩展 `CronSchedule` 支持新的调度类型。

### 自定义任务执行

可以提供自定义的 `on_job` 回调实现特殊任务逻辑。

## 错误处理

### 任务执行失败

- 记录错误信息到 `last_error`
- 设置 `last_status` 为 "error"
- 不影响其他任务

### 调度解析失败

- 使用默认值或禁用任务
- 记录警告

## 性能优化

### 定时器复用

- 使用单个定时器管理所有任务
- 动态计算最近的执行时间

### 惰性保存

- 仅在任务状态变化时保存
- 使用异步写入避免阻塞

### 批量操作

- 支持批量添加、删除、启用/禁用任务

## 安全考虑

### 任务权限

- 任务投递到聊天通道时受 `allow_from` 控制
- 仅允许的用户可以接收任务消息

### 时间安全

- 使用毫秒时间戳避免时区问题
- 支持时区配置（CronSchedule.tz）

### 数据验证

- 使用 Pydantic 验证任务数据
- 防止无效数据损坏存储
