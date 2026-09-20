# LoopX 长任务

长任务是可选功能，默认关闭。主 Agent 调用 `start_long_task` 后立即返回任务 ID，后台 Worker 继续执行；普通聊天和现有 `spawn` 不变。

## 安装与启用

运行环境：Linux/macOS、Python 3.11+、Node.js 24 LTS（LoopX 最低要求 22.18.0）。LoopX 固定为已验证的 1.0.5。

```bash
pip install 'openviking[bot,longtask]'
```

源码开发时，在仓库根目录用 `uv sync --extra bot --extra longtask` 安装当前代码。Node 需事先准备，安装 extra 不会自动安装 Node。

**不需要手动安装 Skills。** 首次启用长任务并启动 Bot 时，先检查 LoopX 和 Node，再调用 LoopX 官方命令，把安装包自带的 Skills 准备到 `<storage.workspace>/bot/longtasks/skills`（默认 `~/.openviking/data/bot/longtasks/skills`）。这个过程不下载额外软件、不自动升级、不换后端。

后续启动复用已有 Skills 并严格校验；缺失、版本不符或准备失败就报错，不自动覆盖。若首次准备中断留下了不完整目录，需要先检查并显式修复，不能靠重启跳过检查。关闭长任务时，不准备 Skills。

在现有 `ov.conf` 的 `bot` 中增加：

```json
{"longtask": {"enabled": true}}
```

启动 `vikingbot gateway`，或通过 OpenViking 的 `--with-bot` 启动。一次性/本地交互 `vikingbot chat` 不承载后台长任务；配置启用后使用该入口会明确报错。

Docker 可选构建：

```bash
docker build --build-arg INSTALL_LONGTASK=true --build-arg UV_LOCK_STRATEGY=locked -t openviking-longtask .
```

此选项额外打包 LoopX 和 Node 24；默认镜像不增加它们。启用配置后，容器首次启动自动把 Skills 准备到持久化数据目录，无需另跑安装命令。

## 使用

告诉 Bot：“启动一个长任务：……”，给清楚目标、允许操作的范围和验收条件。

| 工具 | 用途 |
| --- | --- |
| `start_long_task(objective, request_id)` | 异步创建；同一用户、会话和 request_id 重试不会重复创建 |
| `long_task(task_id, action)` | 查看状态、暂停、取消、恢复 |
| `long_task(..., action="resume", message="用户答复")` | 将用户答复保存后恢复，不改写原始目标 |

工具按渠道传入的 `sender_id` 和原会话检查任务归属；它依赖现有渠道/网关鉴权，不能替代身份认证或系统隔离。取消后需新建任务，不能恢复；暂停先禁止新调用，再通知 LoopX 停止。已经发出的外部操作不保证能撤回。

## 执行链路

```text
用户 → Main Agent → start_long_task → 返回 task_id，继续聊天
                         ↓
                  单实例后台 Worker
                         ↓
       LoopX should-run：本轮能否做、做哪个 Todo
                         ↓
       独立任务 Session / 工作目录 + OpenViking 上下文
                         ↓
           Bot Agent 有限执行 → 提交证据和下一步
                         ↓
      LoopX 写回 / 结算 / 完成回执 → 下一轮或通知用户
```

- **LoopX**：保存 Goal、Todo、执行身份、门禁和结算；Bot 只调用公开 CLI，不改其私有状态。
- **Bot**：保存任务归属、预算、工具调用记录、暂停权限和通知。不会再建一套 Goal/Todo 状态机。
- **OpenViking**：沿用 Bot 管理的身份、稳定任务 Session、上下文读取和会话同步；不作为任务状态数据库。
- **完成判断**：模型检查验收条件并引用真实工具记录；Host 提交给 LoopX，读到 `terminal_no_followup` 才通知完成。命令是否成功按沙箱返回的结构化退出状态判断，不从输出文本猜测；非零或未知退出状态不能作为成功证据。工具成功不等于产物满足业务验收，本实现不是通用业务验收器。

