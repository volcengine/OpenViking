# openviking-opencode

用于 [OpenCode](https://opencode.ai) 的 OpenViking 插件。将您索引的代码仓库注入到 AI 的上下文中，并在需要时自动启动 OpenViking 服务器。

## 前置要求

安装最新版的 OpenViking 并配置 `~/.openviking/ov.conf`：

```bash
pip install openviking --upgrade
```

```json
{
  "storage": {
    "workspace": "/path/to/your/workspace"
  },
  "embedding": {
    "dense": {
      "provider": "openai",
      "model": "your-embedding-model",
      "api_key": "your-api-key",
      "api_base": "https://your-provider/v1",
      "dimension": 1024
    },
    "max_concurrent": 100
  },
  "vlm": {
    "provider": "openai",
    "model": "your-vlm-model",
    "api_key": "your-api-key",
    "api_base": "https://your-provider/v1"
  }
}
```

对于其他提供商（火山引擎、Anthropic、DeepSeek、Ollama 等），请参阅 [OpenViking 文档](https://www.openviking.ai/docs)。

启动 OpenCode 之前，请确保 OpenViking 服务器正在运行。如果尚未启动：

```bash
openviking-server > /tmp/openviking.log 2>&1 &
```

## 在 OpenCode 中使用

将插件添加到 `~/.config/opencode/opencode.json`：

```json
{
  "plugin": ["openviking-opencode"]
}
```

重启 OpenCode — 技能会自动安装。

**索引仓库**（直接在聊天中询问）：
```
"将 https://github.com/tiangolo/fastapi 添加到 OpenViking"
```

**搜索** — 仓库索引完成后，AI 会在相关时自动搜索它们。您也可以显式触发：
```
"FastAPI 如何处理依赖注入？"
"使用 openviking 查找 JWT 令牌如何验证"
```
