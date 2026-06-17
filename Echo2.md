# Echo II: Multi-Source Watcher Extension

> 从单一 Claude Code 监听到多工具 AI 编码助手日志的统一监听与知识提取框架

---

## 一、动机与背景

Active Daemon 的 v1 实现仅支持 Claude Code JSONL 日志监听。然而现实中，开发者的日常工具栈远不止于此：

- **Claude Code CLI** — JSONL 日志（已实现）
- **Cursor** — 日志文件 + SQLite 数据库
- **Aider** — `.aider.chat.history.md` Markdown 文件
- **GitHub Copilot** — VS Code workspace 日志
- **Continue.dev** — `~/.continue/` 下的 JSON 日志
- **Windsurf / Cascade** — 类 Cursor 的日志目录

Echo.md 原文已明确提到需要支持 Cursor（SQLite polling）和 OpenCode（rotating text logs），但 v1 仅完成了最简单的 Claude Code 路径。本次扩展的核心目标是：**将 Daemon 从单源监听器升级为多源监听框架**，使开发者可以自由配置任意数量的工具监听路径。

---

## 二、目标工具可行性分析

| 工具 | 存储格式 | 驱动模式 | 可行性 | 优先级 |
|------|----------|----------|--------|--------|
| **Claude Code** | JSONL append-only | 文件增量监听 | ★★★★★ 已实现 | P0 |
| **Aider** | Markdown `.aider.chat.history.md` | 文件增量监听 | ★★★★☆ | P1 |
| **Cursor** | SQLite `state.vscdb` + 日志 | DB polling + 文件监听 | ★★★☆☆ | P1 |
| **Continue.dev** | JSON 日志 | 文件增量监听 | ★★★★☆ | P2 |
| **GitHub Copilot** | VS Code workspace logs | 文件增量监听 | ★★★☆☆ | P2 |
| **Generic JSONL** | 任意 JSONL | 文件增量监听 | ★★★★★ | P1 |

### 驱动模式分类

根据日志格式差异，Watcher 分为两种驱动模式：

**File-Append Watcher**（文件追加监听）
- 使用 watchdog 监听文件变更事件
- 通过 FileCursor（byte offset）实现增量读取
- 适用于：Claude Code、Aider、Continue.dev、Generic JSONL

**Database Polling Watcher**（数据库轮询监听）
- 定时轮询 SQLite 数据库
- 通过 timestamp cursor 实现增量查询
- 适用于：Cursor（state.vscdb）

---

## 三、架构设计

### 3.1 核心原则

**每个 Watcher 负责将自己的原始事件归一化为统一格式**，下游 ETL pipeline 完全不需要感知工具差异。

```
                     ┌─ ClaudeCodeWatcher ──┐
                     │                      │
AiderWatcher ────────┤                      ├──→ normalized events ──→ ETL Pipeline ──→ viking://
                     │   (shared queue)     │
GenericJSONLWatcher──┘                      │
                                            │
CursorWatcher (polling) ────────────────────┘
```

### 3.2 归一化事件格式 (NormalizedEvent)

所有 Watcher 将原始日志转换为以下统一格式的 dict：

```python
{
    "role": "user" | "assistant",     # 必须
    "content": "...",                  # 必须，消息正文
    "type": "message",                # 必须，固定 "message"
    "timestamp": "2026-06-16T10:30:00Z",  # 可选
    "session_id": "...",              # 可选，工具特定
    "project_name": "...",            # 可选
    "tool_name": "claude_code",       # 必须，来源工具标识
}
```

新增 `tool_name` 字段用于区分来源。现有的 `ConversationReconstructor`、`LowValueFilter`、`KnowledgeExtractor` 均通过 `event.get("role")` / `event.get("content")` 读取，**完全兼容**。

### 3.3 BaseWatcher Protocol

```python
# openviking/daemon/watchers/__init__.py

from typing import Protocol, runtime_checkable

@runtime_checkable
class BaseWatcher(Protocol):
    """All tool-specific watchers must implement this interface."""

    @property
    def tool_name(self) -> str: ...

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def flush(self) -> None: ...
```

### 3.4 BaseFileWatcher 抽象基类

为 File-Append 模式的 Watcher 提取公共逻辑（watchdog Observer + 增量读取 + 游标管理 + 批处理缓冲）：

