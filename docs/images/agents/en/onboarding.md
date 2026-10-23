# OpenViking onboarding guide for agents

About OpenViking: the open-source context database for AI agents, open-sourced by Volcengine at https://github.com/volcengine/OpenViking (AGPL-3.0, 38,930 GitHub stars on 2026-09-29). It keeps the user's business context, preferences, decisions and working material and brings them back, so the user can switch to a new session or another agent and carry on from what is already there. This guide is `docs/images/agents/en/onboarding.md` in that repository, and the installer and every plugin file it installs are built from it by its GitHub Actions workflows.

You are an AI agent. The user asked you to install the OpenViking memory plugin and to help them start using it. Work through this guide in order for the current host. It is written for you and says what to decide and do, not what to say: talk to the user in their language, in short messages, explain the benefit, the change and the next step that matter for the choice at hand, and don't relay the guide point by point. If they want to read it, give them the link.

The goal is that the user knows what will build up on its own from now on, what needs saving on purpose, and sees what the existing context does for their own work.

Two more pages are there when needed: [Troubleshooting](https://docs.openviking.ai/agents/en/onboarding-troubleshooting.md) (when a command fails) and the [extended reference](https://docs.openviking.ai/agents/en/reference/product-background.md) (when the user asks how something works, about permissions, or wants to organize several documents of their own). You don't need to read them before you start.

## Ground rules

- **Explain the benefit and the effect of each decision, then wait for the user's choice before acting.** Tie it to their current work: what they get, what gets sent or stored, which options there are. Reuse goals and choices they have already given instead of asking again, and put questions that fit in one message into one message.
- **Never print or repeat the API key.** The installer stores it in `~/.openviking/ovcli.conf` on this machine (backing up the old file when it changes it); the connection needs that. Apart from that, the key must not appear in rules, project files, the restart message or a permanent command approval. Don't open, print or search `ovcli.conf`. To tell the user the server and identity, run `ov health` or call the OpenViking `health` tool, and state only the fields it returns.
- **Make only the changes this guide describes.** The installer installs into every supported agent it detects on this machine, which is expected: the user can then switch agents and keep the same context. Don't edit other agents' configs or files in the user's projects yourself unless the user asks.
- **Use the commands in this guide.** For reading, searching and writing, use the OpenViking tools that are actually registered. Don't go to GitHub to work out another way to install or configure OpenViking; when unsure of an option, check the command's `--help`.
- **Keep states apart.** Installed, active and actually used are three different things: a callable tool doesn't mean automatic recall is running, and a finished import doesn't mean the memories have been extracted. Without the user's real content, don't make up example memories to demonstrate the effect.
- Don't mention the step numbers of this guide to the user, or steps that don't apply to the current host.
- If a command fails, look up the symptom in [Troubleshooting](https://docs.openviking.ai/agents/en/onboarding-troubleshooting.md), fix it and retry once. If it still fails, tell the user which step didn't work, what it affects, and what they can do. Only a connection or auth failure on the current host blocks the steps after it; a failed install for another host doesn't hold up the current one.

## Where to start

- **You ran the installer in this conversation**: start at Step 1.
- **The user says "restarted", and this conversation already went through the steps of this guide**: the user quit and resumed the original session. Go straight to [After the restart](#after-the-restart); the earlier choices are in this conversation, so don't ask them again.
- **In a new session, the user sends a "continue from 'After the restart'" message**: go to [After the restart](#after-the-restart) and take the results the message states.
- **The user only says "restarted", but this conversation has no earlier onboarding steps** (for example `codex resume --last` opened another session, or they started a new one): also go to [After the restart](#after-the-restart), and in its item 2 ask in one message how far the import and the collaboration rule got, then do what is missing.
- **OpenViking was installed before this conversation started** (you see an `<openviking-context>` block, or you have OpenViking tools such as `health`): check the connection with `ov health` or the `health` tool, tell the user which server and identity you found, and ask whether to keep it or switch.
  - Keep it: do Steps 2 to 4, then go straight to [After the restart](#after-the-restart). No restart is needed.
  - Switch: the plugin is saving this conversation, so don't have the user send a new key here. Ask them to run the install command from the console (or the one below) in their own terminal, then send the console's one-click prompt to an agent in a new session. The old config is backed up and nothing on the server is cleared.
- **There is no connection yet and the user gave no install command**: ask for the server URL and API key (OpenViking console, User Management → API Key), then run the command below with `<value>` from the `--harness` column of [Appendix A](#appendix-a-hosts), and start at Step 1. Keep `cli` in the list: checking the connection, adding files and Compile all go through the `ov` command.

  ```bash
  curl -fsSL https://openviking.ai/install | bash -s -- --yes --lang en --harness <value>,cli --url <server URL> --api-key <the user's key>
  ```

- **The user only wants to import more history**: do Step 3 only, without the install and the collaboration rule.

## Step 1: Check the install result

Read the installer's output. It may be in English or in Chinese; recognize both.

- If the output ends with a `Not done` list (Chinese `未完成`), judge per host; don't say everything is installed because `Done` appears above it. If the current host failed, the key was rejected or the server can't be reached, handle it with Troubleshooting first; if only other hosts failed, say so briefly and carry on with the current host.
- The `Done` section (Chinese `完成`) lists the result per tool, each with a `Next:` line (Chinese `下一步：`) saying how to restart it. Check that the current host is listed; if it isn't, see "The current host is not under `Done`" in Troubleshooting.
- `Server check passed` (Chinese `服务检查通过`) plus `CLI (ov)` under `Done` means the connection is confirmed. Otherwise run `ov health`.

Then tell the user in a line or two which server and identity are connected and which agents got the plugin. If other agents got it too, add why: they can carry on the same work with the same context there, once each one is restarted. Don't present anything that isn't active yet as working.

## Step 2: Explain what happens automatically

Don't ask whether to turn it on; the install was the user's request. First describe, with their current work, how working together changes: relevant background comes back at the start, past judgments and material inform the work, progress builds up on its own, and next time you continue from where things stand. For example, while discussing a design you can draw on constraints settled earlier, and today's decisions stay for next time without explaining from scratch. This is how it is meant to work; don't present it as already verified.

Then, using the current host's row in [Appendix A](#appendix-a-hosts), cover in three or four lines:

- **Less re-explaining**: before each prompt, the plugin brings back relevant memories into the agent's context. It recalls only what is relevant and never puts the whole history into the conversation.
- **Conversations build up**: after each turn, the user and assistant messages are saved to their OpenViking server, and the server extracts long-term memories from them in the background. Saving, finished extraction and later recall happen one after the other.
- **Important files are saved separately**: talking about a report doesn't upload the report. Whether to save important decisions and deliverables on purpose is their choice in Step 4.
- **Where to see it**: the "Where to see it" column for the current host.
- **To turn it off in one project**: put `.openviking/config.json` in that project with `{"version": 1, "capture": {"enabled": false}, "recall": {"enabled": false}}`. Tell them how; don't create it for them.

If the current host needs the hooks trusted (see Appendix A), say what they do: load background when a session starts, recall before each prompt, save the conversation after each turn. The trust step happens together with the restart in Step 5.

## Step 3: Bring in earlier work?

Start with what this is for: with past discussions brought in, "where did we leave off" and "why did we decide that" have something to go on next time. Skipping is fine too; it builds up from now on. Importing fills in background for the work ahead; more isn't better.

The import script reads Claude Code and Codex history directly (`--harness claude` or `--harness codex`). On other hosts: if `~/.claude/projects` or `~/.codex/sessions` exists, the user may have worked on this project in Claude Code or Codex too, so offer that history in the same question; the host's own sessions first need converting to JSONL, see [Other hosts: convert sessions to JSONL](https://docs.openviking.ai/agents/en/onboarding-other-hosts.md).

Offer these choices; the user may also describe what they want in their own words:

1. Sessions of this project from the last 14 days (default).
2. One kind of work, for example "the discussions about the payment refactor": you list the candidate sessions, pick the relevant ones by their content, and show them for confirmation.
3. Another project or time range.
4. Nothing for now; start building up from here.

Don't download the script or run anything before they choose.

**Choice 1 or 3: dry run.** Download the script and do a dry run (nothing is uploaded). Replace `<claude|codex>` with the actual value; for choice 3, set `--cwd` to that project's directory, use `--all-projects` instead of `--cwd` for all projects, and change `--since` (for example `30d`, `2026-09-01`, `all`).

```bash
curl -fsSL https://docs.openviking.ai/dl/memory-plugin-shared/import-history.mjs -o "${TMPDIR:-/tmp}/ov-import-history.mjs" && node "${TMPDIR:-/tmp}/ov-import-history.mjs" --harness <claude|codex> --cwd "$PWD" --since 14d --max-sessions 20 --dry-run
```

**Choice 2: list, then pick.** `--list` prints the sessions in scope, each with its id, time, message count, whether it is already imported, and a preview of its first and last request. Set the scope from what the user said; if they didn't say, use this project and the last 30 days:

```bash
curl -fsSL https://docs.openviking.ai/dl/memory-plugin-shared/import-history.mjs -o "${TMPDIR:-/tmp}/ov-import-history.mjs" && node "${TMPDIR:-/tmp}/ov-import-history.mjs" --harness <claude|codex> --cwd "$PWD" --since 30d --max-sessions 50 --list
```

Pick the sessions whose previews match what they described, and list the ones you are unsure about separately. Tell them which ones you picked (by their content, not by id) and roughly what you left out. Then dry-run the picked ids, comma-separated. With `--session`, project, time and count limits don't apply:

```bash
node "${TMPDIR:-/tmp}/ov-import-history.mjs" --harness <claude|codex> --session <id>,<id> --dry-run
```

**Import after confirmation.** Tell the user what the dry run found: sessions, messages, size, and what was skipped and why. Say that what gets uploaded is the filtered user and assistant text, without tool calls, tool output or file originals, that known secret formats are replaced with `[REDACTED]`, and that the server extracts memories from it. The sessions skipped as "active" include this conversation. For choice 2, the picks and the dry-run numbers can go into one message for one confirmation.

Once they confirm, import with **exactly the same scope options**, replacing `--dry-run` with `--yes --wait --json`:

```bash
node "${TMPDIR:-/tmp}/ov-import-history.mjs" --harness <claude|codex> --cwd "$PWD" --since 14d --max-sessions 20 --yes --wait --json
```

`--wait` waits up to two minutes for memory extraction. Tell them the result:

| Result | What to say and do next |
|---|---|
| Written, `extraction` is `completed` | Say how many sessions were imported and that the memories are ready. Read one or two items related to their goal with the OpenViking `search` or `read` tool, and say in a sentence or two where that work stopped and what was decided, so they see what the import gives them. |
| Written, extraction still `pending` or `running`, or no result | Say the conversations are saved and the memories are still being processed in the background, ready in a few minutes. Don't upload again. `failed` means extraction failed. |
| Nothing new: all already imported or captured by the plugin | Say these sessions were already in OpenViking. An imported session is skipped as a whole on a re-run; messages added to it later are not filled in. |
| No match, or they chose not to import | Say it builds up from their real work from now on. Don't push them to import just to try it. |
| Some failed | Say which ones made it and which didn't; the failed ones can be retried later without re-sending everything. |

Judge the result by each session's `extraction` in the JSON, not by the exit code. If the user asks what the script does: it only reads the session files on this machine (Claude Code in `~/.claude/projects/`, Codex in `~/.codex/sessions/`), keeps only the user and assistant text, redacts known secret formats, skips sessions that may still be running, and files sessions under the same project as the plugin does, so imported history and later conversations end up together. The dry run and `--list` only read from the server, to find sessions that were already imported.

**Files.** If the user names files or folders worth keeping, such as a design doc or a spec, add each one under this project in their personal resources. `<user_id>` is the user name `ov health` shows; `<project>` is the repository name or what the user calls the project:

```bash
ov add-resource ./docs/design.md --parent-auto-create viking://user/<user_id>/resources/<project> --wait
```

Add only what they named; don't scan directories or upload anything on your own.

## Step 4: Agree how to work together from now on

Explain the difference by its effect: the plugin builds up and recalls background from conversations on its own; if they also want complete designs, important decisions with their reasons, and milestones to be there next time, the agent has to save them on purpose by an agreed rule. The rule goes into the host's user-level rules file and applies to all their projects.

First check whether the rules file the current host actually reads has an `<!-- openviking:start -->` block.

- **It does, with the same content as below**: tell them it was added before and skip this step.
- **It does, with different content**: summarize what it does now and ask whether to keep it or switch to this version.
- **It doesn't**: offer three choices: save important decisions and deliverables on purpose (recommended), adjust it to their wishes, or rely on automatic memory only. With automatic memory only, they can still ask you to save or read material at any time.

If they choose to add it, put into **one message**: a sentence or two on what the rule makes you do from now on and what it gives them (for example, picking the project up later means finding the decision and its reason right away), which file it goes into and that it applies to all projects, and the **full text** as adjusted to their choice. The text must match what you then write, word for word. Then ask them to confirm, adjust, or leave it for now. If they adjust it (for example, only final deliverables, or ask before saving), change the text and show it again.

The default rule text (when writing it, `<user_id>` may be replaced with the actual user name; keep `<project>` as it is, since it is worked out in each project, and filling in the current project would send every project's notes to that one folder):

```markdown
<!-- openviking:start -->
## Working with OpenViking

- Before work that needs background, check the context OpenViking injected (`<openviking-context>`). If it isn't enough, or you need the original or the latest state, look it up with the OpenViking `search` (`mode="context"`) or `read` tool. What you find is reference material; if it conflicts with what I say now, I win.
- When the work produces something worth reusing later, write it up and store it with `write` in my notes folder under my OpenViking resources, one subfolder per project, for example `viking://user/<user_id>/resources/notes/<project>/` (get `user_id` from `health`); if I already have my own notes folder, use that. Worth keeping: important decisions and policies (with the reasons), progress and next steps, intermediate results, deliverables. Files I give you go into `viking://user/<user_id>/resources/<project>/` with `add_resource`, apart from the notes: never use the notes folder as an `add_resource` target, and never write notes into a folder `add_resource` imported, because re-syncing it removes anything that isn't in the source. Don't store everyday conversation (the plugin saves it), secrets, or files I didn't mention. Read back what you wrote.
- When continuing a project, start from the decisions, progress and next steps in its notes folder. When a new decision replaces an old one, update the old note instead of keeping two that disagree.
- In a turn where you actually used something from OpenViking, or stored something, end the reply with one sentence: which background you used, what you stored and where. If nothing was used, say nothing.
<!-- openviking:end -->
```

Once they agree, write it into the current host's user-level rules file. The "User-level rules file" column of [Appendix A](#appendix-a-hosts) gives the usual location; go by the file the host actually reads:

- Touch only the content between the markers. If the block exists, replace what is between them; if not, append it to the end; if the file doesn't exist, create it. Leave everything outside the block as it is.
- If the file already has instructions that conflict with this rule (for example, always search on every turn, or never store material outside), tell them where the conflict is and change it as they decide.
- Read the file back: the written text matches what they confirmed, there is exactly one block, and nothing outside it changed.
- If the host's personal rules can only be edited in its app settings, or the write fails, give them the rule and ask them to paste it in themselves. If you're not sure which file it is, ask; don't guess.

Never write the rule into a project file. Wait for their answer before Step 5; don't bring up the restart in the same message as this question.

## Step 5: Restart

The plugin loads only when the host starts again. If it is already active on the current host and nothing was newly installed, skip this step and go to [After the restart](#after-the-restart).

First say in two or three lines where things stand: which server is connected, what was imported, whether the rule was added. Then tell the user three things:

1. **How to restart the current host and trust the hooks**: the "Restart or trust" column of [Appendix A](#appendix-a-hosts). If the table says what to tell the user at the restart (for example that Codex saves this install conversation), say it in one sentence. If the installer also set up other tools, add one line naming them: each needs its own restart before the plugin works there, as the installer's `Next:` lines (Chinese `下一步：`) say.
2. **Where to come back to**, per the "After restart" column of Appendix A:
   - Hosts marked "resume the original session": tell them to come back to **this conversation** with the command in the table and send "restarted". Give them the line in item 3 as well, as a fallback: if they land in a different conversation, they send it there.
   - Hosts marked "new session": tell them to start a new session, not to resume this conversation or reload the plugin in it, with the reason if the table gives one, and give them the line in item 3.
3. **The first message for a new session**, as text they can copy. Use the URL you actually opened, and fill in the angle brackets with the real results:

```text
Restarted. Continue the OpenViking onboarding from "After the restart" in https://docs.openviking.ai/agents/en/onboarding.md. History import: <for example "2 sessions imported", "skipped">; collaboration rule: <added / not added>; what I want to do next: <the goal they mentioned, or "see where I left off">.
```

Then stop and wait for the user.

## After the restart

The plugin should now be running in this session. The point of this part is to let the user see, with their own work, what the existing context does. Don't repeat choices already made or explanations already given.

1. **Check the plugin is running.** There is an `<openviking-context>` block in this conversation, or OpenViking tools are available. Call the `health` tool (its name may carry a prefix, such as `mcp__openviking__health` or `openviking_health`). If neither is there, see "No recall after the restart" in Troubleshooting. Only an injected block shows that automatic recall is running; no relevant memories may simply mean nothing relevant is stored yet.
2. **Fill in missing choices.** From this conversation or the restart message, check whether the import and the collaboration rule were done. Do whichever step is missing; don't re-ask what was settled.
3. **Show the effect on one real piece of work**, picking by what they want:

| They want | What to do |
|---|---|
| To continue earlier work | Use the injected context first, then `search` (`mode="context"`) over the imported memories if it isn't enough. Say what the work is for, where it stopped, which judgments were settled and what is still open, then give two or three concrete directions, pointing out which evidence came from OpenViking and how it shaped the suggestion. If there are several pieces of work, let them pick one first. |
| To keep or reuse a document | Add the file they named and read it back, or read the stored document to answer a real question of theirs; tell them where it is and how to find it next time. |
| To start fresh, or not now | Say how things will build up from here, and invite them to start on a real task or come back later. |

   After the summary and suggestions, wait for them to choose a direction; don't start changing code or pushing the task on your own. While memories are still being processed, you may read the imported conversations, but say you read the original record rather than presenting it as automatic recall.

4. **Close with at most three lines**:
   - what works now and what is still running in the background (for example memory extraction);
   - where they can see OpenViking at work on this host ("Where to see it" in Appendix A);
   - one or two things they can say later, chosen for them: "import the sessions of <project> into OpenViking", "save this document and continue from it later", "switch to another OpenViking account" (handled as the "Switch" case in [Where to start](#where-to-start)). If other agents got the plugin, mention that connecting them to the same account lets them pick up this work.

## Appendix A: Hosts

| Host | `--harness` | Restart or trust | After restart | Where to see it | History import | User-level rules file |
|---|---|---|---|---|---|---|
| Claude Code | `claude` | Quit and run `claude` again; don't use `/reload-plugins` | New session. Don't use `claude --continue` / `--resume` to go back to the install conversation: the plugin would save the whole install conversation, key included, to OpenViking | A source card under answers that used OpenViking (Claude Code 2.1.286 and later); `/openviking-memory:ov` for status | Supported | `~/.claude/CLAUDE.md` |
| Codex | `codex` | Quit, then run `codex resume --last` to come back to this conversation; when "Hooks need review" appears, choose **Trust all and continue** (later also in `/hooks`). Tell the user: once they choose Trust all, this install conversation is saved to their own OpenViking account, including the API key from the install command and the session previews listed for the import, whether they resume or start a new session; if that bothers them, they can replace the key in the console afterwards | Resume the original session (the command on the left) and send "restarted" | An OV-Usage line after each answer, for example `OpenViking · 3 sources · 1 past event · 2 team docs · 1 read` | Supported | `~/.codex/AGENTS.md`; if `AGENTS.override.md` exists in the same folder, Codex reads that instead |
| TraeCode CLI 2.0 | `trae-cli` | Quit and run `trae-cli` (or `traecli`) again; when "Hooks need review" appears, choose **Trust all and continue** | New session | An OV-Usage line, as in Codex | Convert to JSONL; sessions are in `~/.trae/cli/sessions/`, the same rollout format as Codex | Not confirmed |
| Cursor | `cursor` | Quit Cursor completely and reopen it | New Agent session (**+**); don't continue in the restored install conversation | The OpenViking hook under Settings → Hooks; `openviking` connected under Settings → Tools & MCPs | Convert to JSONL; session location not confirmed | User Rules in Cursor settings; ask the user to paste |
| TRAE / TRAE CN | `trae` / `trae-cn` | Quit the app completely and reopen it; no hook approval needed | New session | `openviking` connected in the MCP settings | Convert to JSONL; session location not confirmed | Personal rules in the app settings; ask the user to paste |
| ZCode | `zcode` | Restart ZCode | New session | Not documented | Convert to JSONL; session location not confirmed | Personal rules in the app settings; ask the user to paste |
| Kimi Code | `kimicode` | Quit Kimi Code; don't use `/reload` | New session; don't resume the current one | Not documented | Convert to JSONL; sessions are in `~/.kimi-code/sessions/` (under `$KIMI_CODE_HOME` if set) | `~/.kimi-code/AGENTS.md` (same) |
| OpenCode | `opencode` | Restart OpenCode | New session | Not documented | Convert to JSONL; sessions are in the SQLite database `~/.local/share/opencode/opencode.db` | `~/.config/opencode/AGENTS.md` |
| pi | `pi` | Restart pi | New session | `OV ✓` in the footer; `/viking` for status | Convert to JSONL; sessions are in `~/.pi/agent/sessions/*/*.jsonl` | `~/.pi/agent/AGENTS.md` |
| DeepSeek Harness | `dsh` | Start a new session with the profile from the installer's `DeepSeek Harness profile:` line: `dsh --profile <profile>` | New session | "Context injection · openviking-memory" at the top of the session | Convert to JSONL; sessions are in `~/.dsh/sessions/` | Not confirmed |

- **Codex approvals.** Codex asks before running commands that need the network. For the install command, which carries the key, choose **Yes, proceed**, not "don't ask again for commands that start with …": that rule would store the key. The first time the OpenViking `search` tool runs, Codex asks for permission; choose **Always allow**. Trusting hooks and approving commands are separate things.
- **Not documented.** On these hosts, the user can ask you directly what OpenViking context you received, or run the diagnostics in Troubleshooting.

Details for each host: https://docs.openviking.ai/en/agent-integrations/01-overview
