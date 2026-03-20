# 为 OpenCode 安装 OpenViking Memory Plugin

这个示例把 OpenViking 暴露为 OpenCode 可直接调用的记忆工具，并自动把当前对话同步到 OpenViking Session 中。

安装完成后，你可以在 OpenCode 中使用这些工具：

- `memsearch`
- `memread`
- `membrowse`
- `memcommit`

---

## 机制说明

这个示例使用的是 OpenCode 的 tool 机制，把 OpenViking 能力显式暴露成 Agent 可调用的工具。

更具体一点：

- Agent 会看到 `memsearch`、`memread`、`membrowse`、`memcommit` 这些显式工具
- 只有在 Agent 主动调用这些工具时，OpenViking 的内容才会被读取回来
- 插件还会在后台把 OpenCode session 映射到 OpenViking session，并在合适的时候触发记忆提取

这个示例的重点是显式 memory 访问、类文件系统浏览，以及会话到长期记忆的自动同步。

---

## 前置条件

你需要先准备：

- 已安装 OpenCode
- 已启动 OpenViking HTTP Server
- 可用的 OpenViking API Key（如果服务端启用了认证）

建议先确认 OpenViking 服务正常运行：

```bash
openviking-server --config ~/.openviking/ov.conf
```

如果你已经在后台启动了服务，也可以直接检查健康状态：

```bash
curl http://localhost:1933/health
```

---

## 安装步骤

OpenCode 官方文档更推荐把插件放在：

```bash
~/.config/opencode/plugins
```

### Step 1: 创建插件目录

```bash
mkdir -p ~/.config/opencode/plugins
```

### Step 2: 复制示例文件

在 OpenViking 仓库根目录执行：

```bash
cp examples/opencode-memory-plugin/openviking-memory.ts ~/.config/opencode/plugins/openviking-memory.ts
cp examples/opencode-memory-plugin/openviking-config.example.json ~/.config/opencode/plugins/openviking-config.json
cp examples/opencode-memory-plugin/.gitignore ~/.config/opencode/plugins/.gitignore
```

复制后，插件目录里应该至少有这些文件：

```text
~/.config/opencode/plugins/
├── .gitignore
├── openviking-config.json
└── openviking-memory.ts
```

### Step 3: 配置插件

编辑：

```bash
~/.config/opencode/plugins/openviking-config.json
```

示例配置：

```json
{
  "endpoint": "http://localhost:1933",
  "apiKey": "",
  "enabled": true,
  "timeoutMs": 30000,
  "autoCommit": {
    "enabled": true,
    "intervalMinutes": 10
  }
}
```

字段说明：

- `endpoint`: OpenViking 服务地址
- `apiKey`: 可留空，推荐用环境变量提供
- `enabled`: 是否启用插件
- `timeoutMs`: 普通请求超时时间
- `autoCommit.intervalMinutes`: 自动提交 session 的周期

### Step 3.5: 关于插件注册

这个插件不需要额外写进 `~/.config/opencode/opencode.json`。

原因是 OpenCode 会自动扫描 `~/.config/opencode/plugins/` 下面的一级 `*.ts` / `*.js` 文件，`openviking-memory.ts` 放在这个目录顶层即可被发现。

### Step 4: 配置 API Key

推荐使用环境变量，不要把真实 key 写进配置文件：

```bash
export OPENVIKING_API_KEY="your-api-key-here"
```

如果你使用 `zsh`，可以把它写进 `~/.zshrc`：

```bash
echo 'export OPENVIKING_API_KEY="your-api-key-here"' >> ~/.zshrc
source ~/.zshrc
```

---

## 启动与验证

配置完成后，正常启动 OpenCode 即可。

插件初始化后会：

- 对 OpenViking 做一次 health check
- 为每个 OpenCode session 自动建立对应的 OpenViking session
- 自动把用户消息和 assistant 消息写入 OpenViking
- 按周期触发后台 `commit`

你可以在会话里尝试：

```text
请用 memsearch 搜索我之前的偏好
```

或者手动触发一次记忆提取：

```text
请调用 memcommit
```

---

## 运行时文件

插件运行后，会在插件目录里生成这些本地文件：

- `~/.config/opencode/plugins/openviking-config.json`
- `~/.config/opencode/plugins/openviking-memory.log`
- `~/.config/opencode/plugins/openviking-session-map.json`

这些文件都是运行时产物，不建议提交到版本库。示例里的 `.gitignore` 已经帮你排除了它们。

如果你明确希望按工作区隔离插件，也可以把这三个文件和 `openviking-memory.ts` 一起放在工作区本地插件目录里。当前实现会把配置和运行时文件统一保存在“插件文件所在目录”。

---

## 常见问题

### 1. 插件没有生效

先确认文件位置正确：

```bash
ls ~/.config/opencode/plugins/
```

至少要能看到：

- `openviking-memory.ts`
- `openviking-config.json`

### 2. `Authentication failed`

通常是 API Key 配置不对。优先检查：

- `OPENVIKING_API_KEY` 是否已设置
- 服务端是否启用了认证
- `endpoint` 是否连到了正确的 OpenViking 服务

### 3. `Service unavailable`

说明插件连不上 OpenViking 服务。检查：

```bash
curl http://localhost:1933/health
```

如果失败，先启动：

```bash
openviking-server --config ~/.openviking/ov.conf
```

### 4. `memcommit` 很慢或经常超时

这个示例已经改成了后台 commit task 模式。一般情况下，即使记忆提取比较慢，也不应该再出现“每分钟同步重试一次”的风暴。

如果你仍然觉得慢，优先检查的是：

- OpenViking 服务端的模型配置
- 服务端所在机器的资源是否吃满
- `openviking-memory.log` 里是否有持续的 task failure

### 5. 没有抽出任何 memory

通常不是插件没工作，而是服务端提取条件不满足。优先检查：

- OpenViking 的 `vlm` 和 `embedding` 是否已正确配置
- 当前对话里是否真的有适合沉淀为 memory 的内容

---

## 相关文件

- [README.md](./README.md): English overview
- [openviking-memory.ts](./openviking-memory.ts): plugin implementation
- [openviking-config.example.json](./openviking-config.example.json): config template
