# OpenViking Active Daemon

自动监听 Claude Code 会话日志，提取知识并写入 OpenViking 知识库。

## 快速开始

### 启用 Daemon

设置环境变量：

```bash
export OV_DAEMON_ENABLED=true
export OV_DAEMON_WATCH_DIR=~/.claude/projects    # 监听目录（可选）
export OV_DAEMON_BATCH_LINES=50                   # 批处理触发行数（可选）
export OV_DAEMON_BATCH_SECONDS=300                # 批处理触发秒数（可选）
```

### 启动 OpenViking Server

```bash
openviking serve
```

Daemon 会在服务器启动时自动运行（如果 `OV_DAEMON_ENABLED=true`）。

## 工作原理

1. **文件监听** — 监控 `~/.claude/projects/` 下的 `.jsonl` 文件变化
2. **增量读取** — 文件游标技术，只处理新增内容
3. **批量处理** — 累积 50 行或 5 分钟后触发 ETL 管道
4. **知识提取** — 调用 LLM 过滤噪声，提取有价值的知识
5. **自动存储** — 写入 `viking://skills/`、`viking://memories/`、`viking://resources/`

## 知识分类

| 类型 | 目标路径 | 说明 |
|------|---------|------|
| Skills | `viking://skills/claude_code/<title>.md` | 可复用的操作指南 |
| Memories (有项目) | `viking://memories/projects/<project>/decisions.md` | 项目决策日志 |
| Memories (无项目) | `viking://memories/global/<title>.md` | 全局记忆 |
| Resources | `viking://resources/<tech>/<title>.md` | 参考资源 |

## 架构

```
Claude Code JSONL → File Watcher → Batch Buffer → Filter → Reconstruct
    → LLM Extract → Deduplicate → Route → viking:// Storage
```

## 故障排查

### Daemon 未启动
检查日志中是否有 `Active Daemon is disabled` 消息，确认 `OV_DAEMON_ENABLED=true`。

### 没有提取到知识
- 确认 Claude Code 正在写入 JSONL 文件（`~/.claude/projects/` 下有 `.jsonl` 文件）
- 对话内容可能不够有价值（简单问答会被过滤）
- 查看日志中的 ETL 处理信息

### 知识写入失败
检查 OpenViking ResourceService 是否正常运行。
