## Daemon 数据库源工具适配方案（v2 — 详细执行计划）

### 一、现状分析

#### 1.1 当前架构全景

```
                     BaseWatcher Protocol
                   (tool_name, start, stop, flush)
                           │
                    BaseFileWatcher (ABC)
                ┌─────────┼──────────────────────┐
                │         │                      │
          watchdog     CursorManager         BatchBuffer
          Observer     (byte offset)        (lines + time)
                │         │                      │
                └─────────┴──────────┬───────────┘
                                     │
                          _process_file()
                          parse_line() → normalize_event() → _post_normalize() → filter_event()
                                     │
                              batch_callback → DaemonService._enqueue_batch()
                                     │
                          asyncio.Queue → _etl_loop() → BatchETLPipeline
                                     │
                          Filter → Reconstruct → Extract(LLM) → Dedup
                                     │
                          VikingStorageAdapter → viking://resources/
```

#### 1.2 现有 5 个文件 Watcher

| Watcher | tool_name | 文件格式 | 继承 | 特殊点 |
|---------|-----------|----------|------|--------|
| ClaudeCodeWatcher | `claude_code` | `*.jsonl` | BaseFileWatcher | `_post_normalize` 从路径推导 project_name |
| CursorWatcher | `cursor` | `*.log` | BaseFileWatcher | 灵活 role 映射（user/human/human_turn） |
| AiderWatcher | `aider` | `.aider.chat.history.md` | BaseFileWatcher | **覆盖** `_process_file`，多行 Markdown 解析 |
| ContinueDevWatcher | `continue_dev` | `*.json` | BaseFileWatcher | 标准 JSON 行格式 |
| GenericJSONLWatcher | `generic_jsonl` | `*.jsonl` | BaseFileWatcher | `extra` dict 自定义字段映射 |

#### 1.3 关键接口约束

- **BaseWatcher Protocol**（`watchers/__init__.py`）：4 个方法，天然支持非文件型 watcher
- **Registry**（`registry.py`）：`create_watcher(tool_name, **kwargs)` 工厂模式，已有 `extra` 参数传递链
- **CursorManager**（`cursor_manager.py`）：`file_path TEXT PK` + `last_position INTEGER`，后者是通用整数——文件 watcher 存字节偏移，DB watcher 可存 rowid/timestamp
- **DaemonService**（`service.py`）：`_enqueue_batch` 已用 `loop.call_soon_threadsafe`，线程安全——polling thread 可直接调用
- **WatcherConfig**（`config.py`）：已有 `extra: Dict[str, Any]` 字段，无需改 config schema

#### 1.4 不需要改动的部分

- `BatchBuffer` / `BatchETLPipeline` / `ConversationReconstructor` / `KnowledgeExtractor` / `KnowledgeDeduplicator` / `VikingStorageAdapter` / `KnowledgeRouter` ——全部与数据源无关，只消费 normalized events
- `WatcherConfig` / `DaemonConfig` —— schema 已够用
- `DaemonService.start()` / `_etl_loop()` —— 已支持多 watcher 并行

---

### 二、需要适配的数据库源工具（含格式调研）

#### 2.1 Cursor（P0）— 双库架构

**⚠️ 关键发现：Cursor 使用两个独立的 SQLite 数据库**

| 数据库 | 路径 (Windows) | 表名 | 用途 |
|--------|---------------|------|------|
| 工作区 DB | `%APPDATA%\Cursor\User\workspaceStorage\<hash>\state.vscdb` | `ItemTable`（PascalCase） | 会话元数据 |
| 全局 DB | `%APPDATA%\Cursor\User\globalStorage\state.vscdb` | `cursorDiskKV`（camelCase） | 对话内容 |

**踩坑点：表名大小写不同！混用会报 `no such table` 错误。**

**工作区 DB — 会话元数据**：
```sql
-- 查询所有会话列表
SELECT value FROM ItemTable WHERE [key] = 'composer.composerData';
```
返回 JSON，结构为 `{ "allComposers": [ { "id": "<composerId>", "createdAt": "...", ... } ] }`

