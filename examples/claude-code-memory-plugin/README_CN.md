# OpenViking Memory Plugin for Claude Code

为 Claude Code 提供长期语义记忆功能，基于 [OpenViking](https://github.com/volcengine/OpenViking) 构建。

提供一键安装的插件市场仓库：[openviking-plugins](https://github.com/Castor6/openviking-plugins)

> 移植自 [OpenClaw context-engine plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)，并适配 Claude Code 的插件架构（MCP + hooks）。

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         Claude Code                              │
└────────┬──────────────────────────────────────┬──────────────────┘
         │                                      │
    UserPromptSubmit                           Stop
    (命令 hook)                            (命令 hook)
         │                                      │
  ┌──────▼──────────┐                  ┌────────▼─────────┐
  │  auto-recall.mjs│                  │ auto-capture.mjs │
  │                 │                  │                  │
  │ stdin:          │                  │ stdin:           │
  │  user_prompt    │                  │  transcript_path │
  │                 │                  │                  │
  │ 1. 解析查询     │                  │ 1. 读取对话记录  │
  │ 2. 搜索 OV      │                  │ 2. 提取对话轮次  │
  │ 3. 排序筛选     │                  │ 3. 捕获检查      │
  │ 4. 读取内容     │                  │ 4. 会话/提取     │
  │                 │                  │                  │
  │ stdout:         │                  │ stdout:          │
  │  systemMessage  │                  │  decision:approve│
  │  (记忆内容)     │                  │  (自动捕获)      │
  └──────┬──────────┘                  └────────┬─────────┘
         │                                      │
         │         ┌──────────────┐             │
         └────────►│   OpenViking │◄────────────┘
                   │   Server     │
    MCP tools ────►│   (Python)   │
                   └──────────────┘

  ┌──────────────────────────────────────┐
  │  MCP Server (memory-server.ts)       │
  │  显式使用的工具:                      │
  │  • memory_recall (手动搜索)          │
  │  • memory_store  (手动存储)          │
  │  • memory_forget (删除记忆)          │
  │  • memory_health (健康检查)          │
  └──────────────────────────────────────┘
```

在 `SessionStart` 时，当 Claude 暴露 `CLAUDE_PLUGIN_DATA` 变量时，插件会将其 Node 运行时引导至 `${CLAUDE_PLUGIN_DATA}/runtime`。否则，它会回退到 `~/.openviking/claude-code-memory-plugin/runtime`。这使得 MCP 适配器可以在 marketplace 安装后自愈，而无需将 `node_modules` 检入插件源码树。

## 工作原理

### 运行时引导（透明，会话启动时）

1. Claude 启动会话 → `SessionStart` hook 触发
2. `bootstrap-runtime.mjs` 计算 `package.json`、`package-lock.json` 和 `servers/memory-server.js` 的哈希值
3. 如果运行时目录缺失或过期，将运行时文件复制到该目录
4. 在该运行时目录中运行 `npm ci --omit=dev`
5. 写入 `install-state.json`，以便后续会话跳过重新安装
6. MCP 启动器也可以在需要时自行引导，如果它在 `SessionStart` 之前启动

### 自动召回（透明，每轮对话）

1. 用户提交消息 → `UserPromptSubmit` hook 触发
2. `auto-recall.mjs` 从 stdin 读取 `user_prompt`
3. 调用 OpenViking `/api/v1/search/find` 搜索 `viking://user/memories` 和 `viking://agent/memories`
4. 使用查询感知评分对结果排序（叶子节点增强、偏好增强、时间增强、词法重叠）
5. 读取排名靠前的叶子记忆的完整内容
6. 通过 `systemMessage` 返回 → Claude 透明地看到 `<relevant-memories>` 上下文

### 自动捕获（透明，停止时）

1. Claude 完成回复 → `Stop` hook 触发
2. `auto-capture.mjs` 从 stdin 读取 `transcript_path`
3. 解析对话记录并提取最近的对话轮次，默认只保留用户轮次
4. 对选定的用户轮次运行捕获决策逻辑（语义模式或关键词触发）
5. 创建 OpenViking 临时会话 → 添加消息 → 提取记忆
6. 记忆自动存储，无需 Claude 工具调用

### MCP 工具（显式，按需）

MCP 服务器提供工具，用于 Claude 或用户需要显式记忆操作时：
- **memory_recall** — 手动语义搜索
- **memory_store** — 手动记忆存储
- **memory_forget** — 按 URI 或查询删除记忆
- **memory_health** — 检查服务器状态

## 与 OpenClaw 插件的区别

| 方面 | OpenClaw 插件 | Claude Code 插件 |
|------|--------------|------------------|
| 自动召回 | `before_prompt_build` hook + `prependContext` | `UserPromptSubmit` 命令 hook + `systemMessage` |
| 自动捕获 | `afterTurn` context-engine 方法 | `Stop` 命令 hook + 对话记录解析 |
| 显式工具 | `api.registerTool()` | MCP 服务器（stdio 传输）|
| 透明性 | 两者完全透明 | 两者完全透明 — 无额外 Claude 工具调用 |
| 进程管理 | 插件管理本地子进程 | 用户单独启动 OpenViking |
| 配置 | 带UI提示的插件配置模式 | 单一 JSON 配置文件 |
| JS 运行时依赖 | 打包在插件进程中 | 首次 `SessionStart` 时安装到 `${CLAUDE_PLUGIN_DATA}` 或 `~/.openviking/claude-code-memory-plugin` |

## 快速开始

### 1. 安装 OpenViking

```bash
pip install openviking
```

Mac 用户
```bash
brew install pipx
pipx ensurepath
pipx install openviking
```

### 2. 创建配置

如果还没有 `~/.openviking/ov.conf`（可通过环境变量 `OPENVIKING_CONFIG_FILE` 覆盖默认路径），请创建：

```bash
mkdir -p ~/.openviking
# 编辑 ov.conf：设置你的 embedding API key、model 等
vim ~/.openviking/ov.conf
```

#### `~/.openviking/ov.conf`（本地模式）

```json
{
  "server": { "host": "127.0.0.1", "port": 1933 },
  "storage": {
    "workspace": "/home/yourname/.openviking/data",
    "vectordb": { "backend": "local" },
    "agfs": { "backend": "local" }
  },
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "api_key": "<your-ark-api-key>",
      "model": "doubao-embedding-vision-251215",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3",
      "dimension": 1024,
      "input": "multimodal"
    }
  },
  "vlm": {
    "provider": "volcengine",
    "api_key": "<your-ark-api-key>",
    "model": "doubao-seed-2-0-pro-260215",
    "api_base": "https://ark.cn-beijing.volces.com/api/v3"
  }
}
```

> `root_api_key`：设置后，所有 HTTP 请求必须携带 `X-API-Key` 头。本地模式默认为 `null`（禁用认证）。  
> windows 系统的 workspace 路径分隔请用 / ，不要用 \ ，如 `D:/.openviking/data`

可选添加 `claude_code` 部分用于插件特定覆盖：

```json
{
  "claude_code": {
    "agentId": "claude-code",
    "recallLimit": 6,
    "captureMode": "semantic",
    "captureTimeoutMs": 30000,
    "captureAssistantTurns": false,
    "logRankingDetails": false
  }
}
```

### 3. 启动 OpenViking

```bash
openviking-server
```

### 4. 安装插件

```bash
/plugin marketplace add Castor6/openviking-plugins
/plugin install claude-code-memory-plugin@openviking-plugin
```

### 5. 启动新的 Claude 会话

```bash
claude
```

首次会话会自动准备 MCP 适配器的 Node 运行时。默认使用 `${CLAUDE_PLUGIN_DATA}/runtime`，如果 Claude 未注入 `CLAUDE_PLUGIN_DATA` 则回退到 `~/.openviking/claude-code-memory-plugin/runtime`。marketplace 安装后无需手动 `npm install`。

## 配置

使用与 OpenViking 服务器和 OpenClaw 插件相同的 `~/.openviking/ov.conf`。

通过环境变量覆盖路径：
```bash
export OPENVIKING_CONFIG_FILE="~/custom/path/ov.conf"
```

**连接信息**从 ov.conf 的 `server` 部分读取：

| ov.conf 字段 | 用作 | 描述 |
|-------------|------|------|
| `server.host` + `server.port` | `baseUrl` | 派生 `http://{host}:{port}` |
| `server.root_api_key` | `apiKey` | 认证用的 API key |

**插件覆盖**放在可选的 `claude_code` 部分：

| 字段 | 默认值 | 描述 |
|------|-------|------|
| `agentId` | `claude-code` | 用于记忆隔离的代理标识 |
| `timeoutMs` | `15000` | 召回/通用请求的 HTTP 请求超时（毫秒）|
| `autoRecall` | `true` | 每次用户提示时启用自动召回 |
| `recallLimit` | `6` | 每轮注入的最大记忆数 |
| `scoreThreshold` | `0.01` | 最小相关度分数（0-1）|
| `minQueryLength` | `3` | 跳过非常短查询的召回 |
| `logRankingDetails` | `false` | 为召回输出每个候选的 `ranking_detail` 日志；否则只输出简洁的排序摘要 |
| `autoCapture` | `true` | 停止时启用自动捕获 |
| `captureMode` | `semantic` | `semantic`（始终捕获）或 `keyword`（触发式）|
| `captureMaxLength` | `24000` | 捕获的最大文本长度 |
| `captureTimeoutMs` | `30000` | 自动捕获请求的 HTTP 请求超时（毫秒）|
| `captureAssistantTurns` | `false` | 在自动捕获输入中包含助手轮次；默认只捕获用户 |

## Hook 超时

内置 hooks 有意设计为非对称：

| Hook | 默认超时 | 说明 |
|------|---------|------|
| `SessionStart` | `120s` | 首次会话可能需要时间将运行时依赖安装到 `${CLAUDE_PLUGIN_DATA}` |
| `UserPromptSubmit` | `8s` | 自动召回应保持快速，以免阻塞提示提交 |
| `Stop` | `45s` | 给自动捕获足够时间完成并持久化增量状态 |

保持 `claude_code.captureTimeoutMs` 低于 `Stop` hook 超时，以便脚本可以优雅失败并仍能更新其增量状态。

## 调试日志

当启用 `claude_code.debug` 或 `OPENVIKING_DEBUG=1` 时，hook 日志写入 `~/.openviking/logs/cc-hooks.log`。

- `auto-recall` 现在默认记录关键阶段和简洁的 `ranking_summary`。
- 仅在需要每个候选评分日志时设置 `claude_code.logRankingDetails=true`。
- 对于深度诊断，推荐使用独立脚本 `scripts/debug-recall.mjs` 和 `scripts/debug-capture.mjs`，而不是一直开启详细的 hook 日志。

## 运行时依赖引导

插件将其运行时 npm 依赖保存在专用运行时目录中：

- 优先使用 `${CLAUDE_PLUGIN_DATA}/runtime`，回退到 `~/.openviking/claude-code-memory-plugin/runtime`
- `SessionStart` 使用 `npm ci --omit=dev` 安装或刷新依赖
- `install-state.json` 记录活动清单和服务器哈希
- MCP 启动也可以自行执行相同引导，因此首次运行安装不依赖 hook 顺序
- 如果安装失败，Claude Code 仍可使用；只有显式 MCP 工具在下次成功引导前不可用

## 插件结构

```
claude-code-memory-plugin/
├── .claude-plugin/
│   └── plugin.json              # 插件清单
├── hooks/
│   └── hooks.json               # SessionStart + UserPromptSubmit + Stop hooks
├── scripts/
│   ├── config.mjs               # 共享配置加载器
│   ├── runtime-common.mjs       # 共享运行时路径 + 安装状态助手
│   ├── bootstrap-runtime.mjs    # SessionStart 运行时依赖安装器
│   ├── start-memory-server.mjs  # 从 plugin data runtime 启动 MCP 服务器
│   ├── auto-recall.mjs          # 自动召回 hook 脚本
│   └── auto-capture.mjs         # 自动捕获 hook 脚本
├── servers/
│   └── memory-server.js         # 编译后的 MCP 服务器
├── src/
│   └── memory-server.ts         # MCP 服务器源码
├── .mcp.json                    # MCP 服务器定义
├── package.json
├── tsconfig.json
└── README.md
```

## 与 Claude Code 内置记忆的关系

Claude Code 有使用 `MEMORY.md` 文件的内置自动记忆系统。本插件与该系统**互补**：

| 特性 | 内置记忆 | OpenViking 插件 |
|------|---------|----------------|
| 存储 | 扁平 markdown 文件 | 向量数据库 + 结构化提取 |
| 搜索 | 完全加载到上下文 | 语义相似度搜索 |
| 范围 | 每项目 | 跨项目、跨会话 |
| 容量 | ~200 行（上下文限制）| 无限（服务器端存储）|
| 提取 | 手动规则 | AI 驱动的实体提取 |

## 故障排除

| 症状 | 原因 | 解决方案 |
|------|------|---------|
| 未召回记忆 | 服务器未运行 | 启动 OpenViking 服务器 |
| 自动捕获提取 0 条 | API key / model 错误 | 检查 `ov.conf` embedding 配置 |
| MCP 工具不可用 | 首次运行时安装失败 | 启动新 Claude 会话重试引导，检查 SessionStart stderr 查看 npm 失败原因 |
| 旧上下文被重复自动捕获 | `Stop` hook 在增量状态保存前超时 | 保持 `captureAssistantTurns=false`，提高 `Stop` hook 超时，并保持 `captureTimeoutMs` 低于该 hook 超时 |
| Hook 超时 | 服务器慢/不可达 | 增加 `hooks/hooks.json` 中的 `Stop` hook 超时，并调整 `ov.conf` 中的 `claude_code.captureTimeoutMs` |
| 日志太详细 | 启用了详细召回排序日志 | 正常使用时保持 `logRankingDetails=false`，使用调试脚本进行一次性检查 |

## 许可证

Apache-2.0 — 与 [OpenViking](https://github.com/volcengine/OpenViking) 相同。
