DeerFlow 支持通过 MemoryManager 接入 OpenViking 作为长期记忆后端。接入后，DeerFlow 会将对话消息写入 OpenViking，并在模型调用前通过 OpenViking 进行记忆召回，再注入到上下文中。

## 步骤 1：配置 OpenViking 鉴权信息

在 DeerFlow 项目根目录下编辑 `.env` 文件，添加 OpenViking USER API Key：

```bash
OPENVIKING_API_KEY=[TODO]your-api-key
```

## 步骤 2：修改 DeerFlow 的 memory 配置

打开项目根目录下的 `config.yaml`，找到 `memory:` 配置段，将默认的 DeerMem 配置替换为 OpenViking 配置：

```yaml
memory:
  enabled: true
  injection_enabled: true
  shutdown_flush_timeout_seconds: 30
  manager_class: openviking
  mode: middleware
  backend_config:
    base_url: [TODO]openviking-base-url
    owner_user_id: default
    api_key_env: OPENVIKING_API_KEY
    startup_policy: fail_fast
    failure_policy:
      read: fail_open
      write: log_and_drop
    retrieval:
      top_k: 8
      score_threshold: 0.25
      max_injection_chars: 12000
      content_mode: overview
      injection_query: >-
        user profile preferences important entities events ongoing goals
        constraints and prior decisions
```

## 步骤 3：重启 DeerFlow

保存 `.env` 和 `config.yaml` 后，重新启动 DeerFlow：

```bash
make dev
```

## 步骤 4：验证 OpenViking 是否接入成功

在项目根目录下查看 Gateway 日志：

```bash
grep -i "memory manager resolved\|openviking\|deermem" logs/gateway.log
```

成功日志示例：

```text
Memory manager resolved: OpenVikingMemoryManager (manager_class='openviking')
HTTP Request: GET [TODO]openviking-base-url/health "HTTP/1.1 200 OK"
```

## 步骤 5：验证写入与召回

可通过以下日志确认写入和召回是否正常：

```bash
grep -Ei "messages/batch|commit|search/find|has_memory" logs/gateway.log | tail -100
```

成功日志示例：

```text
/messages/batch "HTTP/1.1 200 OK"
/commit "HTTP/1.1 200 OK"
/search/find "HTTP/1.1 200 OK"
has_memory=True
```

## 故障排查

| 现象 | 原因 | 修复 |
|------|------|------|
| DeerFlow 启动失败，提示 OpenViking 配置错误 | `config.yaml` 中 OpenViking 配置不完整或格式错误 | 检查 `config.yaml` 中是否已配置 `manager_class: openviking`，并确认 `base_url`、`api_key_env` 等字段正确 |
| DeerFlow 未接入 OpenViking | `memory.manager_class` 未改为 `openviking`，或修改配置后未重启服务 | 保存配置后重新启动 DeerFlow，并确认日志中出现 `OpenVikingMemoryManager` |
| 远程认证失败，返回 401 或 403 | OpenViking API Key 缺失、错误或无权限 | 检查 `.env` 中的 `OPENVIKING_API_KEY` 是否正确 |
| 检索失败，但 DeerFlow 仍继续回复 | 当前配置采用 `read: fail_open`，属于预期行为 | OpenViking 检索失败时不会注入记忆，但不会影响主 Agent 正常回复 |
| 回复已生成，但记忆写入失败 | 当前配置采用 `write: log_and_drop`，写入失败会被记录到日志中 | 检查并修复 OpenViking 服务、网络和鉴权配置，后续新消息可继续写入 |
| 已写入消息，但页面未立即看到记忆 | OpenViking 的摘要和记忆提取是异步完成的 | 等待后台任务完成后再查看 |
| 服务关闭时仍有记忆操作未完成 | 系统会在 `shutdown_flush_timeout_seconds` 配置的时间内等待其完成 | 若等待超时，或 OpenViking 在关闭期间不可用，部分记忆写入可能无法完成。可适当调大该配置，并检查关闭期间 OpenViking 的网络和服务状态 |