**全局 DB — 对话内容（Bubble 数据）**：
```sql
-- 查询某会话的所有消息
SELECT [key], value FROM cursorDiskKV WHERE [key] LIKE 'bubbleId:<composerId>:%' ORDER BY rowid ASC;

-- 查询所有 composerId
SELECT DISTINCT SUBSTR([key], 10, INSTR(SUBSTR([key], 10), ':') - 1)
FROM cursorDiskKV WHERE [key] LIKE 'bubbleId:%';
```

**Key 格式**：`bubbleId:<composerId>:<bubbleId>`（冒号分隔三段）

**Value JSON 结构（单条消息）**：
```json
{
  "_v": 3,                    // schema 版本号
  "type": 1,                  // 1=用户消息, 2=助手消息
  "text": "用户的问题内容",     // 对话文本
  "createdAt": "2026-...",    // 时间戳
  "isAgentic": false,         // 是否为 Agent 模式
  "toolResults": [],          // 工具调用结果
  "codeBlocks": [],           // 代码块
  "allThinkingBlocks": [      // AI 思考过程（仅 type=2）
    { "thinking": "..." }
  ]
}
```

**解析注意事项**：
1. 流式传输会产生 `text` 为空的中间态 assistant 消息，必须过滤
2. `_v` 版本号可能随更新递增，遇到未知版本应仅打 warning
3. 工作区路径有 `file:///` 前缀且 URL 编码，需解码
4. 项目文件夹删除后，工作区 DB 丢失但全局 DB 数据仍在（"孤立对话"）
5. 数据物理隔离在两个 DB，必须先从工作区获取 composerId，再跨库查询全局内容

#### 2.2 Trae（P1）— 单库架构

| 路径 (Windows) | 表名 | Key 模式 |
|---------------|------|----------|
| `%APPDATA%\Trae\User\workspaceStorage\<hash>\state.vscdb` | `ItemTable` | `memento/icube-ai-ng-chat-storage` 或 `memento/icube-ai-agent-storage` |

**Value JSON 结构**：
```json
{
  "list": [
    {
      "messages": [
        {
          "role": "user",
          "content": "用户的问题",
          "timestamp": "...",
          "status": "active"          // "deleted" 的需跳过
        },
        {
          "role": "assistant",
          "content": "",               // 注意：可能为空！
          "agentTaskContent": {        // 实际内容可能在这里
            "proposalText": "实际回复内容",
            "proposalReasoningContent": "推理过程"
          }
        }
      ]
    }
  ]
}
```

**踩坑点**：助手消息的 `content` 字段经常为空，核心内容在 `agentTaskContent` 中。提取优先级：`content` > `agentTaskContent.proposalText` > `finish` 工具结果。

#### 2.3 Windsurf（P2）