```python
# openviking/daemon/watchers/base_file_watcher.py

class BaseFileWatcher(ABC):
    """Abstract base for file-append based watchers."""

    def __init__(self, watch_dir, cursor_manager, batch_callback,
                 file_pattern, batch_trigger_lines, batch_trigger_seconds):
        ...

    @abstractmethod
    def parse_line(self, line: str) -> Optional[Dict]:
        """Parse a raw line into a raw event dict. Tool-specific."""
        ...

    @abstractmethod
    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        """Convert raw event to NormalizedEvent format. Tool-specific."""
        ...

    def filter_event(self, event: Dict) -> bool:
        """Optional: additional tool-specific filtering. Default: True."""
        return True

    # --- 公共逻辑（不需要子类重写） ---
    def start(self) -> None: ...  # 创建 Observer + Handler
    def stop(self) -> None: ...
    def flush(self) -> None: ...
```

子类只需实现 `parse_line()` 和 `normalize_event()` 两个方法。

### 3.5 Watcher 注册表 (WatcherRegistry)

```python
# openviking/daemon/watchers/registry.py

WATCHER_REGISTRY: Dict[str, Type[BaseWatcher]] = {
    "claude_code": ClaudeCodeWatcher,
    "aider": AiderWatcher,
    "cursor": CursorWatcher,
    "continue_dev": ContinueDevWatcher,
    "generic_jsonl": GenericJSONLWatcher,
}

def create_watcher(tool_name: str, **kwargs) -> BaseWatcher:
    """Factory function to create a watcher by tool name."""
    cls = WATCHER_REGISTRY.get(tool_name)
    if not cls:
        raise ValueError(f"Unknown tool: {tool_name}. Available: {list(WATCHER_REGISTRY.keys())}")
    return cls(**kwargs)
```

---

## 四、配置设计

### 4.1 新配置结构

`DaemonConfig` 从单一 `watch_dir` 演进为支持 `watchers` 列表：

```python
class WatcherConfig(BaseModel):
    tool_name: str                                    # 必填：工具标识
    watch_dir: str                                    # 必填：监听路径
    file_pattern: str = "*.jsonl"                     # 可选：文件匹配模式
    enabled: bool = True                              # 可选：是否启用
    batch_trigger_lines: int = Field(50, gt=0)        # 可选：批处理行数
    batch_trigger_seconds: int = Field(300, gt=0)     # 可选：批处理秒数
    extra: Dict[str, Any] = Field(default_factory=dict)  # 可选：工具特定参数

class DaemonConfig(BaseModel):
    enabled: bool = False
    db_path: Optional[str] = None
    watchers: List[WatcherConfig] = Field(default_factory=list)

    # 向后兼容：保留旧字段
    watch_dir: Optional[str] = None                   # deprecated
    batch_trigger_lines: int = Field(50, gt=0)        # deprecated, 全局默认值
    batch_trigger_seconds: int = Field(300, gt=0)      # deprecated, 全局默认值

    def get_effective_watchers(self) -> List[WatcherConfig]:
        """向后兼容：如果 watchers 为空但 watch_dir 存在，自动构造单个 Claude Code watcher."""
        if self.watchers:
            return [w for w in self.watchers if w.enabled]
        elif self.watch_dir:
            return [WatcherConfig(
                tool_name="claude_code",
                watch_dir=self.watch_dir,
                batch_trigger_lines=self.batch_trigger_lines,
                batch_trigger_seconds=self.batch_trigger_seconds,
            )]
        else:
            return [WatcherConfig(
                tool_name="claude_code",
                watch_dir=str(Path.home() / ".claude" / "projects"),
                batch_trigger_lines=self.batch_trigger_lines,
                batch_trigger_seconds=self.batch_trigger_seconds,
            )]
```

### 4.2 ov.conf 示例

```json
{
  "server": {
    "daemon": {
      "enabled": true,
      "db_path": "~/.openviking/daemon/cursors.db",
      "watchers": [
        {
          "tool_name": "claude_code",
          "watch_dir": "~/.claude/projects",
          "file_pattern": "*.jsonl"
        },
        {
          "tool_name": "aider",
          "watch_dir": "~/Projects",
          "file_pattern": ".aider.chat.history.md"
        },
        {
          "tool_name": "cursor",
          "watch_dir": "~/.cursor",
          "extra": {"poll_interval_seconds": 60}
        },
        {
          "tool_name": "generic_jsonl",
          "watch_dir": "~/ai-logs/copilot",
          "file_pattern": "*.jsonl",
          "extra": {"user_role_field": "author", "assistant_role_value": "copilot"}
        }
      ]
    }
  }
}
```