中间 Todo 的顺序是“完成并建立下一步 → 写回 → 结算”；最后一个 Todo 按 LoopX 协议先写回、结算，再关闭。整个过程使用同一个 turn ID。

**等待用户也是一次正式收尾**：Bot 先通过 LoopX 将当前 Todo 标为 `blocked`，确认成功后才暂停并通知用户，不补扣交付额度。用户恢复时，通过官方 `supersede` 接口把未完成工作交给后继 Todo，再继续执行；旧 Todo 保留替换关系，不能直接改回 `open`。长任务 ID、会话和已有产物不变。恢复写入结果不明时停止，不自动重复创建后继 Todo。

## 默认限制与恢复

| 配置 | 默认 |
| --- | --- |
| `max_rounds` | 每个任务 20 轮，重启不清零 |
| `model_calls_per_round` | 每轮 10 次 Provider 调用；Provider 内部网络重试不另计 |
| `tools_per_round` | 每轮 30 次工具调用，串行执行 |
| `round_timeout_seconds` | 每轮 600 秒；超时后的外部执行结果可能未知 |
| `max_no_progress_rounds` | 连续 3 轮无有效提交则暂停 |
| `cli_timeout_seconds` | 单次 LoopX CLI 60 秒 |

一个 Bot 数据目录只允许一个长任务 Host，默认单 Worker。不运行第二个管理型 Agent，不让主 Agent 持续轮询。

正常结束一轮后，任务可在进程重启后继续排队。若中断时有未完成的模型调用、工具调用或结算，任务暂停；必须检查操作记录和外部结果，不会自动重放。当前不提供自动消除这类“不确定结果”的功能。

模型只回复文字、未提交结果时，保留原 LoopX 轮次 ID；下一次尝试重新检查 LoopX 后继续该轮，不冒充已收尾。执行中暂停也保留原轮次，用户恢复时继续；只有没有未决操作才允许执行。每次尝试仍计入 Bot 的总轮次和无进展预算，成功提交后才清除原轮次 ID。

创建 LoopX 任务途中中断也不能直接恢复重建，需先检查已产生的状态，避免重复创建。

数据位于 `<storage.workspace>/bot/longtasks/`：`host.db` 保存 Host 记录，`runtime/` 保存 LoopX 运行数据，`workspaces/` 保存任务工作目录，`submissions/` 保存公开 CLI 的提交输入。备份时应一起保留。

## 首版边界

已确认：以下限制只针对长任务 Worker，不改变主 Agent 的 MCP、普通 spawn、定时任务和消息工具配置。

- 工具范围：文件、命令、搜索、已配置的 OpenViking；不加载 MCP，不提供递归 spawn、cron、主动消息或图片生成工具。
- 完成、异常和等待用户输入的通知仍由 Host 发送；这不等于给 Worker 开放主动消息工具。
- 不提供写入个人记忆的 `openviking_memory_commit`：它要求用户 peer 身份。长任务过程由 Bot 管理的任务 Session 自动同步，不冒用发起人的身份。
- 独立工作目录不是安全沙箱；隔离能力沿用 Bot 的 sandbox 配置。`direct` 仍具有宿主机用户权限，只适用于可信调用者。
- 通知经现有 Bot 消息总线发送；持久化的是入队记录，不是飞书等渠道的最终送达回执。状态查询不依赖通知是否送达。
- 后台通知使用独立 `notification` 事件，飞书等推送渠道正常发送；OpenAPI / Bot API 不把它塞进当前聊天请求，不会关闭其他回答的响应流。API 调用方通过 `long_task(..., action="status")` 查询任务结果。
- LoopX 返回当前 Host 不支持的专项修复流程时明确暂停，不跳过门禁，也不自动切换到其他运行方式。

## 验证

```bash
VIKINGBOT_LOOPX_TEST=1 python -m pytest bot/tests/test_longtask.py --no-cov
```

该测试包含真实 LoopX CLI 的两轮续跑和正式完成，以及使用确定性模型响应驱动真实 Bot 文件工具的 Worker 测试，不调用付费模型。OpenViking 会话接线另有单元测试，不能代替部署环境中的模型和 OpenViking 联调。