- 存储格式：类似 VS Code 的 SQLite `state.vscdb`
- 路径：`%APPDATA%\Windsurf\User\` 下
- Cascade 会话文件：`~/.codeium/windsurf/cascade/`
- **具体表名和 key 模式尚无公开文档**，需实际 dump 确认
- 使用 WAL 模式（存在 `-wal` 和 `-shm` 文件）

#### 2.4 GitHub Copilot（P2）

**⚠️ 关键发现：Copilot 不用 SQLite，用的是 JSON/JSONL 文件**

| 路径 | 格式 | 说明 |
|------|------|------|
| VS Code `workspaceStorage/<hash>/chatSessions/` | `.json` + `.jsonl` | 项目级 |
| VS Code `globalStorage/chatSessions/` | `.json` + `.jsonl` | 全局 |

**JSON/JSONL 结构**：
- `.json` 文件包含 `sessionId`、`timestamp`、`customTitle`
- `.jsonl` 文件首行 `kind: 0` 为完整快照，后续行为 UI 补丁（应忽略）
- 快照内 `requests` 数组嵌套用户输入和助手响应
- 响应通过 `kind` 字段区分（文本/思考/工具调用）

→ 这意味着 GitHub Copilot 更适合用现有的 `BaseFileWatcher`（文件追加模式），而非 `BasePollingWatcher`。需要新建一个 `CopilotChatWatcher(BaseFileWatcher)` 子类。

#### 2.5 优先级排序

| 优先级 | 工具 | Watcher 类型 | 理由 |
|--------|------|-------------|------|
| **P0** | Cursor | BasePollingWatcher（双库 SQLite） | 用户量最大，格式已调研 |
| P1 | Trae | BasePollingWatcher（单库 SQLite） | 格式已明确，VS Code fork |
| P1 | Windsurf | BasePollingWatcher（SQLite） | 与 Cursor/Trae 类似，需 dump 确认 |
| P2 | GitHub Copilot | **BaseFileWatcher**（JSON/JSONL 文件） | 非 SQLite！用文件追加模式 |
| P3 | Warp Terminal | BasePollingWatcher | 命令历史，低优先级 |
| P4 | Tabnine | BasePollingWatcher | JSON+SQLite 混合，低优先级 |

---

### 三、架构设计

#### 3.1 新增 BasePollingWatcher 基类

**文件**：`openviking/daemon/watchers/base_polling_watcher.py`

不继承 `BaseFileWatcher`，直接实现 `BaseWatcher` Protocol。

```python
class BasePollingWatcher(ABC):
    """Base class for database/API-based watchers that use periodic polling."""

    def __init__(
        self,
        tool_name: str,
        watch_dir: str,           # DB 文件所在目录（或 DB 文件路径）
        cursor_manager: CursorManager,
        batch_callback: Callable[[List[Dict]], None],
        poll_interval: int = 30,  # 轮询间隔（秒）
        batch_trigger_lines: int = 50,
        batch_trigger_seconds: int = 300,
        extra: Optional[Dict] = None,
        **kwargs,                 # 兼容 create_watcher 工厂的其他参数
    ):
        self._tool_name = tool_name
        self.watch_dir = os.path.expanduser(watch_dir)
        self.cursor_manager = cursor_manager
        self.batch_callback = batch_callback
        self.poll_interval = poll_interval
        self.extra = extra or {}

        self._buffer = BatchBuffer()
        self.batch_trigger_lines = batch_trigger_lines
        self.batch_trigger_seconds = batch_trigger_seconds
        self._poll_thread: Optional[Thread] = None
        self._stop_event = threading.Event()

    # ─── BaseWatcher Protocol ───
    @property
    def tool_name(self) -> str:
        return self._tool_name

    def start(self) -> None:
        self._stop_event.clear()
        self._poll_thread = Thread(target=self._poll_loop, daemon=True, name=f"poll-{self._tool_name}")
        self._poll_thread.start()
        logger.info("[%s] Polling watcher started (interval=%ds)", self._tool_name, self.poll_interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=5)
        logger.info("[%s] Polling watcher stopped", self._tool_name)

    def flush(self) -> None:
        self._flush_buffer()

    # ─── 子类必须实现 ───
    @abstractmethod
    def query_new_events(self, last_cursor: int) -> List[Dict]:
        """查询数据源中 cursor 之后的新事件。
        Args:
            last_cursor: 上次处理到的位置（rowid/timestamp/offset）
        Returns:
            raw event dicts，每个必须包含 '_cursor_position' 字段
        """
        ...

    @abstractmethod
    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        """将 raw DB row 转换为标准 normalized event dict。
        返回 None 表示跳过。
        输出格式同文件 watcher：{role, content, type, timestamp, session_id, project_name}
        """
        ...

    # ─── 可选覆盖 ───
    def filter_event(self, event: Dict) -> bool:
        """额外过滤。默认保留全部。"""
        return True

    def resolve_db_path(self) -> Optional[str]:
        """解析 DB 文件完整路径。
        默认：在 watch_dir 下查找 extra['db_filename']。
        子类可覆盖以实现更复杂的发现逻辑。
        """
        db_filename = self.extra.get("db_filename", "state.vscdb")
        candidate = os.path.join(self.watch_dir, db_filename)
        if os.path.exists(candidate):
            return candidate
        return None

    # ─── 内部方法 ───
    def _poll_loop(self):
        """主轮询循环。"""
        # cursor key 使用 watch_dir（与文件 watcher 的 file_path 区分）
        cursor_key = self.watch_dir

        while not self._stop_event.is_set():
            try:
                db_path = self.resolve_db_path()
                if db_path is None:
                    logger.debug("[%s] DB not found, retrying...", self._tool_name)
                    self._stop_event.wait(self.poll_interval)
                    continue

                cursor = self.cursor_manager.get_cursor(cursor_key)
                raw_events = self.query_new_events(cursor.last_position)

                if raw_events:
                    new_position = cursor.last_position
                    for raw in raw_events:
                        normalized = self.normalize_event(raw)
                        if normalized is None:
                            continue
                        if not self.filter_event(normalized):
                            continue
                        normalized["tool_name"] = self._tool_name
                        self._buffer.add_line(normalized, byte_size=0)

                        # 追踪最大 cursor position
                        pos = raw.get("_cursor_position", 0)
                        if pos > new_position:
                            new_position = pos

                    # 更新 cursor
                    if new_position > cursor.last_position:
                        self.cursor_manager.update_cursor(cursor_key, new_position)

                self._check_batch_trigger()

            except Exception as e:
                logger.error("[%s] Poll error: %s", self._tool_name, e, exc_info=True)

            self._stop_event.wait(self.poll_interval)

    def _check_batch_trigger(self):
        if self._buffer.is_empty():
            return
        line_count = len(self._buffer.lines)
        age = time.time() - self._buffer.created_at if self._buffer.created_at > 0 else 0
        if line_count >= self.batch_trigger_lines or age >= self.batch_trigger_seconds:
            self._flush_buffer()

    def _flush_buffer(self):
        if self._buffer.is_empty():
            return
        events = self._buffer.lines.copy()
        logger.info("[%s] Flushing %d events", self._tool_name, len(events))
        try:
            self.batch_callback(events)
            self._buffer.clear()
        except Exception as e:
            logger.error("[%s] Batch callback failed: %s", self._tool_name, e, exc_info=True)
