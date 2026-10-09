# CodeBuddy Code CLI 的 OpenViking 记忆插件

为 [CodeBuddy Code](https://cnb.cool/codebuddy/codebuddy-code) 提供长期语义记忆，由
[OpenViking](https://github.com/volcengine/OpenViking) 驱动。

插件通过生命周期 hooks 读写 OpenViking，并通过 MCP 暴露 OpenViking 的工具。两者共用同一份
解析后的配置，因此 hooks 与 MCP 对「服务器 / account / user」的理解永远一致。

## 状态

针对 CodeBuddy Code **v2.163.0** 构建。host 契约是**用一次性探针插件在真实 CLI 会话里实测**
得出的，不是照文档抄的——完整事实、方法与仍未验证的项见
[`docs/HOST-CONTRACT.md`](docs/HOST-CONTRACT.md)。要点：

- hooks 在交互 TUI 与 headless（`-p`）下都会触发，但 **`UserPromptSubmit` 与 `SessionEnd`
  只在交互 TUI 触发**；
- `additionalContext` 信封与 Claude Code 字节级兼容，且**未知 JSON 字段会被容忍**；
- `PreToolUse` **没有对模型可见的提示通道**（`permissionDecisionReason` 从未进入 transcript），
  所以 URI 守卫只能 `deny`；
- hook 超时会被杀，但**它的子孙进程不会被杀** ⇒ 分离式异步写是安全的。

## 目录结构

```
.codebuddy-plugin/plugin.json   清单（只有 `name` 必填）
hooks/hooks.json                hook 注册（用 ${CODEBUDDY_PLUGIN_ROOT}）
.mcp.json                       本插件的 MCP server            (P2)
servers/mcp-proxy.mjs           stdio MCP 代理，转发到解析后的配置   (P2)
scripts/                        手写的 hook 入口
scripts/lib/                    手写的插件本地辅助模块
scripts/shared/                 由 examples/memory-plugin-shared/lib 生成 —— 不要手改
skills/                         由 examples/skills 生成          (P6)
docs/HOST-CONTRACT.md           实测得到的 host 契约与方法
```

阶段状态：**P0（契约）与 P1（骨架）已完成** —— 插件可加载、配置可解析；hook 入口、MCP、
capture 与 skills 分别在 P2–P6 落地。

## 本地加载

```bash
codebuddy --plugin-dir examples/codebuddy-memory-plugin
```

`--plugin-dir` 是会话级的：不写任何 CodeBuddy 配置；去掉参数并删掉目录即可不留痕迹。
若要长期生效，可加一个本地 marketplace 并装进 user 作用域。

## 配置

解析顺序（与所有 OpenViking 记忆插件一致）：

```
OPENVIKING_* 环境变量 → 工作区 .openviking/config*.json → ovcli.conf 的 `plugin.codebuddy`
→ ovcli.conf 的 `plugin` → ov.conf 的 `codebuddy` 段 → schema 默认值
```

连接与凭据始终来自 `OPENVIKING_URL` / `OPENVIKING_API_KEY`（或
`OPENVIKING_BEARER_TOKEN`），其次是 `ovcli.conf`，最后是 `ov.conf` 的 `server` 段。

启停：`OPENVIKING_MEMORY_ENABLED=0|1`，或 `ov.conf` 里的 `codebuddy.enabled`。两者都没有时，
仅当 `ov.conf` 或 `ovcli.conf` 存在才算启用。

调试日志：`OPENVIKING_DEBUG=1`（或 `codebuddy.debug: true`）会把 JSON Lines 写到
`~/.openviking/logs/cb-hooks.log`（可用 `OPENVIKING_DEBUG_LOG` 覆盖路径）。

## 命名

harness id 是 **`codebuddy`**。OpenViking 服务端的日志导入子系统里有一个 `workbuddy`
适配器解析同样的 transcript 格式，但那个名字属于服务端的 ingest 命名空间——它不是 harness
id，**不要**注册进 `HARNESS_KEYS`。
