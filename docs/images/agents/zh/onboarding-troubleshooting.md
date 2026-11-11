# OpenViking 接入引导：故障排查（给 Agent 看）

[接入引导](https://docs.openviking.net/agents/zh/onboarding.md)里的命令失败时查这一页。找到对应现象，修好后重试一次；仍然失败，就告诉用户哪一步没成、影响什么、他可以怎么处理。

| 现象 | 处理 |
|---|---|
| `服务拒绝了该 API key（HTTP 401/403）` 或 `该服务需要 API key`（英文 `The server rejected the API key`、`The server requires an API key`） | key 错误、已失效，或属于其他账号。请用户到 OpenViking 控制台（用户管理 → API Key）重新复制 key，在他自己的终端里用新的 `--api-key` 重跑安装命令。自部署服务要用 user key，root key 不能读写记忆。 |
| `无法连接 <url>`（英文 `Cannot reach`） | 运行 `curl -sS -o /dev/null -w '%{http_code}\n' <url>/health`。只要返回 HTTP 状态码（包括 401），服务就是可达的。超时或连接错误说明被代理、VPN 或防火墙挡住了，请用户处理。 |
| 当前宿主出现 `未找到 … CLI，跳过：<命令>`（英文 `CLI not found; skipping`，例如 `未找到 Codex 格式 CLI，跳过：codex`） | 安装器所在的 shell 找不到这个宿主的命令，常见原因是 `PATH` 只在交互式 shell 里设置。请用户在自己的终端里（能直接运行该命令的地方）运行他原始消息中的安装命令，然后从步骤 1 继续。不要把命令贴回给用户，命令里有 key。 |
| 没有 Node.js，或版本低于 18（pi 需要 22.19 及以上） | 请用户安装或升级 Node.js，然后重跑安装器。 |
| 沙箱拦截了联网或写入主目录 | 请用户批准在沙箱外运行。不要绕过沙箱。 |
| Codex 里 `curl` 返回退出码 7 或 6 | 多半是沙箱拦住了联网，不是网站挂了。申请在沙箱外运行这条命令。 |
| 宿主拒绝执行 `curl … \| bash` | 请用户在自己的终端里运行他原始消息中的安装命令，然后从步骤 1 继续。不要把命令贴回给用户，命令里有 key。 |
| 当前宿主不在 `完成` 里，`未完成` 里也没提到它 | 安装器没检测到当前宿主。只为它重跑一次，`<值>` 取接入引导附录 A 的 `--harness` 列：`curl -fsSL https://openviking.ai/install \| bash -s -- --yes --harness <值>,cli`。已保存的连接会保留，不用再带 key。然后从步骤 1 继续。 |
| Windows | 安装器只支持 macOS 和 Linux。告诉用户后停止。 |
| docs.openviking.net 打不开 | 换成 docs.openviking.ai，路径不变。安装器本身会自动尝试两个站点。 |
| 找不到 `ov` 命令 | 用 `--yes --harness cli` 重跑安装器，已保存的连接会保留。`ov` 已安装但不在 `PATH` 里时，把 `$(npm prefix -g)/bin` 加进 `PATH`；安装器回退到 `~/.local` 时则是 `~/.local/bin`。 |
| `ov` 提示需要先设置显示语言 | 运行 `ov language zh-CN`（或 `ov language en`）。 |
| DeepSeek Harness：`dsh plugin add` 失败 | 安装 pnpm（`npm i -g pnpm`），再用 `--harness dsh` 重跑安装器。 |
| `安装校验失败`（英文 `Installation validation failed`） | 重跑当前宿主的安装。仍然失败，把失败的检查项告诉用户。 |
| 导入脚本提示 `not supported yet` | 脚本不能直接读这个宿主的历史，按[其他宿主：把会话转成 JSONL](https://docs.openviking.net/agents/zh/onboarding-other-hosts.md)转换后用 `--input` 导入。 |
| 导入脚本提示 `--session: no session matches …` | 给的 id 不对，什么都没有上传。重新运行 `--list`，用它打印的 id。 |
| 重启后没有召回 | 先分清是库里没有相关内容，还是 hook 没运行。Codex 和 TraeCode CLI：在 `/hooks` 里信任 OpenViking 的 hook，在 `/plugins` 里确认 `openviking-memory` 已启用。Cursor 和 TRAE：应用没有完全退出。其他情况运行诊断：Claude Code 用 `/openviking-memory:ov` 或 `ov-memory-doctor` skill；Codex 用 `$ov-memory-doctor` skill；Cursor、TRAE、TRAE CN、ZCode 运行 `node ~/.openviking/agent-integrations/<cursor\|trae\|trae-cn\|zcode>/scripts/ov-memory-doctor.mjs`；pi 用 `/viking`；DeepSeek Harness 运行 `dsh --profile <profile> --dump-config`（`<profile>` 是安装器输出的那个），输出里应包含 `openviking-memory`。命令行宿主还可以在启动前设置 `OPENVIKING_DEBUG=1`，hook 日志会写到 `~/.openviking/logs/`；从程序坞或启动器打开的桌面应用读不到 shell 里设置的环境变量。 |