```

**关键设计决策：**

| 决策点 | 方案 | 理由 |
|--------|------|------|
| 轮询机制 | `Thread` + `Event.wait(interval)` | 与 watchdog Observer 平级，DaemonService 已处理线程安全 |
| cursor key | 使用 `watch_dir` 字符串 | 与文件 watcher 的 `file_path` 互不冲突，CursorManager 无需改动 |
| DB 连接管理 | 子类在 `query_new_events` 内自行 open/close | SQLite `?mode=ro` 只读，避免锁冲突；CursorManager 已有 per-call connect 模式 |
| `_cursor_position` 约定 | raw event dict 中必须携带 | 让子类灵活定义位置语义（rowid/timestamp/offset），基类只取 max |
| `BatchBuffer` 复用 | 直接用现有实现 | `byte_size=0`（DB 无字节偏移概念），time trigger 正常工作 |
| `extra` 参数 | 传递 `poll_interval`、`db_filename` 等 | 利用已有 WatcherConfig.extra 字段 |

**注意：`BatchBuffer` 与 trigger 值存储**

当前 `BatchBuffer` dataclass 不接受 trigger 参数——`batch_trigger_lines` 和 `batch_trigger_seconds` 由 `BaseFileWatcher` 作为实例属性持有，在 `_check_batch_trigger()` 中读取。

`BasePollingWatcher` 采用同样模式：`self.batch_trigger_lines` / `self.batch_trigger_seconds` 存在 watcher 实例上，`BatchBuffer()` 无参构造。与 `BaseFileWatcher` 完全一致，零改动。

#### 3.2 CursorDBWatcher 实现（P0）— 基于真实格式调研

**文件**：`openviking/daemon/watchers/cursor_db_watcher.py`

Cursor 使用**双库架构**：工作区 DB 存会话元数据，全局 DB 存对话内容。CursorDBWatcher 的 `watch_dir` 指向 Cursor 用户数据根目录（如 `%APPDATA%\Cursor\User`），内部同时访问两个 state.vscdb。

```python
@register_watcher("cursor_db")
class CursorDBWatcher(BasePollingWatcher):
    """Watches Cursor IDE's dual-SQLite storage for AI conversations.

    Architecture:
    - Workspace DB: workspaceStorage/<hash>/state.vscdb → ItemTable → composer.composerData (session metadata)
    - Global DB: globalStorage/state.vscdb → cursorDiskKV → bubbleId:<composerId>:<bubbleId> (message content)
    """

    def __init__(self, watch_dir, cursor_manager, batch_callback,
                 poll_interval=30, batch_trigger_lines=50, batch_trigger_seconds=300,
                 extra=None, **kwargs):
        super().__init__(
            tool_name="cursor_db",
            watch_dir=watch_dir,          # e.g. %APPDATA%\Cursor\User
            cursor_manager=cursor_manager,
            batch_callback=batch_callback,
            poll_interval=poll_interval,
            batch_trigger_lines=batch_trigger_lines,
            batch_trigger_seconds=batch_trigger_seconds,
            extra=extra,
        )
        self._global_db_path = os.path.join(self.watch_dir, "globalStorage", "state.vscdb")
        self._workspace_storage_dir = os.path.join(self.watch_dir, "workspaceStorage")

    def resolve_db_path(self) -> Optional[str]:
        """返回全局 DB 路径（主要数据源）。"""
        if os.path.exists(self._global_db_path):
            return self._global_db_path
        return None

    def _discover_composer_ids(self) -> List[str]:
        """扫描所有工作区 DB，收集 composerId 列表。
        用于关联全局 DB 中的 bubble 数据。
        """
        composer_ids = []
        if not os.path.isdir(self._workspace_storage_dir):
            return composer_ids

        for ws_hash in os.listdir(self._workspace_storage_dir):
            ws_db = os.path.join(self._workspace_storage_dir, ws_hash, "state.vscdb")
            if not os.path.exists(ws_db):
                continue
            try:
                conn = sqlite3.connect(f"file:{ws_db}?mode=ro", uri=True)
                try:
                    row = conn.execute(
                        "SELECT value FROM ItemTable WHERE [key] = 'composer.composerData'"
                    ).fetchone()
                    if row and row[0]:
                        data = json.loads(row[0])
                        all_composers = data.get("allComposers", [])
                        for c in all_composers:
                            cid = c.get("id")
                            if cid:
                                composer_ids.append(cid)
                finally:
                    conn.close()
            except Exception:
                continue
        return composer_ids

    def query_new_events(self, last_cursor: int) -> List[Dict]:
        """从全局 DB 的 cursorDiskKV 表查询新 bubble 数据。

        策略：直接扫描所有 bubbleId:* key（rowid > last_cursor），
        不依赖工作区 DB 的 composerId 列表（支持发现孤立对话）。
        """
        db_path = self.resolve_db_path()
        if not db_path:
            return []

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout = 3000")
            try:
                rows = conn.execute(
                    "SELECT rowid, [key], value FROM cursorDiskKV "
                    "WHERE rowid > ? AND [key] LIKE 'bubbleId:%' "
                    "ORDER BY rowid ASC LIMIT 500",
                    (last_cursor,)
                ).fetchall()

                events = []
                for rowid, key, value in rows:
                    # 解析 key: bubbleId:<composerId>:<bubbleId>
                    parts = key.split(":", 2)
                    composer_id = parts[1] if len(parts) >= 3 else None

                    try:
                        parsed_value = json.loads(value) if isinstance(value, str) else value
                    except (json.JSONDecodeError, TypeError):
                        continue

                    events.append({
                        "rowid": rowid,
                        "key": key,
                        "value": parsed_value,
                        "composer_id": composer_id,
                        "_cursor_position": rowid,
                    })
                return events
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            logger.warning("[cursor_db] SQLite error (DB may be locked): %s", e)
            return []

    def normalize_event(self, raw_event: Dict) -> Optional[Dict]:
        """解析 Cursor bubble 格式。

        Value JSON:
        - _v: schema version (currently 3)
        - type: 1=user, 2=assistant
        - text: message content
        - createdAt: timestamp
        - allThinkingBlocks: AI reasoning (assistant only)
        """
        value = raw_event.get("value")
        if not isinstance(value, dict):
            return None

        # Schema version check — warn but don't crash
        schema_version = value.get("_v", 0)
        if schema_version > 3:
            logger.debug("[cursor_db] Unknown bubble schema v%d", schema_version)

        # type: 1=user, 2=assistant
        bubble_type = value.get("type")
        if bubble_type == 1:
            role = "user"
        elif bubble_type == 2:
            role = "assistant"
        else:
            return None

        # text: message content
        content = value.get("text", "")
        if not content or not content.strip():
            return None  # 过滤流式传输的空壳消息

        return {
            "role": role,
            "content": content,
            "type": "message",
            "timestamp": value.get("createdAt"),
            "session_id": raw_event.get("composer_id"),
        }

    def filter_event(self, event: Dict) -> bool:
        """过滤过短内容。"""
        content = event.get("content", "")
        return len(content.strip()) >= 10
