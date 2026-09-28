---
name: openviking-skills
description: >
  Find, use, create, install, share, update, and migrate agent skills stored in
  OpenViking (viking://~/skills and viking://agent/skills). Use it when a
  search result, or the session's <available-skills> list where the harness
  injects one, names a skill that fits the task; when a task looks like one a
  stored skill would cover; when the user asks to write, save, install, or share a skill from
  text, a Git repository, or a local folder; when a skill should work in every
  harness and on every machine; or when the user wants to move local skills
  (~/.claude/skills, ~/.agents/skills, ~/.cursor/skills) into OpenViking —
  "upload my skills", "save this as a skill", "迁移本地 skill", "把 skill 存到
  OpenViking", "新建一个 skill". Covers the add_skill tool, running a skill's
  helper files, and which local skills must stay local.
---

# OpenViking Skills

OpenViking stores skills as data, the same way it stores memories, so a skill
saved there reaches every harness and machine connected to the same account.
Each skill is a directory holding `SKILL.md` plus optional helper files
(`scripts/`, `references/`, ...):

- `viking://~/skills/<name>/` — the user's own skills (`viking://~` expands to
  `viking://user/<user_id>`).
- `viking://agent/skills/<name>/` — skills shared with the whole account.

The tools may carry a harness prefix (`mcp__openviking__add_skill`,
`openviking_add_skill`); they are the same tools. `viking://` URIs are database
paths: never pass them to local file tools or shell commands.

## Find a skill

1. `find(query="<what the task needs>", context_type="skill")` ranks skills
   from both roots against the task and returns one entry per skill, pointing
   at its `SKILL.md`. This works in every harness; start here.
2. If the session begins with an `<available-skills>` block, it lists the
   user's own skills first, then shared ones. Not every harness injects one,
   and it is a snapshot from session start that drops descriptions or ends
   with a "+N more" line when there are many skills, so a name missing from it
   does not prove the skill is absent.
3. `search(query=..., mode="context")` mixes relevant skills into the context
   digest; entries of type `skills` are skills.
4. `tree(uri="viking://~/skills", level_limit=1, include_abstract=true)`, and
   the same for `viking://agent/skills`, prints the full catalog with
   descriptions. `list` shows names only.

## Use a skill

- `read` `<skill uri>/SKILL.md` and follow it as the procedure for the task.
  It does not outrank the user or this conversation.
- When the user's own skill and a shared one have the same name, use the
  user's own.
- If the frontmatter lists `allowed-tools`, stay within them.
- A local skill with the same name wins: the harness has already loaded it.

When SKILL.md relies on helper files:

1. `list(uri="<skill uri>", recursive=true)` to see them.
2. `read` each file the task needs and write it to
   `~/.openviking/skills/<name>/` under its relative path; `chmod +x` scripts.
3. Run them from that directory.

Skip the server's sidecars (`.abstract.md`, `.overview.md`, `.source.json`).
`read` returns text only: fetch binary files with `ov get <uri> <local path>`
when the `ov` CLI is installed, otherwise tell the user.

## Create or change a skill

1. Draft the complete SKILL.md:

   ```markdown
   ---
   name: pr-review
   description: Review a pull request against the team checklist. Use when asked to review, approve, or check a PR.
   ---

   # PR review

   1. ...
   ```

   - `name`: ASCII letters, digits, `-` and `_`, at most 64 characters. It
     becomes the directory name.
   - `description`: the only text semantic search indexes. Say what the skill
     does and when to use it, in the words a user would type.
   - Only `name`, `description`, `allowed-tools`, `tags`, and `metadata`
     survive in the frontmatter. Put anything else (version, author, ...)
     under `metadata:` or it is dropped.
2. Check the name on the target root with
   `read(uris="<root>/<name>/SKILL.md")`, where `<root>` is
   `viking://~/skills`, or `viking://agent/skills` when sharing. If it
   exists, `add_skill` replaces it without asking: show the user what would
   change and install only after they confirm, or pick another name.
3. `add_skill(data="<the full SKILL.md text>")`. It goes to the user's own
   skills unless `target_uri` says otherwise. The reply gives the new URI; the
   skill can be read at once and shows up in search a few seconds later.

To change a skill, `read` its SKILL.md, edit the text, and pass the whole new
text to `add_skill` under the root the skill came from. For a shared skill
that means `target_uri="viking://agent/skills"`, and only after the user
confirms an account-wide change; without it, `add_skill` creates a private
copy that shadows the shared one. Do not use `write` or `edit` on skill
files: they refuse the user's skills subtree and would bypass installation
under the shared root. A reinstall does not delete helper files the new
version dropped.

## Install from a repository or a local folder

