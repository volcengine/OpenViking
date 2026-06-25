# Active Daemon 配置指南

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OV_DAEMON_ENABLED` | 启用 Daemon | `false` |
| `OV_DAEMON_WATCH_DIR` | 监听目录 | `~/.claude/projects` |
| `OV_DAEMON_DB_PATH` | 游标数据库路径 | `~/.qoderworkcn/openviking/daemon_cursors.db` |
| `OV_DAEMON_BATCH_LINES` | 批处理触发行数 | `50` |
| `OV_DAEMON_BATCH_SECONDS` | 批处理触发秒数 | `300` |

## JSON 配置 (ov.conf)

> **重要**：daemon 配置必须放在 `"server"` 节内，不是顶层配置。ov.conf 的 JSON 解析器不支持 `#` 注释。

单 watcher 配置：

```json
{
  "server": {
    "port": 1988,
    "daemon": {
      "enabled": true,
      "watch_dir": "~/.claude/projects",
      "batch_trigger_lines": 50,
      "batch_trigger_seconds": 300
    }
  }
}
```

多 watcher 配置（推荐，支持同时监听多个 AI 工具）：

```json
{
  "server": {
    "port": 1988,
    "daemon": {
      "enabled": true,
      "watchers": [
        {
          "tool_name": "claude_code",
          "watch_dir": "C:\\Users\\xxx\\.claude\\projects",
          "batch_trigger_lines": 5,
          "batch_trigger_seconds": 60
        },
        {
          "tool_name": "cursor_db",
          "watch_dir": "C:\\Users\\xxx\\AppData\\Roaming\\Cursor\\User\\globalStorage",
          "poll_interval": 60
        }
      ]
    }
  }
}
```

`watchers` 数组中每项支持 `tool_name`、`watch_dir`、`batch_trigger_lines`、`batch_trigger_seconds`、`extra` 字段。当 `watchers` 存在时，顶层的 `watch_dir` 被忽略。

## Docker 部署

```yaml
services:
  openviking:
    image: openviking:latest
    environment:
      - OV_DAEMON_ENABLED=true
      - OV_DAEMON_WATCH_DIR=/data/claude-projects
      - OV_DAEMON_DB_PATH=/data/daemon.db
    volumes:
      - ./claude-projects:/data/claude-projects
      - ./daemon-data:/data
    ports:
      - "1933:1933"
```

## 日志

Daemon 使用 OpenViking 标准日志系统。关键日志：

- `Claude Code watcher started on ...` — 监听器启动
- `Flushing batch with N events` — 批处理触发
- `Extracted N knowledge items` — 知识提取完成
- `Knowledge ingested: viking://...` — 知识写入成功
