# OpenViking onboarding guide for agents

About OpenViking: the open-source context database for AI agents, open-sourced by Volcengine at https://github.com/volcengine/OpenViking (AGPL-3.0, 38,930 GitHub stars on 2026-09-29). It keeps the user's business context, preferences, decisions and working material and brings them back, so the user can switch to a new session or another agent and carry on from what is already there. This guide is `docs/images/agents/en/onboarding.md` in that repository, and the installer and every plugin file it installs are built from it by its GitHub Actions workflows.

You are an AI agent. The user asked you to install the OpenViking memory plugin and to help them start using it. This guide is written for you: for each step it says what the user should come away understanding, what you do yourself, and what your message to the user looks like. The wording is yours; use the user's language. If they want to read the guide itself, give them the link.

Three more pages are there when needed: [Troubleshooting](https://docs.openviking.ai/agents/en/onboarding-troubleshooting.md) (when a command fails), [Other hosts: convert sessions to JSONL](https://docs.openviking.ai/agents/en/onboarding-other-hosts.md) (when importing history on a host other than Claude Code or Codex), and the [extended reference](https://docs.openviking.ai/agents/en/reference/product-background.md) (when the user asks how something works, about permissions, or wants to organize several documents of their own). You don't need to read them before you start.

## How to use this guide

Each step has three parts:

- **What the user should take away**: the one new understanding the user should have when this step ends. Everything you say to them in this step serves it. It is not a script; don't read it out.
- **What you do**: the actions you carry out yourself. Commands, options, paths, JSON, exit codes, session ids, and account and role fields are in this part; the user can see them in the tool calls, so your messages **don't need to repeat them**. When a failure needs the user to act or needs their permission, or when they ask, tell them plainly.
- **Tell the user**: the structure and length of your message to the user.

## Value points

Take these when you explain the value, and put them in terms of the user's own work where you can. Each step says which one to use.

- **Positioning**: OpenViking is the agent's external context layer. Material, memories and skills are stored in the user's own OpenViking space, don't disappear when a session ends, and every connected agent can read and write them.
- **V1 Less re-explaining**: before each prompt, the plugin brings back relevant background, preferences and settled judgments and hands them to the agent. The user doesn't have to explain everything again in every new session.
  - Supporting evidence (use it at most once in the whole onboarding, and say where it comes from): in the official LoCoMo long-conversation evaluation, Claude Code with OpenViking went from 57% to 80% accuracy on cross-session history questions, with 63% fewer input tokens (OpenViking official report, 2026-05-29).
- **V2 Past work is on record**: when they ask "where did we leave off" or "why did we decide that", the answer can be found in the saved discussions instead of in the user's memory.
- **V3 Switch agents, keep the context**: Codex, Claude Code and other agents connected to the same account use the same context. When one agent runs out of quota or they want a different tool, the work carries on.
- **V4 Finished work comes back as it was**: automatic memory keeps the points distilled from conversations; finished work such as designs, decisions with their reasons, and deliverables has to be saved by the agent on purpose, so the original can be read next time.

## How to write messages to the user

- **First what this means for them, then the result, then at most one question.** The first sentence is about their work or what they get, not about which checks you ran.
- **Describe the value as what will happen from now on**: "From now on, when you ask … in Codex, it will …". This explains what the product does once it is set up and doesn't need verifying first; say only what the [value points](#value-points) say, don't overstate it, and don't present something that hasn't happened yet as already done.
- **Mention a status only when it changes what the user does next.** For example, "it takes effect after a restart" needs saying, because they have to restart; notes like "memory extraction isn't confirmed yet" wait for the close and are said once there, not added to the end of every message.
- **Use their own content as examples.** If you can say "the demo storyline you settled on in the Gateway project", don't say "your earlier decisions".
- **Ask one decision at a time.** The import scope, the go-ahead to import and the collaboration rule each get their own turn; wait for the answer before moving on.
- **Ask all three decisions with the choice tool.** For the import scope (3a), the go-ahead to import (3b) and the collaboration rule (Step 4), whenever the host has a tool that lets the user pick an answer (see "Choice tool" under [Appendix A](#appendix-a-hosts)), always ask with it; don't write the options as a text list. The places marked "**Question (choice tool)**" in the steps are where to call it. Put one question and two or three options in the tool; keep option labels short, put extra explanation in the option descriptions, and put the recommended one first, marked "(recommended)". The tool has its own free-text input, so don't add an "Other" option. Only if there is no such tool in your tool list, or the call fails, list the same options in text, and don't retry.
- **Put what they need to see where they are sure to see it.** The question text must make sense on its own: put the points needed for the decision (what the dry run found, the rule summary and the file path) into the question, not just "as above". Some hosts (the Codex desktop app, for example) collapse the progress messages around a tool call once the turn ends, leaving only the last reply and the question card. So the read-back, the rule summary and anything else they need to read go in the body of the last reply of the turn; the question on the choice card carries only a one- or two-sentence summary and the decision, never the whole read-back. With a tool that doesn't wait for the answer, such as `request_user_input_async`, call the tool first, then write the last reply.
- Don't mention the step numbers of this guide or steps that don't apply to the current host, and don't relay the guide point by point.

## Ground rules

- **Never print or repeat the API key.** The installer stores it in `~/.openviking/ovcli.conf` on this machine (backing up the old file when it changes it); the connection needs that. Apart from that, the key must not appear in rules, project files, the continuation message or a permanent command approval. Don't open, print or search `ovcli.conf`. When you need the server and identity, run `ov health` or call the OpenViking `health` tool.
- **Make only the changes this guide describes.** The installer installs into every supported agent it detects on this machine, which is expected. Don't edit other agents' configs or files in the user's projects yourself unless the user asks.
- **Use the commands in this guide.** Don't go to GitHub and read the source to work out another way to install or configure; when unsure of an option, check the command's `--help`.
- **Stick to the facts.** What you read back must come from what OpenViking actually returned. Without the user's real content, don't make up example memories to demonstrate the effect.
- If a command fails, look up the symptom in [Troubleshooting](https://docs.openviking.ai/agents/en/onboarding-troubleshooting.md), fix it and retry once. If it still fails, tell the user which step didn't work, what it affects, and what they can do. Only a connection or auth failure on the current host blocks the steps after it; a failed install for another host doesn't hold up the current one.

## Where to start

- **You ran the installer in this conversation**: start at Step 1.
- **The user says "restarted", and this conversation already went through the earlier steps of this guide**: the user quit and resumed the original session. Go straight to [After the restart](#after-the-restart); the earlier choices are in this conversation, so don't ask them again.
- **In a new session, the user sends a "continue from 'After the restart'" message**: go to [After the restart](#after-the-restart) and take the results the message states.
- **The user only says "restarted", but this conversation has no earlier onboarding steps** (for example `codex resume --last` opened another session, or they started a new one): also go to [After the restart](#after-the-restart), and in its item 2 first ask how far the import and the collaboration rule got, then do what is missing one at a time.
- **OpenViking was installed before this conversation started** (you see an `<openviking-context>` block, or you have OpenViking tools such as `health`): check the connection with `ov health` or the `health` tool and note the user name it returns, tell the user which server and which user they are connected as, and ask whether to keep it or switch.
  - Keep it: do Steps 2 to 4, then go straight to [After the restart](#after-the-restart). No restart is needed.
  - Switch: the plugin is saving this conversation, so don't have the user send a new key here. Ask them to run the install command from the console (or the one below) in their own terminal, then send the console's one-click prompt to an agent in a new session. The old config is backed up and nothing on the server is cleared.
- **There is no connection yet and the user gave no install command**: ask for the server URL and API key (OpenViking console, User Management → API Key), then run the command below with `<value>` from the `--harness` column of [Appendix A](#appendix-a-hosts), and start at Step 1. Keep `cli` in the list: checking the connection, reading content back and adding files all go through the `ov` command.

  ```bash
  curl -fsSL https://openviking.ai/install | bash -s -- --yes --lang en --harness <value>,cli --url <server URL> --api-key <the user's key>
  ```

- **The user only wants to import more history**: do Step 3 only, without the install and the collaboration rule.

## Step 1: Connection result

**What the user should take away**: "My OpenViking is connected; from now on these agents all use the same context." (V3)

**What you do**

Read the installer's output. It may be in English or in Chinese; recognize both.

- If the output ends with a `Not done` list (Chinese `未完成`), judge per host; don't say everything is installed because `Done` appears above it. If the current host failed, the key was rejected or the server can't be reached, handle it with Troubleshooting first; if only other hosts failed, mention it briefly in your message and carry on with the current host.
- The `Done` section (Chinese `完成`) lists the result per tool, each with a `Next:` line (Chinese `下一步：`) saying how to restart it. Check that the current host is listed; if it isn't, see "The current host is not under `Done`" in Troubleshooting.
- Then run `ov health` once: it confirms the connection, and **note the user name it returns**. Writing the collaboration rule and adding files later both use this user name, so you don't need to look it up again. If the installer output already has `Server check passed: <url>` (Chinese `服务检查通过：<url>`), this step is mainly for getting the user name.

**Tell the user**

No separate message. Open the Step 2 message with one sentence: you are connected to their OpenViking, and the plugin went into these agents (by product name, for example Codex, Claude Code). If it went into more than one, add the benefit: from now on they can carry on with the same context in any of these agents.

Don't mention the account name, role, server URL or command names. Tell them which user they are connected as only if they have several OpenViking accounts or ask.

## Step 2: What happens automatically from now on

**What the user should take away**: "I don't have to explain the background every time; what we talk about builds up on its own." (V1)

**What you do**

- Look up three things for the current host in [Appendix A](#appendix-a-hosts): whether the hooks need trusting (and whether that can be done before the restart), where the user can see OpenViking at work, and whether they resume the original session or start a new one after the restart.
- Don't ask whether to turn on automatic memory; the install was the user's request.
- If the user asks how to turn it off in one project, tell them then: put `.openviking/config.json` in that project with `{"version": 1, "capture": {"enabled": false}, "recall": {"enabled": false}}`. Tell them how; don't create it for them.

**Tell the user**

After the Step 1 sentence, in two or three lines:

1. V1: from now on, relevant background is brought to the agent before each prompt, and each turn is saved to their OpenViking automatically and builds up into background over time. Give one example from their current project or request.
2. Where they can see it at work, described the way the user will see it according to Appendix A, for example "an OV-Usage line will appear under answers".
3. If it takes effect only after a restart, say "it starts working after a restart; I'll tell you how at the end". On hosts that need the hooks trusted, add "at the end I'll also ask you to confirm OpenViking's hooks once". The details wait for Step 5.

End this message with the one-sentence value from 3a, then send the 3a question with the choice tool. This message doesn't cover hook event names, the order of saving and extraction, or how to turn it off.

## Step 3: Bring in earlier work

**What the user should take away**: "Past discussions can become background on record. I've seen it for myself this time: it found my work in there." (V2)

This step takes at most three turns: choose the scope → confirm the import → read back the result. The read-back is the point of this step; the import is only the means.

### 3a Choose the scope

**Tell the user**: one sentence of value, ideally in terms of their project: "Bring in your earlier discussions, and next time you ask where <project> left off or why something was decided, there will be something to go on."

**Question (choice tool)**: Which earlier work should I bring in?

- This project, last 14 days (recommended)
- One kind of work or another project, with the description "for example, 'the discussions about the payment refactor'"
- Nothing for now; build up from here

They type any other scope (another time range, all projects) in the tool's free-text input.

**What you do**: Don't download the script or do a dry run before they choose. The import script reads Claude Code and Codex history directly (`--harness claude` or `--harness codex`). On other hosts: if `~/.claude/projects` or `~/.codex/sessions` exists on this machine, you can offer that history as an option as well; the host's own sessions first need converting, see [Other hosts: convert sessions to JSONL](https://docs.openviking.ai/agents/en/onboarding-other-hosts.md).

### 3b Dry run

**What you do**

Option 1 or their own scope: download the script and do a dry run (nothing is uploaded). Replace `<claude|codex>` with the actual value; for their own scope, set `--cwd` to that project's directory, use `--all-projects` instead of `--cwd` for all projects, and change `--since` (for example `30d`, `2026-09-01`, `all`).

```bash
curl -fsSL https://docs.openviking.ai/dl/memory-plugin-shared/import-history.mjs -o "${TMPDIR:-/tmp}/ov-import-history.mjs" && node "${TMPDIR:-/tmp}/ov-import-history.mjs" --harness <claude|codex> --cwd "$PWD" --since 14d --max-sessions 20 --dry-run --json
```

Option 2: first list the sessions in scope with `--list` (each with its id, time, message count, state, and a preview of its first and last request). Set the scope from what the user described; if they didn't say, use this project and the last 30 days. Pick the sessions whose previews match what they described, then dry-run the picked ids (comma-separated; with `--session`, project, time and count limits don't apply):

```bash
curl -fsSL https://docs.openviking.ai/dl/memory-plugin-shared/import-history.mjs -o "${TMPDIR:-/tmp}/ov-import-history.mjs" && node "${TMPDIR:-/tmp}/ov-import-history.mjs" --harness <claude|codex> --cwd "$PWD" --since 30d --max-sessions 50 --list --json
node "${TMPDIR:-/tmp}/ov-import-history.mjs" --harness <claude|codex> --session <id>,<id> --dry-run --json
```

Read the dry-run JSON: `toImport.sessions` is the number of sessions to upload; sessions whose `sessions[].state` is `imported` (imported before) or `live` (already saved by the plugin) are already in OpenViking. The sessions skipped as "still active" include this conversation.

- **`toImport.sessions` is 0, but there are `imported` or `live` sessions**: this content is already in OpenViking, so there is nothing to upload and no need to ask them to confirm. Go straight to the 3c read-back.
- **No sessions at all in scope**: tell them nothing was found in this scope, and ask whether to try another scope or skip importing for now.
- **There are sessions to upload**: ask them to confirm as below.

**Tell the user** (when there are sessions to upload): three or four lines.

- What was found: say which work it is, from the previews (for option 2, which ones you picked and roughly what you left out); no ids.
- How big: how many sessions, and roughly how much text.
- What gets sent: only what they and the agent wrote; no tool calls or tool output, no file originals, and known secret formats are replaced.

**Question (choice tool)**: Import this scope? Options: Import (recommended) / Not for now. If they want to change the scope, they say so in the free-text input.

### 3c Import and read back

**What you do**

If there is nothing new, skip the import and go straight to the read-back. Once the user confirms, import with **exactly the same scope options**, replacing `--dry-run` with `--yes --wait` (`--wait` waits up to two minutes for memory extraction):

```bash
node "${TMPDIR:-/tmp}/ov-import-history.mjs" --harness <claude|codex> --cwd "$PWD" --since 14d --max-sessions 20 --yes --wait --json
```

Check each session's `extraction` in the JSON, not just the exit code.

Then **read back**. Whenever any session is in OpenViking (imported this time, imported before, or already saved by the plugin), read it back. When `--wait` returns, the sessions whose `extraction` is `completed` have already been turned into memories, so the read-back reads memories, not the sessions themselves. The plugin loads into this conversation only after the restart, so use the `ov` command here:

1. Search the memories across the user's whole directory, with the work or project name they described as the query. Memories extracted from the import end up in two places: `viking://user/<user_id>/memories/` (entities, events and preferences that span projects) and `viking://user/<user_id>/peers/<project>/memories/` (this project's entities, events and preferences; `<project>` is `projectPeer` in the import JSON). One search scoped to the user directory covers both:

   ```bash
   ov find "<the work or project name they described>" -u viking://user/<user_id> --context-type memory -n 10
   ```

   To see only the memories this import produced, add `--after` with a time from before the import started, for example `--after 30m`.

2. From the results, pick the two or three most relevant, focusing on the entity (`entities/`) and event (`events/`) memories under the two `memories/` folders above, and read them in full with `ov read`. An entity memory says what this work or thing is and where it stands; an event memory has a summary and also carries the conversation as it was said:

   ```bash
   ov read <memory URI>
   ```

3. Read the imported conversation itself only in two cases: they want the original wording, or no memories were extracted (`extraction` isn't `completed`, or item 1 found nothing). Use the session `id` from the import JSON; an import commits once, so the archive is `archive_001`. Sessions the plugin saved (`live`) aren't read this way; go by what item 1 returned:

   ```bash
   ov session get-session-archive <session id> archive_001
   ```

4. From what you read, put together four things: what this work is meant to achieve, where it left off, one judgment that was settled and its reason, and what is still open. Leave out anything you can't find; don't fill it in. Also note one **gap**: finished work that the discussion mentions but whose original isn't in OpenViking, such as a design doc, a script or a report. Step 4 uses it.

| Import result | What the first sentence says | Read back |
|---|---|---|
| Written, `extraction` is `completed` | Which work was imported, and that the memories are ready | Read memories: `ov find`, then `ov read` one or two |
| Written, extraction still in progress (`pending`, `running`) or no result | The discussions are saved; the memories are still being processed in the background and can be recalled automatically in a few minutes | Read the original conversation (`get-session-archive`), and say "this is read from the original conversation" |
| `failed` | The discussions are saved, but processing them into memories failed; it can be retried later | Read the original conversations |
| Nothing new: all imported before or saved by the plugin | These discussions were already in their OpenViking, so there was no need to upload them again | Read memories (without `--after`) |
| Some failed | Which ones made it and which didn't; the failed ones can be retried later without re-sending everything | Read the ones that made it |
| No match, or the user chose not to import | It builds up from their real work from now on | No; go straight to Step 4 |

If the user asks what the script does: it only reads the session files on this machine (Claude Code in `~/.claude/projects/`, Codex in `~/.codex/sessions/`), keeps only the user and assistant text, redacts known secret formats, skips sessions that may still be running, and files sessions under the same project as the plugin does, so imported history and later conversations end up together. The dry run and `--list` only read from the server, to find sessions that were already imported.

**Tell the user**

1. First sentence: "What the first sentence says" in the table above. Don't give message counts, sizes, or state names such as `extraction`.
2. The read-back, two to four items, in their own words and names: what they are working on, where it left off, what was decided (with the reason), what is still missing. If there are several pieces of work, one line each, no detail. The read-back goes in the body of this message; the choice card for the next step mentions it in one sentence at most.
3. One sentence of value (V2 + V3): "From now on, in any connected agent, ask where <work> stands and it picks up from here."

This message doesn't ask whether to start on this work, and doesn't list states. In the same message, go on to the Step 4 bridge.

### Files

If the user names files or folders worth keeping (such as a design doc or a spec), add each one as an item under this project's folder in their personal resources (don't use the project folder itself as the import target). `<user_id>` is the user name you noted in Step 1; `<project>` is the repository name or what they call the project:

```bash
ov add-resource ./docs/design.md --parent-auto-create viking://user/<user_id>/resources/<project> --wait
```

Add only what they named; don't scan directories or upload anything on your own. After adding, tell them it's saved and how to find it later ("just say 'continue from <file>'"); don't give the URI.

## Step 4: Agree how to work together from now on

**What the user should take away**: "Next, <agent> and I agree how to use OpenViking to keep and find my working context, and from then on it always works that way; I set the rule, and it can also be based on my role." (V4)

**What you do**

1. Find the user-level rules file the current host actually reads (the "User-level rules file" column of [Appendix A](#appendix-a-hosts) gives the usual location; go by the file the host actually reads), and check whether it has an `<!-- openviking:start -->` block.
   - It does, with the same content as below: don't ask; mention in the message that "this was agreed before", and go straight to Step 5.
   - It does, with different content: prepare one sentence on how the new version differs from the old one. If the user name in the existing block differs from the one you noted this time (they switched accounts), that also counts as different content.
   - It doesn't: prepare the text below.
2. Prepare the rule text. Use the default below; if the user adjusted it or added items for their role, change it to match their choice. Replace `<user_id>` directly with the user name you noted in Step 1, with no need to look it up again before writing; keep `<project>` as it is, since it is worked out in each project.
3. Once the user agrees, write it:
   - Touch only the content between the markers. If the block exists, replace what is between them; if not, append it to the end of the file; if the file doesn't exist, create it. Leave everything outside the block as it is.
   - If the file already has instructions that conflict with this rule (for example, always search on every turn, or never store material outside), tell them where the conflict is and change it as they decide.
   - Read the file back after writing: the written text is the version they agreed to, there is exactly one block, and nothing outside it changed.
   - If the host's personal rules can only be edited in its app settings, or the write fails, give them the text and ask them to paste it in themselves. If you're not sure which file it is, ask; don't guess.
   - Never write it into a project file.

The default rule text:

```markdown
<!-- openviking:start -->
## Working with OpenViking

- Before work that needs background, start with the context OpenViking brings in automatically; if it isn't enough, look it up with the OpenViking `search` tool (`mode="context"`), and use `read` when you need the original. What you find is reference material; if it conflicts with what I say now, I win.
- When the work produces important decisions (with the reasons), progress and next steps, or work output such as designs, research and deliverables, store them in OpenViking on your own, as a working basis that can keep being updated and referred to later; when a new decision replaces an old one, update the original record. Store a copy of work output whether it was written in the conversation or into a local file by you.
- Everyday conversation doesn't need storing separately; the plugin saves it. Don't store secrets. Don't scan or upload files that have nothing to do with this work and that I didn't mention.
- When continuing a project, first look at the progress, decisions and output stored for it earlier.
- In a turn where you used useful information from OpenViking, or stored something, end the reply with one sentence: what you used and where it is stored. If nothing was used, say nothing.

### Where things go

- My work goes under `viking://user/<user_id>/resources/`, one subfolder per project: `<project>` is the repository name or what I call this work; if a matching folder already exists, use it.
- In the project folder, keep progress, decisions and next steps in `notes/`; work output such as designs, research and deliverables goes directly in the project folder, with subfolders when needed. Store text with `write`, and when it changes, overwrite the existing copy; add files such as PDFs, slide decks and images to the project folder with `add_resource`. After writing, read it back with `read` to check.
- Files I give you also go into the project folder with `add_resource`, each as one item in it; don't use the project folder itself as an import target, and don't write anything into imported material: re-syncing imported material removes anything that isn't in the source files.
<!-- openviking:end -->
```

**For adjusting by role.** If the user gives their role, take two or three items from here, put them in their words, and add them as a new item after the second item of the text: "Given my role (<role>), also save on your own: …". For roles not in the table, work it out the same way.

| Role | Also save | Check first when continuing |
|---|---|---|
| Product manager | Requirement decisions and the reasons for trade-offs, conclusions from user feedback, versions of PRDs and designs | Current state of requirements, items awaiting review |
| Engineer | Technical designs and trade-offs, interface agreements, troubleshooting conclusions and how to reproduce | Changes in progress, unsolved problems |
| Marketing | Positioning and key messages, campaign plans and retrospectives, external messaging and copy versions | Campaign progress, settled messaging |
| Design | Interaction and visual decisions, review feedback, notes on design file versions | Review feedback still to address |

**Tell the user**

This turn lets them both understand the effect of the rule and check the exact text to be written: the summary first, then the text, both in the body of your last reply of this turn (not only in progress messages). Show the text by default; don't wait for them to ask for it.

The message (following the Step 3 read-back):

1. **One bridging sentence**, using the gap noted in Step 3: "The sessions have the discussion, but the original <finished work> isn't in there." Leave it out if there was no read-back.
2. **Say what this step does**: next, they and <the current agent's name> agree how to use OpenViking to keep and find working context; the rule goes into <full path of the rules file>, applies to all their projects, is followed in all their work from now on, and can be changed at any time.
3. **The approach we suggest**, three points, in the user's language, without storage locations, folders or tool names:
   - before starting work, bring back the relevant background from OpenViking;
   - when the work produces important decisions (with the reasons), progress and next steps, or output such as designs, research and deliverables, store them in OpenViking on its own, as a working basis that can keep being updated and referred to later;
   - whenever useful information from OpenViking was used, say so briefly at the end of the reply.
4. **The text to be written**: right after the summary, say "Here is the text that will go into <full path of the rules file>:", then give the full text in a code block (with `<user_id>` replaced by their user name).

**Question (choice tool)**: The question text must make sense on its own, with the gist of the summary and the file path, for example "Work this way from now on? Bring back background before starting work; store important decisions, progress, and output such as designs and research in OpenViking on my own; tell you briefly when I used it. Goes into <rules file path>, for all projects." Options:

- Work this way (recommended)
- Adjust for my role, with the description "Tell me your role, for example marketing, product or engineering, and I'll suggest what to save"
- Automatic memory only

In the question or a description, mention that if they want to change anything in the text, they can say so directly in the free-text input.

Then:

- **Work this way**: write it.
- **Adjust for my role**: ask their role (one sentence in text is enough). Using the reference above, give the adjusted effect in three or four points, marking which ones are new, together with the adjusted text, then ask with the choice tool: Write this (recommended) / Adjust again / Automatic memory only.
- **They said directly what to change**: change the text as they want, show the effect and the changed text, and ask again.
- **Automatic memory only**: write nothing. Tell them they can still ask you to save or read material at any time.

The summary must truthfully match the text to be written: anything in the text that the summary doesn't mention may only be implementation detail of behavior the summary describes (such as where things go).

If they ask "where is it stored", explain then: in their personal OpenViking space, one folder per project; progress and decisions go in the project's notes; designs, research and deliverables (whether you produced them or they uploaded them) go in that project folder too. Wait for their answer before Step 5; don't bring up the restart in the same message as this question.

## Step 5: Restart

**What the user should take away**: "One more step and it's ready to use; when I come back, it picks up right where my work is."

**What you do**

- If it is already active on the current host and nothing was newly installed, skip this step and go straight to [After the restart](#after-the-restart).
- Look up the "Restart or trust" and "After restart" columns for the current host in [Appendix A](#appendix-a-hosts), and the installer's `Next:` lines (Chinese `下一步：`) for the other tools.
- Prepare the continuation message. Use the URL you actually opened successfully; if this guide is a local file, use its absolute path. Fill in the angle brackets with the real results:

```text
Restarted. Continue the OpenViking onboarding from "After the restart" in https://docs.openviking.ai/agents/en/onboarding.md. History import: <for example "2 sessions imported", "already in OpenViking", "skipped">; collaboration rule: <added / not added>; what I want to do next: <the goal they mentioned, or if none, "see where <work> left off">.
```

**Tell the user**

1. Where things stand, stated positively: connected, and which work was brought in. If the collaboration rule was written, list its three summary points again with the rules file path, and tell them that to read or change it later they can open that file or ask you; if it wasn't written, say so.
2. **On hosts that need the hooks trusted (Codex, TraeCode CLI 2.0), ask them to trust the hooks before quitting**: in the Codex desktop app, click the hooks icon below the input box; in Codex CLI and TraeCode CLI, type `/hooks` in the session; trust all of OpenViking's hooks. Give the benefit in one sentence: after the restart it won't stop at a review prompt, and they can carry on with their work right away. Appendix A says what to tell the user after trusting (Codex saves this install conversation, key included); cover that in one sentence too. If "Hooks need review" still appears after the restart, choose **Trust all and continue** as before.
3. How to restart the current host.
4. If the installer also set up other agents, add one line naming them: each takes effect after its own restart; see the "Next" lines in the installer output.
5. What to do when they come back:
   - Hosts whose "After restart" column says to resume the original session: tell them to come back to **this conversation** with the command in the table and send "restarted". Give them the continuation message as well, as a fallback: if they land in a different conversation, they send it in a new session.
   - Hosts that say to start a new session: tell them to start a new session, not to resume this conversation or reload the plugin in it, and give them the continuation message.
6. One line on what comes next: "When you're back, I'll first tell you where <work> left off, then give you a few directions to take it." If no work was brought in: "When you're back, just tell me what you want to do."

Then stop and wait for the user.

## After the restart

**What the user should take away**: "It really did pick up: I didn't explain any background, and it knows what I'm working on." (V1, V2)

**What you do**

1. **Check the plugin is running.** There is an `<openviking-context>` block in this conversation, or OpenViking tools are available. Call the `health` tool (its name may carry a prefix, such as `mcp__openviking__health` or `openviking_health`). If neither is there, see "No recall after the restart" in Troubleshooting. Only an injected block shows that automatic recall is running; no relevant memories may simply mean nothing relevant is stored yet.
2. **Fill in missing choices.** From this conversation or the continuation message, check whether the import and the collaboration rule were done. Do whichever step is missing; don't re-ask what was settled.
3. **Bring back the background.** Use the injected context first; if it isn't enough, use `search` (`mode="context"`), and `read` the original when needed. While memories are still being processed, you may read the imported original conversations, but say that you read the original record.

**Tell the user**

Pick one by the outcome they want:

| They want | What the message contains |
|---|---|
| To continue earlier work | What the work is meant to achieve, where it left off, which judgments were settled and what is still open; then two or three concrete directions to take it. Point out one piece of evidence that came from OpenViking and how it shaped your suggestion (V1). If there are several pieces of work, let them pick one first. |
| To keep or reuse a document | Add the file they named and read it back, or read stored material to answer a real question of theirs; tell them how to find it next time. |
| To start fresh, or not now | One sentence on how things will build up from here, and invite them to start on a real task or come back later. |

After the summary and directions, wait for them to choose; don't start changing code or pushing the task on your own.

**Close** with at most three lines, after they choose a direction or at the end of this message:

- What already works, and what is still running in the background (for example memory processing). Any status not yet confirmed is mentioned here, once.
- Where they can see OpenViking at work on this host ("Where to see it" in Appendix A).
- One or two things they can say later, chosen for them: "import the sessions of <project> into OpenViking", "save this document and continue from it later", "switch to another OpenViking account" (handled as the "Switch" case in [Where to start](#where-to-start)). If other agents got the plugin, mention that they can carry on this work there and the background will still be there (V3).

## Appendix A: Hosts

| Host | `--harness` | Restart or trust | After restart | Where to see it | History import | User-level rules file |
|---|---|---|---|---|---|---|
| Claude Code | `claude` | Quit and run `claude` again; don't use `/reload-plugins` | New session. Don't use `claude --continue` / `--resume` to go back to the install conversation: the plugin would save the whole install conversation, key included, to OpenViking | A source card under answers that used OpenViking (Claude Code 2.1.286 and later); `/openviking-memory:ov` for status | Supported | `~/.claude/CLAUDE.md` |
| Codex | `codex` | **Trust the hooks before quitting**: in the desktop app, click the hooks icon below the input box; in the CLI, type `/hooks` in the session; trust all of OpenViking's hooks. The trust is recorded in `~/.codex/config.toml`, so there is no prompt after the restart. Then quit: in the CLI, run `codex resume --last` to come back to this conversation; in the desktop app, quit, reopen it and go back to this conversation. If "Hooks need review" still appears after the restart, choose **Trust all and continue**. Tell the user: once the hooks are trusted, this install conversation is saved to their own OpenViking account, including the API key from the install command and the session previews listed for the import, whether they resume or start a new session; if that bothers them, they can replace the key in the console afterwards | Resume the original session (the command on the left) and send "restarted" | An OV-Usage line after each answer, for example `OpenViking · 3 sources · 1 past event · 2 team docs · 1 read` | Supported | `~/.codex/AGENTS.md`; if `AGENTS.override.md` exists in the same folder, Codex reads that instead |
| TraeCode CLI 2.0 | `trae-cli` | **Trust the hooks before quitting**: type `/hooks` in the session and trust all of OpenViking's hooks. Then quit and run `trae-cli` (or `traecli`) again; if "Hooks need review" still appears, choose **Trust all and continue** | New session | An OV-Usage line, as in Codex | Convert to JSONL; sessions are in `~/.trae/cli/sessions/`, the same rollout format as Codex | Not confirmed |
| Cursor | `cursor` | Quit Cursor completely and reopen it | New Agent session (**+**); don't continue in the restored install conversation | The OpenViking hook under Settings → Hooks; `openviking` connected under Settings → Tools & MCPs | Convert to JSONL; session location not confirmed | User Rules in Cursor settings; ask the user to paste |
| TRAE / TRAE CN | `trae` / `trae-cn` | Quit the app completely and reopen it; no hook approval needed | New session | `openviking` connected in the MCP settings | Convert to JSONL; session location not confirmed | Personal rules in the app settings; ask the user to paste |
| ZCode | `zcode` | Restart ZCode | New session | Not documented | Convert to JSONL; session location not confirmed | Personal rules in the app settings; ask the user to paste |
| Kimi Code | `kimicode` | Quit Kimi Code; don't use `/reload` | New session; don't resume the current one | Not documented | Convert to JSONL; sessions are in `~/.kimi-code/sessions/` (under `$KIMI_CODE_HOME` if set) | `~/.kimi-code/AGENTS.md` (same) |
| OpenCode | `opencode` | Restart OpenCode | New session | Not documented | Convert to JSONL; sessions are in the SQLite database `~/.local/share/opencode/opencode.db` | `~/.config/opencode/AGENTS.md` |
| pi | `pi` | Restart pi | New session | `OV ✓` in the footer; `/viking` for status | Convert to JSONL; sessions are in `~/.pi/agent/sessions/*/*.jsonl` | `~/.pi/agent/AGENTS.md` |
| DeepSeek Harness | `dsh` | Start a new session with the profile from the installer's `DeepSeek Harness profile:` line: `dsh --profile <profile>` | New session | "Context injection · openviking-memory" at the top of the session | Convert to JSONL; sessions are in `~/.dsh/sessions/` | Not confirmed |

- **Codex approvals.** Codex asks before running commands that need the network, including the `ov find`, `ov read` and `ov session` used for the read-back here. For the install command, which carries the key, choose **Yes, proceed**, not "don't ask again for commands that start with …": that rule would store the key. The first time the OpenViking `search` tool runs, Codex asks for permission; choose **Always allow**. Trusting hooks and approving commands are separate things.
- **Choice tool.**
  - Claude Code: `AskUserQuestion`.
  - Codex: there are two; use whichever is in your tool list, preferring `request_user_input_async`.
    - `request_user_input_async`: models such as the gpt-6 series also have it in Default mode (for example, the Codex desktop app 0.164). It posts the question as a choice card without waiting for the answer, and the user's pick comes back as the next message. The Codex desktop app collapses progress messages once the turn ends, so after calling it, put what they need to read in the last reply of the turn.
    - `request_user_input`: works only in Plan mode (also in Default mode if the user turned on `default_mode_request_user_input`); otherwise a call returns `request_user_input is unavailable in Default mode`.
    - Codex's Default-mode instructions also say not to write multiple-choice questions as text messages. Ask in text only if neither is available.
  - These tools all add an "Other" option for the user's own input automatically, so don't put an "Other" option among yours. On other hosts, use a similar tool if there is one; otherwise ask in text.
- **Not documented.** On these hosts, the user can ask you directly what OpenViking context you received, or run the diagnostics in Troubleshooting.

Details for each host: https://docs.openviking.ai/en/agent-integrations/01-overview