```

**与原计划的关键差异**：

| 原假设 | 实际格式 |
|--------|----------|
| 单库 `ItemTable` + `WHERE key LIKE '%chat%'` | 双库：`ItemTable`（元数据）+ `cursorDiskKV`（内容） |
| value 有 `role` 字段 | `type` 字段：1=用户, 2=助手 |
| value 有 `message`/`content` 字段 | `text` 字段 |
| `session_id` 在 value 内 | 从 key 的冒号分隔中解析（`composerId`） |
| 无 schema 版本 | `_v` 字段标识版本（当前主流 v3） |

**调研验证命令**（在用户机器上确认）：
```bash
# 确认全局 DB 表名
sqlite3 "%APPDATA%/Cursor/User/globalStorage/state.vscdb" ".tables"
# 预期输出: cursorDiskKV

# 查看 bubble 数据样例
sqlite3 "%APPDATA%/Cursor/User/globalStorage/state.vscdb" \
  "SELECT [key], value FROM cursorDiskKV WHERE [key] LIKE 'bubbleId:%' LIMIT 3"
```

#### 3.3 WindsurfDBWatcher（P2，待调研）

**⚠️ Windsurf 的具体表名和 key 模式尚无公开文档。** Phase 4 需先 dump 真实 state.vscdb 确认：
- 是否使用 `cursorDiskKV` 还是 `ItemTable`
- bubble key 模式是否与 Cursor 相同
- 是否有独特的 Cascade 会话格式

如果格式与 Cursor 高度相似，可继承 CursorDBWatcher：

```python
@register_watcher("windsurf_db")
class WindsurfDBWatcher(CursorDBWatcher):
    """Windsurf — format TBD after dump analysis."""

    @property
    def tool_name(self) -> str:
        return "windsurf_db"
