# openviking-opencode

用于 [OpenCode](https://opencode.ai) 的 OpenViking 插件。提供原生工具用于语义搜索、记忆和代码检索。

## v1.0.0 更新内容

**重大变更**：此版本将基于技能的集成替换为原生 OpenCode 工具。

- **之前 (v0.x)**：需要 `skill("openviking")` + bash `ov` 命令
- **现在 (v1.0)**：工具直接出现在代理的工具清单中 — 无需加载技能

优势：
- 工具始终对代理可见（不会遗忘技能）
- 无需 shell 命令转换开销
- 更快更可靠的执行

## 前置要求

安装 OpenViking 并配置 `~/.openviking/ov.conf`：

```bash
pip install openviking --upgrade
```

启动 OpenCode 之前，启动 OpenViking 服务器：

```bash
openviking-server --config ~/.openviking/ov.conf
```

## 安装

将插件添加到 `~/.config/opencode/opencode.json`：

```json
{
  "plugin": ["openviking-opencode"]
}
```

重启 OpenCode。

## 配置

创建 `~/.config/opencode/openviking-config.json`：

```json
{
  "endpoint": "http://localhost:1933",
  "apiKey": "",
  "account": "",
  "user": "",
  "peerId": "",
  "enabled": true,
  "timeoutMs": 30000,
  "repoContext": { "enabled": true, "cacheTtlMs": 60000 },
  "autoRecall": {
    "enabled": true,
    "limit": 6,
    "scoreThreshold": 0.15,
    "maxContentChars": 500,
    "preferAbstract": true,
    "tokenBudget": 2000
  }
}
```

环境变量会覆盖配置文件中的值：

```bash
export OPENVIKING_API_KEY="your-api-key"
export OPENVIKING_ACCOUNT="default"   # 仅信任模式
export OPENVIKING_USER="opencode"     # 仅信任模式
export OPENVIKING_PEER_ID="opencode"  # 对等范围记忆
```

## 工具

### 记忆和搜索工具

| 工具 | 描述 |
|------|------|
| `memsearch` | 跨记忆、资源和技能的语义搜索 |
| `memread` | 读取特定 `viking://` URI 的内容 |
| `membrowse` | 浏览文件系统结构（列表、树、状态） |
| `memgrep` | 内容中的模式/关键字搜索 |
| `memglob` | Glob 文件匹配 |
| `memadd` | 将远程 URL 或本地文件添加到 OpenViking |
| `memremove` | 移除 `viking://` 资源 |
| `memqueue` | 检查观察者队列状态 |
| `memcommit` | 提交会话以进行记忆提取 |

### 代码工具

| 工具 | 描述 |
|------|------|
| `codesearch` | 在索引的代码仓库中搜索符号名称 |
| `codeoutline` | 显示源文件的符号结构 |
| `codeexpand` | 返回命名符号的完整源代码 |

## 使用示例

代理会在相关时自动使用这些工具。您也可以直接请求：

```
"搜索 fastapi 仓库中的认证中间件"
→ 代理使用 memsearch，target_uri=viking://resources/fastapi/

"查找 UserService 在哪里定义"
→ 代理使用 codesearch query="UserService"

"将 https://github.com/tiangolo/fastapi 添加到 OpenViking"
→ 代理使用 memadd 及 path 和 to 参数
```

## 从 v0.x 迁移

如果您使用的是基于技能的版本：

1. 更新到 v1.0.0：`npm update openviking-opencode`
2. 从 `~/.config/opencode/skills/openviking/` 移除所有手动技能文件
3. 重启 OpenCode

代理现在会自动使用原生工具 — 无需 `skill("openviking")` 调用。

## 许可证

Apache-2.0
