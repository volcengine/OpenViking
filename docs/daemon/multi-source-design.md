# Daemon 多源监听拓展设计

| 项目 | 信息 |
|-----|------|
| 状态 | `草案` |
| 创建日期 | 2026-06-16 |
| 基线分支 | `feature/active-daemon` |

---

## 概述

将 Active Daemon 从单一 Claude Code CLI 监听源拓展为支持多个可配置监听源的通用知识采集框架。核心变更包括：引入 Watcher 抽象层、多源配置模型、Claude Desktop 专用 Watcher、以及 Source-aware 的知识路由。

---

## 目录

- [背景与问题](#背景与问题)
- [目标与非目标](#目标与非目标)
- [架构设计](#架构设计)
- [核心抽象](#核心抽象)
- [配置设计](#配置设计)
- [各 Watcher 实现](#各-watcher-实现)
- [ETL 管道适配](#etl-管道适配)
- [路由与存储适配](#路由与存储适配)
- [文件变更清单](#文件变更清单)
- [分阶段实施](#分阶段实施)
- [风险与权衡](#风险与权衡)
- [验证方案](#验证方案)

---

## 背景与问题

### 当前架构

Daemon 目前仅支持一个监听源：Claude Code CLI 的 JSONL 会话日志（`~/.claude/projects/`）。架构是单 Watcher 硬编码：

```
DaemonService
    └── ClaudeCodeWatcher(watch_dir="~/.claude/projects")
            └── ClaudeCodeLogHandler → BatchBuffer → asyncio.Queue
                                                ↓
                                    BatchETLPipeline (Filter → Reconstruct → Extract → Dedup)
                                                ↓
                                    VikingStorageAdapter → viking://skills/claude_code/*
```

### 问题

| 问题 | 说明 |
|-----|------|
| 单源硬编码 | `DaemonService` 只实例化一个 `ClaudeCodeWatcher`，无法扩展 |
| 无抽象基类 | `watchers/__init__.py` 为空，没有 Watcher 协议定义 |
| 路由硬编码 | `KnowledgeRouter` 把 skills 统一路由到 `viking://skills/claude_code/`，不区分来源 |
| 模板硬编码 | `VikingStorageAdapter._format_skill()` 固定写 `Source: Claude Code session` |
| 配置扁平 | `DaemonConfig` 只有 `watch_dir`（单路径），无法表达多源 |
| Claude Desktop 未覆盖 | Claude Desktop 的会话存储在 IndexedDB 中（`%APPDATA%\Claude\`），当前完全忽略 |

### 动机

用户可能同时使用多个 AI 编码工具（Claude Code CLI、Claude Desktop、Cursor、Aider 等），每个工具产生不同格式的日志。Daemon 应能从所有这些源中提取知识，统一汇入 OpenViking。

---

## 目标与非目标

### 目标

1. **Watcher 抽象**：定义统一接口，新增监听源只需实现一个 Watcher 类
2. **多路径配置**：支持配置任意数量的监听源，每个源可指定独立路径和参数
3. **Claude Desktop 支持**：提取 Claude Desktop 的会话数据（JSONL + IndexedDB）
4. **Source-aware 路由**：知识路由和存储模板反映数据来源
5. **向后兼容**：单源配置自动迁移为多源格式，现有行为不变
6. **共享 ETL**：所有 Watcher 的输出进入同一个 ETL 管道，无需管道层改动

### 非目标

1. **非 Cursor/Aider/Copilot 支持** —— 本期只做 Claude 生态（CLI + Desktop），架构预留扩展点但不实现
2. **非实时处理** —— 保持批量处理模型（50 行 / 5 分钟阈值）
3. **不改变 LLM 提取逻辑** —— Prompt 和置信度阈值不变
4. **不引入新依赖** —— IndexedDB 提取如需要 plyvel，作为 optional dependency

---

## 架构设计

### 多源架构总览

```
DaemonService
    │
    ├── WatcherRegistry
    │       │
    │       ├── ClaudeCodeWatcher (JSONL, ~/.claude/projects/)
    │       │       └── ClaudeCodeLogHandler → normalize → ┐
    │       │                                                │
    │       ├── ClaudeDesktopWatcher (JSONL + IndexedDB)    │
    │       │       ├── DesktopJsonlHandler → normalize → ──┤
    │       │       └── IndexedDBPoller → normalize → ──────┤
    │       │                                                │
    │       └── [未来] GenericJsonlWatcher (自定义路径)      │
    │               └── GenericLogHandler → normalize → ────┤
    │                                                        │
    │                                        ┌───────────────┘
    │                                        ↓
    │                          asyncio.Queue[NormalizedEvent]
    │                                        ↓
    └──────────────────→ BatchETLPipeline (Filter → Reconstruct → Extract → Dedup)
                                        ↓
                              SourceAwareRouter → viking://skills/{source}/*
                                        ↓
                              VikingStorageAdapter (source-aware templates)
```

### 数据流对比

```
┌─ 当前 ───────────────────────────────────────────────────────┐
│                                                              │
│  ~/.claude/projects/**/*.jsonl                               │
│       ↓ ClaudeCodeWatcher                                    │
│  {role, content, type, timestamp}                            │
│       ↓ Queue                                                │
│  ETL → viking://skills/claude_code/xxx                       │
│                                                              │
└──────────────────────────────────────────────────────────────┘

┌─ 目标 ───────────────────────────────────────────────────────┐
│                                                              │
│  Source A: ~/.claude/projects/**/*.jsonl                     │
│       ↓ ClaudeCodeWatcher  →  normalize  → { ..., source:    │
│                                              "claude_code" } │
│  Source B: %APPDATA%/Claude/... (JSONL / IndexedDB)          │
│       ↓ ClaudeDesktopWatcher → normalize → { ..., source:    │
│                                              "claude_desktop"│
│                                            }                 │
│  Source C: /custom/path/**/*.jsonl (用户自定义)              │
│       ↓ GenericJsonlWatcher → normalize → { ..., source:     │
│                                              "custom_xxx" }  │
│       ↓ asyncio.Queue (共享)                                 │
│  ETL (unchanged)                                             │
│       ↓                                                      │
│  SourceAwareRouter → viking://skills/{source}/xxx            │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 核心抽象

### 1. NormalizedEvent — 统一事件模型

在现有 `BatchBuffer` 的 `Dict[str, Any]` 事件基础上，增加 `source` 字段：

```python
# openviking/daemon/models.py — 新增字段

@dataclass
class NormalizedEvent:
    """所有 Watcher 产出的标准化事件。"""
    role: str                    # "user" | "assistant"
    content: str                 # 消息正文
    type: str                    # "message"
    timestamp: Optional[str]     # ISO-8601
    session_id: Optional[str]    # 会话 ID
    project_name: Optional[str]  # 项目名
    source: str                  # 来源标识: "claude_code" | "claude_desktop" | "custom:xxx"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "type": self.type,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "project_name": self.project_name,
            "source": self.source,
        }
```

**为什么新增 NormalizedEvent 而不是复用 Dict**：当前 pipeline 里事件是裸 Dict，各 Watcher 自行构造。增加 `source` 字段后需要类型约束，避免拼写错误导致路由分错。同时 `to_dict()` 保证与现有 `BatchETLPipeline.process_batch(events: List[Dict])` 接口兼容。

### 2. BaseWatcher — Watcher 抽象基类

```python
# openviking/daemon/watchers/base.py — 新文件

from abc import ABC, abstractmethod
from typing import Callable, List, Dict, Optional

from openviking.daemon.cursor_manager import CursorManager


class BaseWatcher(ABC):
    """所有日志监听器的抽象基类。"""

    def __init__(
        self,
        paths: List[str],
        source_id: str,
        cursor_manager: CursorManager,
        batch_callback: Callable[[List[Dict]], None],
        batch_trigger_lines: int = 50,
        batch_trigger_seconds: int = 300,
    ):
        self.paths = paths
        self.source_id = source_id
        self.cursor_manager = cursor_manager
        self.batch_callback = batch_callback
        self.batch_trigger_lines = batch_trigger_lines
        self.batch_trigger_seconds = batch_trigger_seconds

    @abstractmethod
    def start(self):
        """启动监听。可启动一个或多个 watchdog Observer 线程。"""
        ...

    @abstractmethod
    def stop(self):
        """停止所有监听线程。"""
        ...

    @abstractmethod
    def flush(self):
        """强制刷新所有缓冲区。"""
        ...

    @property
    @abstractmethod
    def watcher_type(self) -> str:
        """Watcher 类型标识，用于配置解析。"""
        ...

    @property
    def status(self) -> Dict:
        """返回当前状态摘要，供 /daemon/status API 使用。"""
        return {
            "type": self.watcher_type,
            "source_id": self.source_id,
            "paths": self.paths,
        }
```

**设计要点**：
- `paths: List[str]` — 支持多个监听路径（一个 Watcher 可监听多个目录）
- `source_id: str` — 来源标识，注入到 NormalizedEvent.source 和路由路径
- `cursor_manager` 共享 — 所有 Watcher 共用同一个 SQLite 游标库，通过 file_path 主键天然隔离
- `batch_callback` 共享 — 所有 Watcher 向同一个 `asyncio.Queue` 投递事件

### 3. WatcherFactory — 配置驱动的实例化

```python
# openviking/daemon/watchers/factory.py — 新文件

from typing import Dict, List, Type

from openviking.daemon.watchers.base import BaseWatcher


class WatcherFactory:
    """根据配置创建 Watcher 实例。"""

    _registry: Dict[str, Type[BaseWatcher]] = {}

    @classmethod
    def register(cls, watcher_type: str, watcher_class: Type[BaseWatcher]):
        cls._registry[watcher_type] = watcher_class

    @classmethod
    def create(
        cls,
        source_config: Dict,
        cursor_manager,
        batch_callback,
        default_lines: int = 50,
        default_seconds: int = 300,
    ) -> BaseWatcher:
        watcher_type = source_config["type"]
        watcher_class = cls._registry.get(watcher_type)
        if not watcher_class:
            raise ValueError(
                f"Unknown watcher type: {watcher_type}. "
                f"Available: {list(cls._registry.keys())}"
            )
        return watcher_class(
            paths=source_config.get("paths", []),
            source_id=source_config.get("source_id", watcher_type),
            cursor_manager=cursor_manager,
            batch_callback=batch_callback,
            batch_trigger_lines=source_config.get(
                "batch_trigger_lines", default_lines
            ),
            batch_trigger_seconds=source_config.get(
                "batch_trigger_seconds", default_seconds
            ),
            # source-specific options
            options=source_config.get("options", {}),
        )
```

注册时机在 `DaemonService.start()` 中，通过 import 触发：

```python
# openviking/daemon/watchers/__init__.py

from openviking.daemon.watchers.factory import WatcherFactory
from openviking.daemon.watchers.claude_code_watcher import ClaudeCodeWatcher
from openviking.daemon.watchers.claude_desktop_watcher import ClaudeDesktopWatcher

WatcherFactory.register("claude_code", ClaudeCodeWatcher)
WatcherFactory.register("claude_desktop", ClaudeDesktopWatcher)
```

---

## 配置设计

### 新配置模型

```python
# openviking/server/config.py — DaemonConfig 重构

class SourceConfig(BaseModel):
    """单个监听源的配置。"""
    type: str                                    # "claude_code" | "claude_desktop"
    paths: List[str]                             # 监听路径列表
    source_id: Optional[str] = None              # 来源标识（默认 = type）
    enabled: bool = True
    batch_trigger_lines: Optional[int] = None    # 覆盖全局值
    batch_trigger_seconds: Optional[int] = None  # 覆盖全局值
    options: Dict[str, Any] = {}                 # Watcher 特定选项

class DaemonConfig(BaseModel):
    """Active Daemon 配置（多源）。"""
    enabled: bool = False
    sources: List[SourceConfig] = []             # 多源配置
    db_path: Optional[str] = None
    batch_trigger_lines: int = Field(50, gt=0)
    batch_trigger_seconds: int = Field(300, gt=0)

    @classmethod
    def from_env(cls) -> "DaemonConfig":
        ...  # 见下方兼容性设计
```

### ov.conf 示例

```json
{
  "daemon": {
    "enabled": true,
    "db_path": "~/.qoderworkcn/openviking/daemon_cursors.db",
    "batch_trigger_lines": 50,
    "batch_trigger_seconds": 300,
    "sources": [
      {
        "type": "claude_code",
        "paths": ["~/.claude/projects"],
        "source_id": "claude_code"
      },
      {
        "type": "claude_desktop",
        "paths": ["%APPDATA%/Claude"],
        "source_id": "claude_desktop",
        "options": {
          "enable_indexeddb": true,
          "indexeddb_path": "IndexedDB/https_claude.ai_0.indexeddb.leveldb"
        }
      },
      {
        "type": "claude_code",
        "paths": ["D:/Work/other-claude/projects", "E:/Shared/logs/claude"],
        "source_id": "claude_code_work"
      }
    ]
  }
}
```

### 向后兼容：单源 → 多源自动迁移

旧配置格式仍可使用：

```json
{
  "daemon": {
    "enabled": true,
    "watch_dir": "~/.claude/projects"
  }
}
```

迁移逻辑在 `DaemonConfig` 的 model_validator 中：

```python
class DaemonConfig(BaseModel):
    enabled: bool = False
    # 旧字段（兼容）
    watch_dir: Optional[str] = None
    # 新字段
    sources: List[SourceConfig] = []
    db_path: Optional[str] = None
    ...

    @model_validator(mode="after")
    def migrate_legacy_config(self):
        if self.watch_dir and not self.sources:
            self.sources = [
                SourceConfig(
                    type="claude_code",
                    paths=[self.watch_dir],
                    source_id="claude_code",
                )
            ]
        return self
```

### 环境变量兼容

```python
@classmethod
def from_env(cls) -> "DaemonConfig":
    import os

    # 单源（旧）: OV_DAEMON_WATCH_DIR
    watch_dir = os.getenv("OV_DAEMON_WATCH_DIR")

    # 多源（新）: OV_DAEMON_SOURCES (JSON 数组)
    sources_json = os.getenv("OV_DAEMON_SOURCES")

    sources = []
    if sources_json:
        sources = [SourceConfig(**s) for s in json.loads(sources_json)]
    elif watch_dir:
        sources = [SourceConfig(
            type="claude_code",
            paths=[watch_dir],
            source_id="claude_code",
        )]

    return cls(
        enabled=os.getenv("OV_DAEMON_ENABLED", "false").lower() == "true",
        sources=sources,
        db_path=os.getenv("OV_DAEMON_DB_PATH"),
        batch_trigger_lines=int(os.getenv("OV_DAEMON_BATCH_LINES", "50")),
        batch_trigger_seconds=int(os.getenv("OV_DAEMON_BATCH_SECONDS", "300")),
    )
```

---

## 各 Watcher 实现

### 1. ClaudeCodeWatcher（重构）

将现有 `ClaudeCodeWatcher` 从硬编码单路径重构为继承 `BaseWatcher`、支持多路径：

```python
# openviking/daemon/watchers/claude_code_watcher.py — 重构

class ClaudeCodeWatcher(BaseWatcher):
    """监听 Claude Code CLI 的 JSONL 会话日志。"""

    watcher_type = "claude_code"

    def __init__(self, paths, source_id, cursor_manager, batch_callback,
                 batch_trigger_lines=50, batch_trigger_seconds=300,
                 options=None):
        super().__init__(paths, source_id or "claude_code",
                         cursor_manager, batch_callback,
                         batch_trigger_lines, batch_trigger_seconds)
        self.observers: List[Observer] = []
        self.handlers: List[ClaudeCodeLogHandler] = []

    def start(self):
        for path in self.paths:
            expanded = os.path.expanduser(os.path.expandvars(path))
            handler = ClaudeCodeLogHandler(
                cursor_manager=self.cursor_manager,
                batch_callback=self.batch_callback,
                batch_trigger_lines=self.batch_trigger_lines,
                batch_trigger_seconds=self.batch_trigger_seconds,
                source_id=self.source_id,  # 注入到 NormalizedEvent
            )
            observer = Observer()
            observer.schedule(handler, expanded, recursive=True)
            observer.start()
            self.observers.append(observer)
            self.handlers.append(handler)
            logger.info("ClaudeCodeWatcher started on %s (source=%s)",
                        expanded, self.source_id)

    def stop(self):
        for obs in self.observers:
            obs.stop()
            obs.join()

    def flush(self):
        for h in self.handlers:
            h.force_flush()
```

**ClaudeCodeLogHandler 变更**：在 `_process_file` 产出的事件 Dict 中注入 `"source": self.source_id`。事件过滤逻辑（`_filter_events`）不变。

### 2. ClaudeDesktopWatcher（新实现）

Claude Desktop 的会话数据有两个存储位置：

| 位置 | 格式 | 内容 |
|-----|------|------|
| `%APPDATA%/Claude/logs/main.log` | 纯文本 | Electron 应用日志，包含 API 调用痕迹 |
| `%APPDATA%/Claude/IndexedDB/` | LevelDB | Chromium IndexedDB，存储 claude.ai Web 会话 |

**策略：JSONL 子路径扫描 + IndexedDB 轮询双通道**

```python
# openviking/daemon/watchers/claude_desktop_watcher.py — 新文件

class ClaudeDesktopWatcher(BaseWatcher):
    """
    监听 Claude Desktop 的会话数据。

    双通道：
    1. JSONL 扫描 — 扫描 paths 下的 .jsonl 文件（如果有外部导出）
    2. IndexedDB 轮询 — 读取 LevelDB 中的会话数据
    """

    watcher_type = "claude_desktop"

    def __init__(self, paths, source_id, cursor_manager, batch_callback,
                 batch_trigger_lines=50, batch_trigger_seconds=300,
                 options=None):
        super().__init__(paths, source_id or "claude_desktop",
                         cursor_manager, batch_callback,
                         batch_trigger_lines, batch_trigger_seconds)
        self.options = options or {}
        self.enable_indexeddb = self.options.get("enable_indexeddb", False)
        self.indexeddb_relpath = self.options.get(
            "indexeddb_path",
            "IndexedDB/https_claude.ai_0.indexeddb.leveldb"
        )
        self.observers: List[Observer] = []
        self.handlers: List[DesktopLogHandler] = []
        self._indexeddb_poller: Optional[IndexedDBPoller] = None

    def start(self):
        # 通道 1: JSONL 文件监听
        for path in self.paths:
            expanded = os.path.expanduser(os.path.expandvars(path))
            handler = DesktopLogHandler(
                cursor_manager=self.cursor_manager,
                batch_callback=self.batch_callback,
                source_id=self.source_id,
                ...
            )
            observer = Observer()
            observer.schedule(handler, expanded, recursive=True)
            observer.start()
            self.observers.append(observer)
            self.handlers.append(handler)

        # 通道 2: IndexedDB 轮询（可选）
        if self.enable_indexeddb:
            self._indexeddb_poller = IndexedDBPoller(
                base_paths=self.paths,
                relpath=self.indexeddb_relpath,
                cursor_manager=self.cursor_manager,
                batch_callback=self.batch_callback,
                source_id=self.source_id,
                poll_interval=self.batch_trigger_seconds,
            )
            self._indexeddb_poller.start()

    def stop(self):
        for obs in self.observers:
            obs.stop()
            obs.join()
        if self._indexeddb_poller:
            self._indexeddb_poller.stop()

    def flush(self):
        for h in self.handlers:
            h.force_flush()
        if self._indexeddb_poller:
            self._indexeddb_poller.force_flush()
```

#### DesktopLogHandler — 纯文本日志解析

`main.log` 是 Electron 应用日志，格式如下：

```
2026-03-23 12:34:38 [info] Starting app { ... }
2026-03-23 12:34:39 [info] API call to /v1/messages { model: "claude-sonnet-4-20250514" }
```

DesktopLogHandler 的策略是**仅提取 JSON 负载中包含对话内容的行**，过滤掉纯应用日志：

```python
class DesktopLogHandler(FileSystemEventHandler):
    """解析 Claude Desktop 的 main.log 文件。"""

    # 匹配带 JSON 负载的日志行
    LOG_PATTERN = re.compile(
        r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+\[(\w+)\]\s+(.+)$"
    )

    def on_modified(self, event):
        if event.is_directory or not event.src_path.endswith(".log"):
            return
        self._process_file(event.src_path)

    def _process_file(self, file_path):
        cursor = self.cursor_manager.get_cursor(file_path)
        current_size = os.path.getsize(file_path)
        if current_size <= cursor.last_position:
            return

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(cursor.last_position)
            for line in f:
                event = self._parse_log_line(line)
                if event:
                    self.buffer.add_line(event, len(line))

            new_position = f.tell()

        self.cursor_manager.update_cursor(file_path, new_position)
        self._check_batch_trigger()

    def _parse_log_line(self, line) -> Optional[Dict]:
        """
        从日志行中提取对话事件。
        仅保留包含 user/assistant 消息内容的行。
        """
        match = self.LOG_PATTERN.match(line.strip())
        if not match:
            return None

        timestamp, level, message = match.groups()

        # 尝试从 message 中提取 JSON 负载
        try:
            # 找到第一个 { 并解析到末尾
            json_start = message.index("{")
            payload = json.loads(message[json_start:])
        except (ValueError, json.JSONDecodeError):
            return None

        # 仅保留包含对话角色的事件
        role = payload.get("role")
        if role not in ("user", "assistant"):
            return None

        return {
            "role": role,
            "content": payload.get("content", ""),
            "type": "message",
            "timestamp": timestamp,
            "session_id": payload.get("sessionId"),
            "project_name": payload.get("projectName"),
            "source": self.source_id,
        }
```

#### IndexedDBPoller — LevelDB 轮询提取（可选）

```python
# openviking/daemon/watchers/indexeddb_poller.py — 新文件

class IndexedDBPoller:
    """
    定期轮询 Claude Desktop 的 IndexedDB LevelDB 存储。
    提取新增/更新的会话记录，转换为 NormalizedEvent。

    需要 plyvel 库（pip install plyvel）。
    """

    def __init__(self, base_paths, relpath, cursor_manager,
                 batch_callback, source_id, poll_interval=300):
        self.base_paths = base_paths
        self.relpath = relpath
        self.cursor_manager = cursor_manager
        self.batch_callback = batch_callback
        self.source_id = source_id
        self.poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self):
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True
        )
        self._thread.start()
        logger.info("IndexedDB poller started (interval=%ds)", self.poll_interval)

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _poll_loop(self):
        while not self._stop_event.is_set():
            for base_path in self.base_paths:
                db_path = os.path.join(
                    os.path.expanduser(os.path.expandvars(base_path)),
                    self.relpath,
                )
                if os.path.isdir(db_path):
                    try:
                        self._extract_from_leveldb(db_path)
                    except ImportError:
                        logger.error(
                            "plyvel not installed. "
                            "Run: pip install plyvel"
                        )
                        return  # 不重试
                    except Exception as e:
                        logger.error("IndexedDB extraction failed: %s", e)

            self._stop_event.wait(self.poll_interval)

    def _extract_from_leveldb(self, db_path: str):
        """
        从 LevelDB 中提取会话数据。

        IndexedDB 在 LevelDB 中的 key 格式：
          _file_version
          <database_id>-<object_store_id>-<index_id>-<key>

        Claude Desktop 的会话数据存储在 object store 中，
        key 前缀通常为 conversation 或 message 相关。

        由于 schema 未公开文档化，采用启发式提取：
        1. 遍历所有 key-value 对
        2. 尝试 JSON 解析 value
        3. 筛选包含 role=user/assistant 的记录
        """
        import plyvel

        cursor_key = f"indexeddb:{db_path}"
        cursor = self.cursor_manager.get_cursor(cursor_key)
        last_count = cursor.last_position  # 复用 last_position 存已处理记录数

        db = plyvel.DB(db_path, create_if_missing=False)
        try:
            events = []
            count = 0
            for key, value in db.iterator():
                count += 1
                if count <= last_count:
                    continue

                try:
                    record = json.loads(value)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                event = self._try_extract_event(record)
                if event:
                    events.append(event)

            if events:
                self.batch_callback(events)

            self.cursor_manager.update_cursor(cursor_key, count)
        finally:
            db.close()

    def _try_extract_event(self, record: Dict) -> Optional[Dict]:
        """启发式提取：从 JSON 记录中查找对话消息。"""
        # 场景 1: 记录本身是消息
        if record.get("role") in ("user", "assistant"):
            return {
                "role": record["role"],
                "content": record.get("content", ""),
                "type": "message",
                "timestamp": record.get("timestamp"),
                "session_id": record.get("conversationId",
                                         record.get("sessionId")),
                "project_name": None,
                "source": self.source_id,
            }

        # 场景 2: 记录包含 messages 数组
        messages = record.get("messages", [])
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") in ("user", "assistant"):
                return {
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                    "type": "message",
                    "timestamp": msg.get("timestamp"),
                    "session_id": record.get("id",
                                             record.get("conversationId")),
                    "project_name": None,
                    "source": self.source_id,
                }

        return None

    def force_flush(self):
        """触发一次即时提取。"""
        # 简化实现：设置 wait 为 0 让循环立即执行
        pass
```

### 3. GenericJsonlWatcher（预留，本期不实现）

```python
# openviking/daemon/watchers/generic_jsonl_watcher.py — 预留接口

class GenericJsonlWatcher(BaseWatcher):
    """
    通用 JSONL 文件监听器。
    支持自定义 JSON 字段映射（role_field, content_field 等）。
    本期不实现，仅注册到 Factory 以便未来扩展。
    """
    watcher_type = "generic_jsonl"
    # ...
```

---

## ETL 管道适配

### BatchETLPipeline — 最小改动

当前 `process_batch(events: List[Dict])` 接收裸 Dict 列表，不需要改接口。

需要改动的地方：

**1. ConversationTurn 增加 source 字段**

```python
# models.py

@dataclass
class ConversationTurn:
    user_prompt: str
    assistant_response: str
    session_id: Optional[str] = None
    project_name: Optional[str] = None
    timestamp: Optional[str] = None
    source: Optional[str] = None  # 新增
```

**2. ConversationReconstructor 传递 source**

```python
# conversation_reconstructor.py — reconstruct() 方法

def reconstruct(self, events: List[Dict]) -> List[ConversationTurn]:
    # ... 现有配对逻辑 ...
    turns.append(ConversationTurn(
        user_prompt=user_event["content"],
        assistant_response=assistant_event["content"],
        session_id=user_event.get("session_id"),
        project_name=user_event.get("project_name"),
        timestamp=user_event.get("timestamp"),
        source=user_event.get("source"),  # 新增
    ))
    return turns
```

**3. ExtractedKnowledge 增加 source 字段**

```python
# models.py

@dataclass
class ExtractedKnowledge:
    status: str
    category: str
    title: str
    content: str
    confidence: float = 0.0
    project_name: Optional[str] = None
    entity_links: List[str] = field(default_factory=list)
    actionable_steps: List[str] = field(default_factory=list)
    timestamp: Optional[str] = None
    source: Optional[str] = None  # 新增
```

**4. KnowledgeExtractor 传递 source**

`extract()` 方法在构造 `ExtractedKnowledge` 时，从 `ConversationTurn.source` 赋值到 `ExtractedKnowledge.source`。LLM prompt 不需要改。

---

## 路由与存储适配

### KnowledgeRouter — Source-aware 路由

```python
# knowledge_router.py — 重构 route()

class KnowledgeRouter:
    """Routes knowledge items to viking:// URIs based on category and source."""

    def route(self, knowledge: ExtractedKnowledge) -> Optional[str]:
        category = knowledge.category
        source = knowledge.source or "unknown"
        project_name = knowledge.project_name
        title = self._sanitize_filename(knowledge.title)

        if category == "skills":
            # viking://skills/{source}/{title}.md
            # 例: viking://skills/claude_code/fix-docker-networking.md
            #     viking://skills/claude_desktop/design-review-pattern.md
            return f"viking://skills/{self._sanitize_filename(source)}/{title}.md"

        elif category == "memories":
            if project_name:
                safe_project = self._sanitize_filename(project_name)
                return f"viking://memories/projects/{safe_project}/decisions.md"
            else:
                return f"viking://memories/global/{title}.md"

        elif category == "resources":
            entity_links = knowledge.entity_links
            tech_stack = (
                self._sanitize_filename(entity_links[0])
                if entity_links else "general"
            )
            return f"viking://resources/{tech_stack}/{title}.md"

        else:
            logger.warning("Unknown category: %s", category)
            return None
```

**变更影响**：原来 skills 统一写入 `viking://skills/claude_code/`，现在按 source 分目录。已有数据不受影响（路径不变），新数据会写入 `viking://skills/claude_desktop/` 等子目录。

### VikingStorageAdapter — Source-aware 模板

```python
# storage_adapter.py — 修改 _format_skill() 和 _format_resource()

@staticmethod
def _format_skill(knowledge: ExtractedKnowledge, timestamp: str) -> str:
    source_label = knowledge.source or "Claude Code"
    # 将 source_id 映射为人类可读名称
    SOURCE_LABELS = {
        "claude_code": "Claude Code CLI",
        "claude_desktop": "Claude Desktop",
    }
    label = SOURCE_LABELS.get(source_label, source_label)

    steps = ""
    if knowledge.actionable_steps:
        steps = "\n## Steps\n\n"
        for i, step in enumerate(knowledge.actionable_steps, 1):
            steps += f"{i}. {step}\n"
    else:
        steps = "\n## Steps\n\n(No specific steps)\n"

    return f"""# {knowledge.title}

**Extracted**: {timestamp}
**Source**: {label} session
**Confidence**: {knowledge.confidence}

## Content

{knowledge.content}
{steps}
---
*Auto-generated by OpenViking Active Daemon*
"""
```

`_format_resource()` 同理，将 `Source: Claude Code session` 替换为动态 source label。

### REST API 扩展

```python
# routers/daemon.py — 扩展 status 响应

@router.get("/api/v1/daemon/status")
async def get_daemon_status():
    daemon = get_daemon_service()
    if not daemon:
        return {"enabled": False}

    return {
        "enabled": True,
        "running": daemon.is_running,
        "sources": [w.status for w in daemon.watchers],  # 多源状态
        "db_path": daemon.db_path,
        "batch_trigger_lines": daemon.batch_trigger_lines,
        "batch_trigger_seconds": daemon.batch_trigger_seconds,
        "cursor_count": daemon.cursor_manager.count() if daemon.cursor_manager else 0,
    }
```

---

## DaemonService 重构

```python
# service.py — 多源版本

class DaemonService:

    def __init__(self, resource_service, config: DaemonConfig):
        self.resource_service = resource_service
        self.config = config
        self.db_path = config.db_path or str(
            Path.home() / ".qoderworkcn" / "openviking" / "daemon_cursors.db"
        )
        self.batch_trigger_lines = config.batch_trigger_lines
        self.batch_trigger_seconds = config.batch_trigger_seconds

        self.cursor_manager: Optional[CursorManager] = None
        self.watchers: List[BaseWatcher] = []   # 从单个变为列表
        self.etl_pipeline: Optional[BatchETLPipeline] = None
        self.storage_adapter: Optional[VikingStorageAdapter] = None

        self._running = False
        self._etl_task: Optional[asyncio.Task] = None
        self._batch_queue: asyncio.Queue = asyncio.Queue()

    async def start(self):
        logger.info("Starting OpenViking Active Daemon (multi-source)...")

        self.cursor_manager = CursorManager(self.db_path)
        self.etl_pipeline = BatchETLPipeline()
        self.storage_adapter = VikingStorageAdapter(self.resource_service)

        # 确保 Watcher 类型已注册
        import openviking.daemon.watchers  # noqa: F401 触发注册

        # 为每个 source 创建 Watcher
        for source_config in self.config.sources:
            if not source_config.enabled:
                continue

            # 展开路径中的环境变量和 ~
            expanded_paths = [
                os.path.expanduser(os.path.expandvars(p))
                for p in source_config.paths
            ]

            # 确保至少一个路径存在
            for p in expanded_paths:
                Path(p).mkdir(parents=True, exist_ok=True)

            watcher = WatcherFactory.create(
                source_config=source_config.model_dump(),
                cursor_manager=self.cursor_manager,
                batch_callback=self._enqueue_batch,
                default_lines=self.batch_trigger_lines,
                default_seconds=self.batch_trigger_seconds,
            )
            watcher.start()
            self.watchers.append(watcher)

        self._etl_task = asyncio.create_task(self._etl_loop())
        self._running = True
        logger.info("Daemon started with %d source(s)", len(self.watchers))

    async def stop(self):
        self._running = False
        for watcher in self.watchers:
            watcher.stop()
        if self._etl_task:
            await self._batch_queue.put(None)
            try:
                await asyncio.wait_for(self._etl_task, timeout=10)
            except asyncio.TimeoutError:
                self._etl_task.cancel()
        logger.info("Daemon stopped")

    async def flush(self):
        for watcher in self.watchers:
            watcher.flush()
```

---

## 文件变更清单

| 文件路径 | 操作 | 说明 |
|---------|------|------|
| `openviking/daemon/models.py` | 修改 | 新增 `NormalizedEvent`，`ConversationTurn` 和 `ExtractedKnowledge` 增加 `source` 字段 |
| `openviking/daemon/watchers/base.py` | **新建** | `BaseWatcher` 抽象基类 |
| `openviking/daemon/watchers/factory.py` | **新建** | `WatcherFactory` 配置驱动实例化 |
| `openviking/daemon/watchers/__init__.py` | 修改 | 注册所有 Watcher 类型 |
| `openviking/daemon/watchers/claude_code_watcher.py` | 修改 | 继承 `BaseWatcher`，支持多路径，注入 `source_id` |
| `openviking/daemon/watchers/claude_desktop_watcher.py` | **新建** | `ClaudeDesktopWatcher` + `DesktopLogHandler` |
| `openviking/daemon/watchers/indexeddb_poller.py` | **新建** | IndexedDB LevelDB 轮询提取器（可选） |
| `openviking/daemon/service.py` | 修改 | 多 Watcher 管理，配置驱动初始化 |
| `openviking/daemon/conversation_reconstructor.py` | 修改 | 传递 `source` 到 `ConversationTurn` |
| `openviking/daemon/knowledge_extractor.py` | 修改 | 传递 `source` 到 `ExtractedKnowledge` |
| `openviking/daemon/knowledge_router.py` | 修改 | Source-aware URI 路由 |
| `openviking/daemon/storage_adapter.py` | 修改 | Source-aware Markdown 模板 |
| `openviking/server/config.py` | 修改 | 新增 `SourceConfig`，重构 `DaemonConfig`，兼容迁移 |
| `openviking/server/routers/daemon.py` | 修改 | 扩展 status API 返回多源状态 |
| `openviking/server/app.py` | 修改 | 适配新的 `DaemonService(config=...)` 签名 |
| `tests/daemon/test_multi_source.py` | **新建** | 多源配置、Factory、路由测试 |
| `tests/daemon/test_claude_desktop_watcher.py` | **新建** | Desktop Watcher 单元测试 |
| `tests/daemon/test_indexeddb_poller.py` | **新建** | IndexedDB 提取测试（mock LevelDB） |
| `docs/daemon/configuration.md` | 修改 | 新增多源配置文档 |

---

## 分阶段实施

### Phase 1: 抽象层与多源框架（Day 1-2）

| 任务 | 文件 | 验收标准 |
|-----|------|---------|
| 创建 `BaseWatcher` ABC | `watchers/base.py` | 抽象方法定义完整，`status` 属性可用 |
| 创建 `WatcherFactory` | `watchers/factory.py` | `register()` + `create()` 工作正常 |
| 重构 `ClaudeCodeWatcher` | `watchers/claude_code_watcher.py` | 继承 `BaseWatcher`，支持 `paths: List[str]`，注入 `source`，现有测试仍通过 |
| 新增 `NormalizedEvent` | `models.py` | `to_dict()` 输出与旧 Dict 格式兼容 |
| 重构 `DaemonConfig` | `server/config.py` | 旧配置自动迁移，新配置正常解析 |
| 重构 `DaemonService` | `service.py` | 多 Watcher 列表管理，ETL 循环不变 |
| Source-aware 路由 | `knowledge_router.py` | skills 路由包含 source 前缀 |
| Source-aware 模板 | `storage_adapter.py` | Markdown 模板显示正确 source |
| 传递 source 到 Turn/Knowledge | `reconstructor.py`, `extractor.py` | `source` 字段端到端传递 |
| 集成测试 | `tests/daemon/test_multi_source.py` | 模拟双源配置，验证两条数据链路 |

**Phase 1 里程碑**：配置多个 `claude_code` 类型源（不同路径），两个 Watcher 各自监听、共享 ETL、路由到不同 `viking://skills/{source_id}/` 路径。

### Phase 2: Claude Desktop Watcher（Day 3-4）

| 任务 | 文件 | 验收标准 |
|-----|------|---------|
| 实现 `ClaudeDesktopWatcher` | `watchers/claude_desktop_watcher.py` | JSONL 通道可用 |
| 实现 `DesktopLogHandler` | 同上 | 正确解析 `main.log` 格式，过滤非对话行 |
| 单元测试 | `tests/daemon/test_claude_desktop_watcher.py` | 覆盖日志解析、过滤、缓冲触发 |
| REST API 扩展 | `routers/daemon.py` | `/daemon/status` 返回多源状态 |
| 文档更新 | `docs/daemon/configuration.md` | 多源配置示例完整 |

**Phase 2 里程碑**：配置 `claude_desktop` 源后，能解析 Desktop 日志文件并提取知识。

### Phase 3: IndexedDB 提取（Day 5，可选）

| 任务 | 文件 | 验收标准 |
|-----|------|---------|
| 实现 `IndexedDBPoller` | `watchers/indexeddb_poller.py` | 能从 LevelDB 提取会话 |
| plyvel 可选依赖 | `pyproject.toml` | 未安装时优雅降级（日志警告） |
| 单元测试 | `tests/daemon/test_indexeddb_poller.py` | mock LevelDB 数据，验证提取逻辑 |
| 端到端验证 | — | 实际 Claude Desktop IndexedDB 数据提取成功 |

**Phase 3 里程碑**：Claude Desktop 的 IndexedDB 会话数据可被自动提取和入库。

---

## 风险与权衡

| 风险 | 影响 | 缓解方案 |
|-----|------|---------|
| Claude Desktop IndexedDB schema 未公开 | 提取逻辑可能在版本更新后失效 | 启发式提取 + 异常兜底 + 日志告警 |
| `main.log` 不含完整对话内容 | Desktop Watcher 可能提取不到知识 | IndexedDB 作为补充通道；纯 JSONL 作为 fallback |
| plyvel 在 Windows 上安装困难 | IndexedDB 提取不可用 | 设为 optional dependency，未安装时跳过 |
| 多 Watcher 共享 CursorManager 的并发写入 | SQLite 锁争用 | CursorManager 已使用线程安全连接；每个 Watcher 操作不同 file_path 主键 |
| 多源事件到达顺序不确定 | 同一会话的 user/assistant 可能被分到不同 batch | ConversationReconstructor 已有排序逻辑；共享 Queue 保证 FIFO |
| source_id 冲突（两个源用同一 ID） | 路由路径碰撞 | 配置校验：启动时检查 source_id 唯一性 |
| 旧配置迁移出错 | 现有部署启动失败 | `model_validator` 中 try-except 兜底，日志警告 |

---

## 验证方案

### 单元测试

```bash
# Phase 1 验收
python -m pytest tests/daemon/test_multi_source.py -v
# 预期：多源 Factory 创建、配置迁移、Source-aware 路由 全部通过

# Phase 2 验收
python -m pytest tests/daemon/test_claude_desktop_watcher.py -v
# 预期：日志解析、过滤规则、缓冲触发 全部通过

# 回归测试
python -m pytest tests/daemon/ -v
# 预期：所有现有测试（test_claude_code_watcher, test_cursor_manager, test_integration）仍通过
```

### 集成测试场景

**场景 A：双 Claude Code 源**

```json
{
  "sources": [
    {"type": "claude_code", "paths": ["~/.claude/projects"], "source_id": "cc_main"},
    {"type": "claude_code", "paths": ["D:/Work/claude-logs"], "source_id": "cc_work"}
  ]
}
```

验证：两个目录的 JSONL 文件各自被监听，知识分别路由到 `viking://skills/cc_main/` 和 `viking://skills/cc_work/`。

**场景 B：CLI + Desktop 混合**

```json
{
  "sources": [
    {"type": "claude_code", "paths": ["~/.claude/projects"]},
    {"type": "claude_desktop", "paths": ["%APPDATA%/Claude"]}
  ]
}
```

验证：两个源各自正常采集，CLI 源走 JSONL 通道，Desktop 源走 log 解析通道，两者共享 ETL 管道。

### 手动验证步骤

1. 启动 Daemon：`openviking-server --with-daemon --config test-config.json`
2. 向 `~/.claude/projects` 下写入测试 JSONL → 观察 `cc_main` 路由
3. 向 `%APPDATA%/Claude/logs/` 下追加模拟 `main.log` 行 → 观察 `claude_desktop` 路由
4. 调用 `GET /api/v1/daemon/status` → 确认两个 source 状态正确
5. 检查 `viking://skills/` 下的目录结构 → 确认按 source 分目录

---

## 一句话总结

通过引入 BaseWatcher 抽象层 + WatcherFactory + SourceConfig 配置模型，将 Daemon 从单源硬编码升级为多源可配置框架，在保持 ETL 管道不变的前提下支持 Claude Code CLI、Claude Desktop、以及未来任意 JSONL 日志源的知识采集。