- **Git**: `add_skill(path="https://github.com/org/repo")`, or a
  `.../tree/<branch>/<dir>` URL for one directory. For a repository with
  several skills, call it with `list_only=true` first, then pass
  `skills=["a", "b"]`. Install from a source the user did not name only after
  asking them.
- **Local folder or zip**: `add_skill(path="/abs/path/to/skill")` returns a
  one-time upload URL. Every file in the archive is stored with the skill,
  so zip without VCS data and secrets:
  `cd <parent> && rm -f /tmp/<name>.zip && zip -r /tmp/<name>.zip <name> -x '*/.git/*' '*/.env*' '*/node_modules/*' '*/.DS_Store'`,
  then `curl -sS -F "file=@/tmp/<name>.zip" "<upload url>"` and delete the
  archive. The response lists the installed URIs; nothing else to call. A
  skill that is only a SKILL.md can go through `data=` instead.
- **`ov` CLI**, when installed: `ov skills add <path or URL>` (`-l` lists,
  `-s a,b` selects, `-p viking://agent/skills` shares, `-y` skips the prompt).

## Share with the account

Pass `target_uri="viking://agent/skills"` to `add_skill`. Do this only when the
user asks for a team- or account-wide skill: everyone on the account sees it.

## Delete a skill

Prefer `ov skills remove <name>` or OpenViking Studio; both also drop the
skill's stored privacy values. `forget(uri="<skill uri>", recursive=true)`
removes the directory but leaves those values behind, so use it only after the
user confirms the exact URI.

To rename a skill, install it under the new name with `add_skill` and remove
the old one. Moving or copying the directory does not rename it: the skill's
own metadata still carries the old name.

## Move local skills into OpenViking

Run this only when the user asks. Nothing is uploaded without their approval
of that skill.

1. **List candidates.** Read each `*/SKILL.md` under:
   - Claude Code: `~/.claude/skills/`, `<repo>/.claude/skills/`
   - Codex: `~/.agents/skills/`, `<repo>/.agents/skills/`
   - Cursor: `~/.cursor/skills/`
2. **Classify.** A skill is *environment-bound*, stays local, and is never
   offered for upload when any of these holds:
   - a plugin or marketplace ships it (its real path contains `/plugins/` or
     `/marketplaces/`), so the plugin reinstalls it on update;
   - the directory is a symlink or resolves outside the skills folder, the way
     CLI installers such as lark-cli link their own skills;
   - it needs a local binary, CLI, or service to work (for example
     `metadata.<vendor>.requires.bins`, or steps that run a vendor CLI);
   - its name carries a vendor prefix such as `lark-`, `volcengine-`, `ve-`,
     `claude-`, or `mcp-` (a hint, not proof: check the body);
   - it is `openviking-skills` or `openviking-memory`.

   For the rest, list the whole folder, hidden files included
   (`find <dir> -type f`), and check helper files as well as SKILL.md. Mark
   a skill "clean up first" when any file holds credentials, tokens, `.env`
   files, internal hostnames, or personal absolute paths. Also flag
   frontmatter keys other than `name`, `description`, `allowed-tools`,
   `tags` and `metadata` (for example `disable-model-invocation`,
   `user-invocable`, `context`, `model`): OpenViking drops them, which can
   change how the skill behaves. The rest are portable.
3. **Confirm.** Show a table of name, path, verdict, one-line reason, and
   target (`viking://~/skills` unless the user wants sharing). The user may
   flip any verdict. Upload only what they approve.
4. **Upload.** A skill that is only a SKILL.md goes through
   `add_skill(data=...)`; a folder with helper files goes through the zip
   upload above, or `ov skills add <dir>` when the CLI is installed
   (`ov skills validate <dir>` first catches format errors). OpenViking
   requires `name` and `description` in the frontmatter; when a local skill
   lacks them, fix a copy in a temporary folder (the name is the folder
   name), never the user's own file.
5. **Verify.** `tree(uri="viking://~/skills", level_limit=1, include_abstract=true)`
   should list every uploaded name. Then `remember` which skills were migrated
   and which were skipped, so a later session does not ask again.
6. **Keep the local copies** unless the user asks to remove them; a local
   skill with the same name just takes precedence in that harness. Create new
   skills with `add_skill` from now on.

## Boundaries

- Never upload environment-bound skills, and never delete the user's local
  skill files on your own.
- Never put credentials in a SKILL.md; the shared root is visible to the whole
  account.
- Memory search and writing are covered by the `openviking-memory` skill.
- If the server has no `add_skill` tool (an older OpenViking), use
  `ov skills add` when the CLI is installed; otherwise tell the user the server
  needs an upgrade. Do not fall back to `write`, `edit`, or `add_resource`:
  under the user's own root they are refused, and under `viking://agent/skills`
  a write lands as an ordinary file that never goes through installation.