### 4.3 环境变量

| 变量 | 说明 |
|------|------|
| `OV_DAEMON_ENABLED` | 全局开关 |
| `OV_DAEMON_WATCHERS` | JSON 格式的 watchers 列表（覆盖 ov.conf） |

---

## 五、实现计划

### Batch 1：基础抽象层（3 文件 + 6 tests）

| 任务 | 文件 | 说明 |
|------|------|------|
| 1.1 | `watchers/__init__.py` | 定义 `BaseWatcher` Protocol |
| 1.2 | `watchers/base_file_watcher.py` | 抽象基类，提取 ClaudeCodeWatcher 的公共逻辑 |
| 1.3 | `watchers/registry.py` | Watcher 注册表 + `create_watcher()` 工厂 |
| 1.4 | `tests/daemon/test_base_file_watcher.py` | BaseFileWatcher 的 parse/normalize/buffer 测试 |
| 1.5 | `tests/daemon/test_registry.py` | 注册表查找、未知工具报错测试 |

### Batch 2：重构 ClaudeCodeWatcher + GenericJSONLWatcher（3 文件 + 8 tests）

| 任务 | 文件 | 说明 |
|------|------|------|
| 2.1 | `watchers/claude_code_watcher.py` | 重构为继承 BaseFileWatcher，实现 parse_line/normalize_event |
| 2.2 | `watchers/generic_jsonl_watcher.py` | 通用 JSONL Watcher，支持自定义字段映射 |
| 2.3 | `models.py` | 在 BatchBuffer 中增加 `tool_name` 字段 |
| 2.4 | `tests/daemon/test_claude_code_watcher.py` | 更新已有测试（保持兼容） |
| 2.5 | `tests/daemon/test_generic_jsonl_watcher.py` | GenericJSONLWatcher 的解析/归一化/字段映射测试 |

### Batch 3：AiderWatcher + CursorWatcher（4 文件 + 10 tests）

| 任务 | 文件 | 说明 |
|------|------|------|
| 3.1 | `watchers/aider_watcher.py` | Aider Markdown 历史解析 → 归一化事件 |
| 3.2 | `watchers/cursor_watcher.py` | Cursor SQLite polling + 日志文件监听 |
| 3.3 | `watchers/continue_dev_watcher.py` | Continue.dev JSON 日志监听 |
| 3.4 | `tests/daemon/test_aider_watcher.py` | Markdown 解析、对话重组、增量读取测试 |
| 3.5 | `tests/daemon/test_cursor_watcher.py` | SQLite polling、增量查询测试 |
| 3.6 | `tests/daemon/test_continue_dev_watcher.py` | JSON 日志解析测试 |

### Batch 4：配置 + 服务层重构（4 文件 + 6 tests）

| 任务 | 文件 | 说明 |
|------|------|------|
| 4.1 | `server/config.py` | 新增 WatcherConfig，重构 DaemonConfig |
| 4.2 | `daemon/service.py` | 单 watcher → watchers 列表，使用 registry 创建 |
| 4.3 | `daemon/knowledge_router.py` | URI 中包含 tool_name：`viking://skills/{tool_name}/...` |
| 4.4 | `daemon/storage_adapter.py` | Markdown 模板中 Source 字段参数化 |
| 4.5 | `daemon/models.py` | ExtractedKnowledge 增加 `source_tool` 字段 |
| 4.6 | `server/bootstrap.py` | 环境变量 OV_DAEMON_WATCHERS 支持 |
| 4.7 | `tests/daemon/test_config.py` | WatcherConfig 验证 + 向后兼容测试 |
| 4.8 | `tests/daemon/test_service_multi.py` | 多 watcher 启动/停止/路由测试 |

### Batch 5：API + Web Studio（3 文件 + 0 tests）

| 任务 | 文件 | 说明 |
|------|------|------|
| 5.1 | `server/routers/daemon.py` | 扩展 status 端点：返回每个 watcher 的状态 |
| 5.2 | `web-studio/.../daemon-status-card.tsx` | 重构为多 watcher 视图：表格 + 状态 |
| 5.3 | `web-studio/.../i18n` | 多 watcher 相关翻译 |

### Batch 6：集成测试 + 收尾（2 文件）