```

如果格式差异大，则独立实现。

#### 3.4 Registry 更新

在 `_register_builtins()` 中添加：

```python
try:
    from openviking.daemon.watchers.cursor_db_watcher import CursorDBWatcher
    _WATCHER_REGISTRY["cursor_db"] = CursorDBWatcher
except ImportError:
    pass

try:
    from openviking.daemon.watchers.trae_db_watcher import TraeDBWatcher
    _WATCHER_REGISTRY["trae_db"] = TraeDBWatcher
except ImportError:
    pass

try:
    from openviking.daemon.watchers.windsurf_db_watcher import WindsurfDBWatcher
    _WATCHER_REGISTRY["windsurf_db"] = WindsurfDBWatcher
except ImportError:
    pass
```

#### 3.5 WatcherConfig 示例

```json
{
  "server": {
    "daemon": {
      "enabled": true,
      "watchers": [
        {
          "tool_name": "claude_code",
          "watch_dir": "C:\\Users\\xxx\\.claude\\projects",
          "batch_trigger_lines": 5
        },
        {
          "tool_name": "cursor_db",
          "watch_dir": "C:\\Users\\xxx\\AppData\\Roaming\\Cursor\\User",
          "batch_trigger_lines": 5,
          "extra": {
            "poll_interval": 30
          }
        },
        {
          "tool_name": "trae_db",
          "watch_dir": "C:\\Users\\xxx\\AppData\\Roaming\\Trae\\User",
          "batch_trigger_lines": 5,
          "extra": {
            "poll_interval": 30
          }
        },
        {
          "tool_name": "windsurf_db",
          "watch_dir": "C:\\Users\\xxx\\AppData\\Roaming\\Windsurf\\User",
          "batch_trigger_lines": 5,
          "extra": {
            "poll_interval": 30
          }
        }
      ]
    }
  }
}
```

**注意**：`watch_dir` 现在指向各 IDE 的 `User` 根目录（而非 `globalStorage`），因为 CursorDBWatcher 内部需要同时访问 `workspaceStorage/` 和 `globalStorage/` 两个子目录。

#### 3.6 CursorManager 兼容性

**无需改动**。现有表结构完全适用：

```sql
CREATE TABLE IF NOT EXISTS file_cursors (
    file_path TEXT PRIMARY KEY,         -- DB watcher: watch_dir 路径
    last_position INTEGER NOT NULL,     -- DB watcher: rowid / timestamp
    last_read_time REAL NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

文件 watcher 和 DB watcher 以 `file_path` 字段值（文件路径 vs 目录路径）自然区分，互不干扰。

#### 3.7 DaemonService 兼容性

**无需改动**。`service.py` 的 `start()` 方法已经：
- 通过 `create_watcher(**kwargs)` 工厂创建 watcher，自动适配 BasePollingWatcher
- 通过 `_enqueue_batch` → `call_soon_threadsafe` 处理来自 polling thread 的回调
- 通过 `watcher.start()` / `stop()` / `flush()` 统一生命周期管理

唯一需要确认：`DaemonService.start()` 中 `Path(watch_dir).mkdir(parents=True, exist_ok=True)` 对 DB watcher 是否安全——DB watcher 的 watch_dir 是已存在的 Cursor 安装目录，`mkdir(exist_ok=True)` 不会出错。✓

---

### 四、实施任务清单

#### Phase 1：基础设施（BasePollingWatcher + 测试框架）

| # | 任务 | 文件 | 预估 |
|---|------|------|------|
| 1.1 | 创建 `BasePollingWatcher` 基类 | `watchers/base_polling_watcher.py` | ~120 行 |
| 1.2 | 创建 `test_base_polling_watcher.py` 单元测试 | `tests/daemon/` | mock poll loop / buffer / flush / Protocol 验证 |

#### Phase 2：CursorDBWatcher（P0 核心）

| # | 任务 | 文件 | 预估 |
|---|------|------|------|
| 2.1 | ~~调研~~：dump 真实 Cursor state.vscdb 结构 | — | **已完成**（见 §2.1） |
| 2.2 | 创建 `CursorDBWatcher`（双库架构、cursorDiskKV、bubbleId 解析） | `watchers/cursor_db_watcher.py` | ~150 行 |
| 2.3 | 单元测试 `test_cursor_db_watcher.py` | `tests/daemon/` | 创建临时双库 SQLite → mock bubble 数据 → normalize 验证 |
| 2.4 | 集成测试：完整 poll 周期 → batch_callback 验证 | 同上 | |
| 2.5 | Registry 注册 `cursor_db` | `watchers/registry.py` | 5 行 |

#### Phase 3：TraeDBWatcher（P1）

| # | 任务 | 文件 | 预估 |
|---|------|------|------|
| 3.1 | 创建 `TraeDBWatcher`（单库 ItemTable、`memento/icube-ai-ng-chat-storage` key） | `watchers/trae_db_watcher.py` | ~120 行 |
| 3.2 | `normalize_event`：处理 `content` 为空的 fallback（`agentTaskContent.proposalText`） | 同上 | |
| 3.3 | 单元测试 + Registry 注册 `trae_db` | `tests/daemon/test_trae_db_watcher.py` | |

#### Phase 4：WindsurfDBWatcher（P2，需先 dump 确认格式）

| # | 任务 | 文件 | 预估 |
|---|------|------|------|
| 4.1 | **调研**：dump 真实 Windsurf state.vscdb 确认表名和 key 模式 | 手动 | 可能继承 CursorDBWatcher |
| 4.2 | 创建 `WindsurfDBWatcher` | `watchers/windsurf_db_watcher.py` | ~30 行（若格式同 Cursor） |
| 4.3 | 单元测试 + Registry 注册 `windsurf_db` | | |

#### Phase 5：集成验证

| # | 任务 | 文件 | 预估 |
|---|------|------|------|
| 5.1 | 更新 `test_multi_watcher_integration.py`：加入 DB watcher 测试 | `tests/daemon/` | |
| 5.2 | 端到端：真实 Cursor 对话 → watcher → ETL → viking:// | 手动验证 | |
| 5.3 | 更新 `test_all_watchers_registered` 验证新增 watcher | | 2 行 |

---

### 五、风险点与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| Cursor 锁住 SQLite（WAL mode） | `query_new_events` 读超时 | `?mode=ro` + `PRAGMA busy_timeout=3000`；读失败时跳过本轮 |
| Cursor 更新 `cursorDiskKV` 格式 / `_v` 版本升级 | normalize 失效 | normalize_event 已含 `_v` 版本检查；加 debug 日志记录未知格式 |
| watch_dir 不存在或 DB 文件被删 | resolve_db_path 返回 None | _poll_loop 已有 None 检查，优雅跳过 |
| polling thread 与 asyncio event loop 竞争 | batch_callback 死锁 | DaemonService._enqueue_batch 已用 call_soon_threadsafe，无改动 |
| CursorManager 的 `file_path` 字段语义混淆 | 调试困难 | 在 BasePollingWatcher 文档注释中明确说明 cursor_key = watch_dir |
| Trae 助手消息 `content` 为空 | 丢失有效内容 | 三级 fallback：`content` → `agentTaskContent.proposalText` → `finish` 工具结果 |
| Windsurf 格式与 Cursor 不同 | 不能简单继承 | Phase 4 调研确认后再决定实现方式 |

---

### 六、验证计划

每个新 DB watcher 的三级验证：

1. **单元测试**：创建临时 SQLite → 写入 mock 数据（模拟真实 bubble/trait 格式）→ 调用 `query_new_events(0)` → 验证返回值 → 调用 `normalize_event` → 验证 normalized 格式
2. **集成测试**：`BasePollingWatcher.start()` → 等待 1-2 个 poll 周期 → 验证 `batch_callback` 被调用且 events 格式正确 → `stop()`
3. **端到端**：真实工具产生对话 → watcher 检测 → ETL pipeline → 知识写入 `viking://resources/`

---

### 七、文件变更清单

| 操作 | 文件路径 |
|------|----------|
| **新增** | `openviking/daemon/watchers/base_polling_watcher.py` |
| **新增** | `openviking/daemon/watchers/cursor_db_watcher.py`（P0） |
| **新增** | `openviking/daemon/watchers/trae_db_watcher.py`（P1） |
| **新增** | `openviking/daemon/watchers/windsurf_db_watcher.py`（P2） |
| **新增** | `tests/daemon/test_base_polling_watcher.py` |
| **新增** | `tests/daemon/test_cursor_db_watcher.py` |
| **新增** | `tests/daemon/test_trae_db_watcher.py` |
| **修改** | `openviking/daemon/watchers/registry.py` — 添加 cursor_db / trae_db / windsurf_db 注册 |
| **修改** | `tests/daemon/test_multi_watcher_integration.py` — 添加 DB watcher 测试用例 |
| **不改** | `models.py` / `cursor_manager.py` / `service.py` / `etl_pipeline.py` / `config.py` / `base_file_watcher.py` |

---

### 八、参考资料

| 来源 | 链接 |
|------|------|
| Cursor state.vscdb 解析踩坑记 | https://article.juejin.cn/post/7640063917021167625 |
| Cursor 对话导入：解析 SQLite 里的宝藏 | https://juejin.cn/post/7640053666326741030 |
| 5 种 AI 对话数据格式全解析 | https://blog.csdn.net/2201_75708499/article/details/161789991 |
| Codex CLI / Trae / Copilot 数据源接入 | https://juejin.cn/post/7640357289836281882 |
| AI编程助手数据提取终极指南 | https://www.xugj520.cn/archives/ai-coding-assistant-data-extraction-2.html |
| Trae 对话记录导出脚本 | https://juejin.cn/post/7472786501598101523 |
| windsurf-monitor (GitHub) | https://github.com/bjfwan/windsurf-monitor |
