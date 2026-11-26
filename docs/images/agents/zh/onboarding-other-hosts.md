# OpenViking 接入引导：其他宿主的历史导入（给 Agent 看）

导入脚本只能直接读 Claude Code 和 Codex 的历史。在其他宿主上，用户在[接入引导](https://docs.openviking.net/agents/zh/onboarding.md)步骤 3 选了导入时，先由你把这个宿主的会话转成 JSONL，再交给同一个脚本。范围选择、确认和结果说明都和接入引导里一样，这一页只讲转换。

1. 运行 `echo "${TMPDIR:-/tmp}"`，记下这个临时目录，下面写作 `<tmp>`。之后写文件、跑命令都用这个绝对路径，不要再写 `${TMPDIR:-/tmp}`。下载导入脚本：

   ```bash
   curl -fsSL https://docs.openviking.net/dl/memory-plugin-shared/import-history.mjs -o "<tmp>/ov-import-history.mjs"
   ```

2. 找到当前宿主在本机保存会话的地方。下表是提示，以本机实际为准。只读，不要改。

   | 宿主 | `--harness` | 会话位置 |
   |---|---|---|
   | TraeCode CLI 2.0 | `trae-cli` | `~/.trae/cli/sessions/`，和 Codex 相同的 rollout 格式 |
   | Kimi Code | `kimicode` | `~/.kimi-code/sessions/`（设置了 `$KIMI_CODE_HOME` 时在该目录下），每个会话的 `state.json` 带 cwd，消息在 `agents/main/wire.jsonl` |
   | OpenCode | `opencode` | SQLite 库 `~/.local/share/opencode/opencode.db`，没有 `sqlite3` 时可用 Node 22 的 `node:sqlite` 只读打开；`synthetic` 的文本是注入内容，`parent_id` 非空的是子代理会话 |
   | pi | `pi` | `~/.pi/agent/sessions/*/*.jsonl`，每个文件第一行 `{"type":"session"}` 带 id 和 cwd |
   | DeepSeek Harness | `dsh` | `~/.dsh/sessions/` |
   | Cursor、TRAE、TRAE CN、ZCode | `cursor`、`trae`、`trae-cn`、`zcode` | 未确认 |

3. 在 `<tmp>` 下写一个小转换脚本，把会话写进 `<tmp>/ov-sessions.jsonl`，每行一个会话：

   ```json
   {"id": "<宿主自己的会话 id>", "cwd": "<会话所在目录的绝对路径>", "messages": [{"role": "user", "text": "……", "created_at": "2026-10-16T08:00:00Z"}, {"role": "assistant", "text": "……", "created_at": "2026-10-16T08:00:05Z"}]}
   ```

   - `id` 用宿主记录里的原生会话 id，不要自己生成；`cwd` 用会话实际所在的目录；每条消息都带上 ISO 8601 时间。子代理的会话加上 `"subagent": true`。
   - 只放用户和助手说的话。工具调用、工具输出、系统提示、思考过程，以及宿主或插件注入的内容（例如 `<openviking-context>`、提醒、通知）都不要放。
   - 存储里的会话全部写进去，不要按项目、目录或时间筛选：脚本会按 `--cwd` 判断哪些属于这个项目（包括子目录），按 `--since` 筛时间，`--list` 和 `--session` 也照常可用。唯一要排除的是当前这段对话，因为它含有安装命令里的 key；认不出是哪一段时，排除最近 2 小时内有更新的会话。
   - 不用自己删 key，脚本会做；也不要自己去查 JSONL 里有没有 key，命令里不能出现 key 或它的任何一段。转换脚本只读本地文件、只写这一个 JSONL，不发送任何东西。
   - 检查转换结果时只看摘要，例如行数和每行的 id、cwd、消息数，不要把整个 JSONL 打印到对话里：里面有其他项目的对话。

4. 试运行。在接入引导的命令上加 `--input "<tmp>/ov-sessions.jsonl"`，`--harness` 用上表的值，例如：

   ```bash
   node "<tmp>/ov-import-history.mjs" --harness <值> --input "<tmp>/ov-sessions.jsonl" --cwd "$PWD" --since 14d --max-sessions 20 --dry-run --json
   ```

   脚本指出某一行格式不对时，改转换脚本、重新生成，再试运行。数量明显不对（例如范围内一个会话都没有，或消息数远少于实际对话）时，先检查转换，不要直接导入。要上传的是 0 个、但有状态为 `imported` 或 `live` 的会话，说明内容已经在 OpenViking 里，不是转换出错，按接入引导 3b 直接去读回。

5. 之后和接入引导里一样：按 3b 请用户确认（当前这段对话已经由转换脚本排除，不会出现在「仍在活动」里），他确认后把 `--dry-run` 换成 `--yes --wait` 正式导入，再按 3c 读回。导入完，或者决定不导入，都要删掉 JSONL 和转换脚本；写到过几个位置就删几处，再用 `ls` 确认已经删掉。

找不到会话保存在哪，或者改了两三次仍转换不对，就告诉用户这次没能导入这个宿主的历史，OpenViking 会从插件生效后的对话开始积累，然后回到接入引导的步骤 4。
