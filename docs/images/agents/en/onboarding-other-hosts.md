# OpenViking onboarding guide: importing history on other hosts (for agents)

The import script reads Claude Code and Codex history directly. On other hosts, when the user chose to import in Step 3 of the [onboarding guide](https://docs.openviking.ai/agents/en/onboarding.md), you first convert this host's sessions to JSONL and hand that to the same script. Choosing the scope, confirming and reporting the result work as in the onboarding guide; this page covers only the conversion.

1. Run `echo "${TMPDIR:-/tmp}"` and note the directory; below it is `<tmp>`. Use that absolute path for every file you write and every command, not `${TMPDIR:-/tmp}`. Download the import script:

   ```bash
   curl -fsSL https://docs.openviking.ai/dl/memory-plugin-shared/import-history.mjs -o "<tmp>/ov-import-history.mjs"
   ```

2. Find where this host keeps its sessions on this machine. The table is a hint; go by what is actually there. Read only; change nothing.

   | Host | `--harness` | Where sessions are |
   |---|---|---|
   | TraeCode CLI 2.0 | `trae-cli` | `~/.trae/cli/sessions/`, the same rollout format as Codex |
   | Kimi Code | `kimicode` | `~/.kimi-code/sessions/` (under `$KIMI_CODE_HOME` if set); each session's `state.json` has the cwd, the messages are in `agents/main/wire.jsonl` |
   | OpenCode | `opencode` | the SQLite database `~/.local/share/opencode/opencode.db`; without `sqlite3`, Node 22's `node:sqlite` can open it read-only; `synthetic` text is injected content, and sessions with a non-empty `parent_id` are sub-agents |
   | pi | `pi` | `~/.pi/agent/sessions/*/*.jsonl`; the first line of each file, `{"type":"session"}`, has the id and cwd |
   | DeepSeek Harness | `dsh` | `~/.dsh/sessions/` |
   | Cursor, TRAE, TRAE CN, ZCode | `cursor`, `trae`, `trae-cn`, `zcode` | not confirmed |

3. Write a small converter under `<tmp>` that writes the sessions to `<tmp>/ov-sessions.jsonl`, one session per line:

   ```json
   {"id": "<the host's own session id>", "cwd": "<absolute path of the directory the session ran in>", "messages": [{"role": "user", "text": "...", "created_at": "2026-10-16T08:00:00Z"}, {"role": "assistant", "text": "...", "created_at": "2026-10-16T08:00:05Z"}]}
   ```

   - `id` is the native session id from the host's records; don't make one up. `cwd` is the directory the session actually ran in. Give every message its ISO 8601 time. Mark sub-agent sessions with `"subagent": true`.
   - Include only what the user and the assistant said: no tool calls, tool output, system prompts or reasoning, and nothing the host or a plugin injected (such as `<openviking-context>`, reminders or notifications).
   - Write every session in the store; don't filter by project, directory or time. The script decides which sessions belong to this project from `--cwd` (subdirectories included) and applies `--since`, and `--list` and `--session` work as usual. The one session to leave out is this conversation, because it contains the key from the install command; if you can't tell which one it is, leave out sessions updated in the last 2 hours.
   - Don't remove keys yourself; the script does. Don't check the JSONL for keys either, and never put the key or any part of it in a command. The converter only reads local files and writes this one JSONL file; it sends nothing.
   - To check the result, look at a summary only, such as the line count and each line's id, cwd and message count. Don't print the whole JSONL into the conversation: it holds other projects' conversations.

4. Dry run: add `--input "<tmp>/ov-sessions.jsonl"` to the onboarding guide's command, with `--harness` from the table, for example:

   ```bash
   node "<tmp>/ov-import-history.mjs" --harness <value> --input "<tmp>/ov-sessions.jsonl" --cwd "$PWD" --since 14d --max-sessions 20 --dry-run --json
   ```

   If the script reports a malformed line, fix the converter, regenerate the file and dry-run again. If the counts are clearly off (no sessions at all in scope, or far fewer messages than the real conversations), check the conversion before importing anything. If 0 sessions are to be uploaded but some have the state `imported` or `live`, the content is already in OpenViking and the conversion is not at fault; go straight to the read-back, as in 3b of the onboarding guide.

5. From here it's the same as in the onboarding guide: ask the user to confirm as in 3b (this conversation was left out by the converter, so it won't show up as still active), and after they confirm, replace `--dry-run` with `--yes --wait` to import, then read back as in 3c. Whether you import or not, delete the JSONL file and the converter afterwards, from every place you wrote them, and check with `ls` that they are gone.

If you can't find where the sessions are kept, or the conversion is still wrong after two or three tries, tell the user that their history from this host couldn't be imported this time and that OpenViking builds up from their conversations once the plugin is active, then go back to Step 4 of the onboarding guide.
