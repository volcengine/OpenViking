# OpenViking extended reference

The [onboarding guide](https://docs.openviking.ai/agents/en/onboarding.md) (or the team edition, [onboarding-team.md](https://docs.openviking.ai/agents/en/onboarding-team.md)) already explains what a normal setup needs. This page is for answering the user's follow-up questions or handling goals beyond the setup; you don't need to read it through before you start, only the part relevant to the question at hand. Knowing something here doesn't mean the current tools support it, and it doesn't authorize extra tasks.

## Kinds of content and processing states

- Sessions keep the interaction within what is supported; a stored session doesn't prove it holds the latest messages.
- Memories are extracted and updated by the service from committed sessions according to its policies, such as preferences, events and experience. Memories can be missing or out of date; what the user asks for now takes precedence.
- Resources keep readable content such as requirements, designs and reports; mentioning a file in a chat doesn't upload the original, and importing the body doesn't prove every attachment was backed up.
- Skills provide reusable procedures; saving experience doesn't create or install a skill.

A message written, a commit submitted, extraction finished, a search hit and actual use are different states. To confirm a state, check the matching result; don't infer finished extraction from a file existing, or use of an answer from a source count.

## Hooks, rules and cost

Hooks are how the host runs the plugin on session events: loading background, recall, capture or triggering a commit. The exact events, trust requirements, what is sent and when commits happen depend on the installed version. If a client doesn't use hooks, describe its actual plugin mechanism, and don't send the user looking for an approval screen that doesn't exist.

The lasting rule tells the agent when to look up the originals, what to save on purpose and how to report it; it doesn't create a background file watcher and doesn't guarantee correct behavior every turn. When the automatic injection is enough, don't force extra lookups, and when an ordinary conversation is already captured, don't save it again by hand.

Search, injection and processing can cost context and model calls. Bring back background by relevance to the task and read originals only when the details are needed; don't promise a fixed saving. Whether it overlaps with the host's own memory depends on what each one covers; don't claim it replaces it wholesale.

## Folders and layered reading

OpenViking keeps where content belongs and where it came from through folders and URIs. The L0 abstract helps identify the topic, the L1 overview helps understand the content, and L2 is the original for checking details. Pick the level by the question, don't load the whole history by default, and don't make the user learn these terms first.

To continue work, start from the injected context and the related memories and notes, and read the session originals when details are needed. Across several sessions, check against dates, scope and the original records; don't take the latest session for the whole project. Don't reopen finished items, don't take earlier plans for what is live now, and go back to the original when summaries conflict.

## Across agents and identities

Once another session or agent connects to content the same identity can reach, it can continue the work with the existing background. It doesn't copy the whole chat window, device state, client rules or attachments that were never uploaded in real time, and the latest content that wasn't committed or processed yet may be missing.

Check identity and content availability only for the two ends the user chose, and don't install a second client just to explain. Verifying it takes an independent context that actually retrieves the background; reading it back in the original chat is not a cross-agent verification.

The same URL doesn't mean the same user. Binding or switching identities doesn't move earlier material; re-align the read and write scopes and keep the original config. Without an actual way to bind or switch, state the limit; don't invent commands.

## Preferences, experience and organizing knowledge

Preferences help the collaboration fit stable ways of expressing and judging things; apply them only where they fit the current work, don't turn a one-off choice into a lasting preference, and don't collect unrelated personal information to show the effect.

Experience keeps what worked, corrections and when they apply. Explain why it fits the current task rather than copying it mechanically; whether extraction is on and whether it is searchable needs checking, so don't promise experience will be produced every turn.

Organizing knowledge, Compile, suits building sourced topic knowledge or a wiki from several documents. It runs as `ov compile` in the `ov` command line (the installer's `cli` entry installs `ov`), is executed by the server's Compile Runtime, and writes its results back into OpenViking. First check that the server supports it, the source scope and where the output goes, then run it as the user authorizes; usage is at https://docs.openviking.ai/en/context-compilation/01-overview . Installing the plugin, importing history or producing an ordinary summary is not the same as running Compile.

## Team shared folders and permissions

Team members belong to the same account, each as a user in it. Each member's memories, sessions, personal material and private skills are under `viking://user/<their own user_id>/`, isolated per user, so other members can't read them. The account shares two places: `viking://resources/` (shared material) and `viking://agent/skills/` (shared skills). The plugin's automatic recall before each prompt covers their own space and these two.

The team decides the permissions on shared material. When the account has access control (ACL) off, which is the default, every member can read, write and delete anything under `viking://resources/`; when it is on, every member can still manage everything by default, and an admin can make a folder restricted, granting it only to chosen members or groups. Admins always keep management rights on shared material. A refused write returns `PERMISSION_DENIED`. Copying or moving doesn't carry the old permissions along: the new location inherits the target folder's permissions, and moving into the personal space stops sharing.

If they want their own folder to be editable only by them and readable by everyone else, it works only when the account has access control on; then run this on their own folder:

```bash
ov acl set viking://resources/<user_id> --acl-mode restricted --entry user:<user_id>=manage --entry 'user:*=read'
```

Do it only when they ask, and only on their own folder. With access control off, a regular member running this command gets `PERMISSION_DENIED … ACL management denied … manage permission required`. That means the team hasn't turned access control on, not that something is wrong with their permissions, and it doesn't affect their reading or writing in the shared folder; to protect their folder, they ask their team admin.

Shared skills are installed with `add_skill` (or `ov add-skill <folder> -p viking://agent/skills`), and other members' agents find and use them for fitting tasks; without `target_uri` the skill goes into their own private skills.