| 任务 | 文件 | 说明 |
|------|------|------|
| 6.1 | `tests/daemon/test_multi_watcher_integration.py` | 多 watcher → ETL → storage 全链路 |
| 6.2 | 文档更新 | README、Echo.md 引用更新 |

---

## 六、关键设计决策

### 6.1 归一化 vs 分支管道

**选择：Watcher 层归一化**，而非 ETL 层分支。

理由：
- ETL Pipeline 的四个阶段（Filter → Reconstruct → Extract → Deduplicate）已经设计为通用的
- 如果在 ETL 层引入工具分支，每个阶段都需要 if/else，复杂度 O(tools × stages)
- Watcher 层归一化后，新增工具只需实现 `parse_line()` + `normalize_event()`，ETL 零修改

### 6.2 向后兼容策略

旧的 `DaemonConfig` 字段（`watch_dir`、`batch_trigger_lines`、`batch_trigger_seconds`）保留为 deprecated，`get_effective_watchers()` 方法自动将旧配置转换为单个 `WatcherConfig`。这确保现有用户的 ov.conf 不需要修改。

### 6.3 Cursor SQLite Polling

Cursor 使用 SQLite `state.vscdb` 存储对话历史。与文件监听不同，需要：
- 定时轮询（默认 60 秒）
- 通过 `last_timestamp` 游标实现增量查询
- 复用 `CursorManager` 存储轮询位置

这是一个独立于 watchdog 的驱动模式，但通过实现相同的 `BaseWatcher` Protocol，对 DaemonService 完全透明。

### 6.4 GenericJSONLWatcher 字段映射

为了支持任意 JSONL 日志（Copilot、自研工具等），GenericJSONLWatcher 支持通过 `extra` 配置自定义字段映射：

```json
{
  "tool_name": "generic_jsonl",
  "watch_dir": "~/ai-logs/my-tool",
  "extra": {
    "role_field": "author",
    "user_role_value": "human",
    "assistant_role_value": "ai",
    "content_field": "text",
    "timestamp_field": "ts"
  }
}
```

默认值兼容 Claude Code 格式（`role` / `user` / `assistant` / `content` / `timestamp`）。

---

## 七、API 变更

### 7.1 GET /api/v1/daemon/status（增强）

```json
{
  "enabled": true,
  "running": true,
  "watchers": [
    {
      "tool_name": "claude_code",
      "watch_dir": "/Users/xxx/.claude/projects",
      "enabled": true,
      "running": true,
      "cursor_count": 12,
      "batch_trigger_lines": 50,
      "batch_trigger_seconds": 300
    },
    {
      "tool_name": "aider",
      "watch_dir": "/Users/xxx/Projects",
      "enabled": true,
      "running": true,
      "cursor_count": 3,
      "batch_trigger_lines": 50,
      "batch_trigger_seconds": 300
    }
  ],
  "db_path": "...",
  "available_tools": ["claude_code", "aider", "cursor", "continue_dev", "generic_jsonl"]
}
```

---

## 八、Web Studio 变更

Home 页面 DaemonStatusCard 从单状态卡片升级为多 Watcher 表格视图：

- 顶部：全局状态（enabled/running）+ watcher 数量统计
- 中部：Watcher 列表表格（工具名、路径、状态、游标数、最后活动）
- 底部：可用工具列表（available_tools）

每 30 秒自动刷新。

---

## 九、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Cursor SQLite schema 变更 | 解析失败 | 防御性编程 + 版本检测 + 降级到日志文件监听 |
| 多 watcher 并发写入 SQLite cursor DB | 竞态条件 | SQLite WAL mode + 每个 watcher 独立 cursor key |
| Aider Markdown 格式不稳定 | 解析错误 | 宽松正则 + 跳过不可解析段落 + 详细日志 |
| 大量 watcher 导致 ETL 队列拥堵 | 延迟增加 | Queue 大小限制 + 背压 + 优先级队列 |

---

## 十、里程碑

| 阶段 | 内容 | 预计 commits |
|------|------|-------------|
| Batch 1 | 基础抽象层 | 3-4 |
| Batch 2 | 重构 + GenericJSONL | 4-5 |
| Batch 3 | Aider + Cursor + Continue | 5-6 |
| Batch 4 | 配置 + 服务层 | 4-5 |
| Batch 5 | API + Web Studio | 3-4 |
| Batch 6 | 集成 + 收尾 | 2-3 |
| **总计** | | **21-27 commits** |
