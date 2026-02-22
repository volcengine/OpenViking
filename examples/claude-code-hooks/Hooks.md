# Claude Code Hooks — Quick Reference

## Hook Events & Unique Params

| Hook | Key unique params |
|------|------------------|
| `SessionStart` | `source`, `model`, `agent_type` |
| `UserPromptSubmit` | `prompt` |
| `PreToolUse` | `tool_name`, `tool_use_id`, `tool_input` |
| `PermissionRequest` | `tool_name`, `tool_input`, `permission_suggestions` |
| `PostToolUse` | `tool_name`, `tool_input`, `tool_response`, `tool_use_id` |
| `PostToolUseFailure` | `tool_name`, `tool_input`, `error`, `is_interrupt` |
| `Notification` | `message`, `title`, `notification_type` |
| `SubagentStart` | `agent_id`, `agent_type` |
| `SubagentStop` | `agent_id`, `agent_type`, `agent_transcript_path`, `last_assistant_message`, `stop_hook_active` |
| `Stop` | `last_assistant_message`, `stop_hook_active` |
| `TeammateIdle` | `teammate_name`, `team_name` |
| `TaskCompleted` | `task_id`, `task_subject`, `task_description`, `teammate_name`, `team_name` |
| `ConfigChange` | `source`, `file_path` |
| `WorktreeCreate` | `name` |
| `WorktreeRemove` | `worktree_path` |
| `PreCompact` | `trigger`, `custom_instructions` |
| `SessionEnd` | `reason` |

## Common Params (all hooks)

`session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`
