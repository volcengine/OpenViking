# OpenCode 的 OpenViking 记忆插件

OpenCode 插件示例，将 OpenViking 记忆作为显式工具暴露，并自动同步对话会话到 OpenViking。

安装指南：[INSTALL-ZH.md](./INSTALL-ZH.md)

## 机制

本示例使用 OpenCode 的工具机制将 OpenViking 功能暴露为agent可调用的显式工具。

实际上，这意味着：

- agent可以看到具体的工具并决定何时调用它们
- OpenViking 数据通过工具执行按需获取，而不是预先注入到每个提示中
- 插件还保持 OpenViking 会话与 OpenCode 对话同步，并使用 `memcommit` 触发后台记忆提取

本示例专注于 OpenCode 中的显式记忆访问、文件系统风格的浏览和会话到记忆的同步。

## 功能

- 为 OpenCode agent暴露四个记忆工具：
  - `memsearch`
  - `memread`
  - `membrowse`
  - `memcommit`
- 自动将每个 OpenCode 会话映射到 OpenViking 会话
- 将用户和助手消息流式传输到 OpenViking
- 使用后台 `commit` 任务避免重复的同步导致超时失败
- 持久化本地运行时状态以支持重新连接和恢复

## 文件

本示例包含：

- `openviking-memory.ts`：OpenCode 使用的插件实现
- `openviking-config.example.json`：配置模板
- `.gitignore`：复制到工作区后忽略本地运行时文件

## 前置要求

- OpenCode
- OpenViking HTTP 服务器
- 如果您的服务器需要身份验证，则需要有效的 OpenViking API 密钥

如果服务器尚未运行，请先启动：

```bash
openviking-server --config ~/.openviking/ov.conf
```

## 安装到 OpenCode

OpenCode 文档推荐的安装位置：

```bash
~/.config/opencode/plugins
```

使用以下命令安装：

```bash
mkdir -p ~/.config/opencode/plugins
cp examples/opencode-memory-plugin/openviking-memory.ts ~/.config/opencode/plugins/openviking-memory.ts
cp examples/opencode-memory-plugin/openviking-config.example.json ~/.config/opencode/plugins/openviking-config.json
cp examples/opencode-memory-plugin/.gitignore ~/.config/opencode/plugins/.gitignore
```

然后编辑 `~/.config/opencode/plugins/openviking-config.json`。

OpenCode 会自动发现 `~/.config/opencode/plugins` 下一级目录中的 `*.ts` 和 `*.js` 文件，因此不需要在 `~/.config/opencode/opencode.json` 中显式配置 `plugin` 条目。

如果您有意将插件放置在工作区本地插件目录中，此插件也可以使用，因为它会将配置和运行时文件存储在插件文件旁边。

推荐：通过环境变量提供 API 密钥，而不是将其写入配置文件：

```bash
export OPENVIKING_API_KEY="your-api-key-here"
```

## 配置

配置示例：

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

环境变量 `OPENVIKING_API_KEY` 优先于配置文件。

## 运行时文件

安装后，插件会在插件文件旁边创建这些本地文件：

- `openviking-config.json`
- `openviking-memory.log`
- `openviking-session-map.json`

这些是运行时生成的文件，不应提交到版本控制。

## 工具

### `memsearch`

在记忆、资源和技能中进行统一搜索。

参数：

- `query`：搜索查询
- `target_uri?`：将搜索限制在 URI 前缀，如 `viking://user/memories/`
- `mode?`：`auto | fast | deep`
- `limit?`：最大结果数
- `score_threshold?`：可选的最小分数

### `memread`

从特定的 `viking://` URI 读取内容。

参数：

- `uri`：目标 URI
- `level?`：`auto | abstract | overview | read`

### `membrowse`

浏览 OpenViking 文件系统布局。

参数：

- `uri`：目标 URI
- `view?`：`list | tree | stat`
- `recursive?`：仅用于 `view: "list"`
- `simple?`：仅用于 `view: "list"`

### `memcommit`

触发当前会话的立即记忆提取。

参数：

- `session_id?`：可选的显式 OpenViking 会话 ID

返回后台任务进度或完成详情，包括 `task_id`、分类计数 `memories_extracted` 和 `archived`。

## 使用示例

搜索然后读取：

```typescript
const results = await memsearch({
  query: "user coding preferences",
  target_uri: "viking://user/memories/",
  mode: "auto"
})

const content = await memread({
  uri: results[0].uri,
  level: "auto"
})
```

先浏览：

```typescript
const tree = await membrowse({
  uri: "viking://resources/",
  view: "tree"
})
```

强制进行会话中期提交：

```typescript
const result = await memcommit({})
```

## 审查者说明

- 插件设计为作为 OpenCode 插件目录中的一级 `*.ts` 文件运行
- 有意将运行时配置、日志和会话映射保留在仓库示例之外
- 使用 OpenViking 后台提交任务来避免长时间记忆提取期间的重复超时/重试循环

## 故障排除

- 插件未加载：确认文件存在于 `~/.config/opencode/plugins/openviking-memory.ts`
- 服务不可用：确认 `openviking-server` 正在运行并且在配置的端点可访问
- 身份验证失败：检查 `OPENVIKING_API_KEY` 或 `openviking-config.json`
- 未提取记忆：检查您的 OpenViking 服务器是否有正常工作的 `vlm` 和 `embedding` 配置
