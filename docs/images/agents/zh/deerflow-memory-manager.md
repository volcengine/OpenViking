DeerFlow 支持通过 MemoryManager 接入 OpenViking 作为长期记忆后端。接入后，DeerFlow 会将对话消息写入 OpenViking，并在模型调用前通过 OpenViking 进行记忆召回，再注入到上下文中。

## 步骤 1：配置 OpenViking 鉴权信息

在启动 DeerFlow 的环境中配置 OpenViking 服务地址和 API Key：

```bash
export OPENVIKING_BASE_URL="https://api.vikingdb.cn-beijing.volces.com/openviking"
export OPENVIKING_API_KEY="[TODO]your-api-key"
```

如果使用 `.env` 或部署平台的环境变量管理能力，请写入同名变量，并确认 DeerFlow 进程启动时能够读取到。

## 步骤 2：修改 DeerFlow 的 memory 配置

在 DeerFlow 的配置文件中启用 MemoryManager，并将 provider 指向 OpenViking：

```yaml
memory:
  enabled: true
  provider: openviking
  openviking:
    base_url: ${OPENVIKING_BASE_URL}
    api_key: ${OPENVIKING_API_KEY}
    auto_write: true
    auto_recall: true
    inject_recalled_memory: true
```

建议同时开启写入、召回和注入，确保 DeerFlow 能够在对话过程中持续沉淀长期记忆，并在后续任务中自动使用相关记忆。

## 步骤 3：重启 DeerFlow

保存配置后重启 DeerFlow，使新的 memory 配置生效：

```bash
pnpm dev
```

如果你通过 Docker、进程管理器或云服务部署 DeerFlow，请使用对应的重启方式，并确认新的环境变量已注入到运行时。

## 步骤 4：验证 OpenViking 是否接入成功

启动后检查 DeerFlow 日志，确认 MemoryManager 初始化成功，且没有出现 OpenViking 鉴权或网络错误。

也可以直接检查 OpenViking 服务连通性：

```bash
curl -H "Authorization: Bearer ${OPENVIKING_API_KEY}" \
  "${OPENVIKING_BASE_URL}/health"
```

返回服务状态信息即表示 OpenViking 服务可访问。

## 步骤 5：验证记忆写入与召回是否正常

在 DeerFlow 中发起一轮包含稳定事实的对话，例如：

```text
请记住：我的 DeerFlow 测试项目使用 OpenViking 作为长期记忆后端。
```

随后开启一轮新对话并询问：

```text
我的 DeerFlow 测试项目使用什么作为长期记忆后端？
```

如果 DeerFlow 能够回答 OpenViking，说明记忆写入、召回和上下文注入链路已经生效。

## 故障排查

| 现象 | 处理方式 |
|------|----------|
| MemoryManager 初始化失败 | 检查 `memory.enabled`、`provider` 和 OpenViking 配置项是否已写入 DeerFlow 实际加载的配置文件 |
| OpenViking 返回 401 / 403 | 检查 `OPENVIKING_API_KEY` 是否正确、是否过期，以及请求头是否带有 `Bearer` 前缀 |
| 记忆没有写入 | 确认 `auto_write` 已开启，并检查 DeerFlow 日志中是否有写入失败信息 |
| 记忆无法召回 | 确认 `auto_recall` 和 `inject_recalled_memory` 已开启，并确保 OpenViking 中已有相关记忆 |
| 本地可用、部署后不可用 | 检查部署环境是否正确注入 `OPENVIKING_BASE_URL` 和 `OPENVIKING_API_KEY` |
