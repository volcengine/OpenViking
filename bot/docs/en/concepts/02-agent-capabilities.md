# Agent Capabilities

VikingBot's Agent capabilities combine context, Skills, tools, sandboxing, and automation. Context tells the model who it is, what it knows, and how it should work. Tools and the sandbox determine what it can actually do.

## Context Construction

ContextBuilder organizes model input in this order:

```text
Bot identity
  + Sandbox environment description
  + Workspace bootstrap files
  + Full content of Always Skills
  + Summaries of available Skills
  + OpenViking Profile, Memories, and Experiences
  + Local or compressed conversation history
  + Text and media for the current turn
```

Workspace bootstrap files provide a stable identity and operating rules. Images and other media are converted into multimodal content blocks supported by the Provider.

## Skills and Tools

| Concept | Purpose | Form |
|---------|---------|------|
| **Skill** | Tells the Agent how to complete a class of tasks | `SKILL.md` instructions and resources |
| **Tool** | Lets the Agent perform a concrete operation | A JSON Schema function registered with the model |

Skills use progressive loading. Every turn includes the full content of Always Skills. Other Skills contribute only their name, description, and path until the Agent reads them with `read_file`. SkillsLoader checks dependencies such as commands and environment variables so unavailable capabilities are not presented as ready.

A Skill may orchestrate several tools, but it does not receive additional permissions automatically. Tool visibility still depends on the runtime mode, channel settings, request parameters, and sandbox.

## Default Tools

| Category | Tools | Purpose |
|----------|-------|---------|
| Files | `read_file`, `write_file`, `edit_file`, `list_dir` | Operate on workspace files |
| Commands | `exec` | Execute shell commands through the sandbox backend |
| Web | `web_search`, `web_fetch` | Search and read web pages |
| OpenViking | `openviking_list/search/grep/glob/multi_read` | Browse, retrieve, and read context |
| OpenViking | `openviking_add_resource`, `openviking_memory_commit` | Add resources and commit memory |
| Delivery | `message`, `generate_image` | Send messages proactively or generate images |
| Automation | `cron` | Manage scheduled Agent tasks |
| Parallel work | `spawn` | Start a background subagent |

ToolRegistry handles registration, argument validation, execution, and Hooks. ToolContext gives each call the current SessionKey, sender identity, channel metadata, sandbox, and authenticated OpenViking connection.

OpenAPI's `disabled_tools` can hide tools per request. A channel with `ov_tools_enable=false` hides OpenViking tools and disables automatic memory context. `readonly` mode does not register resource-write tools.

## MCP Extensions

`bot.tools.mcp_servers` connects external MCP Servers over `stdio`, `sse`, or `streamableHttp`. Remote tools are wrapped as ordinary VikingBot Tools and registered as `mcp_<server>_<tool>`.

Each MCP Server can configure:

- a launch command or remote URL;
- environment variables and request headers;
- an `enabled_tools` allowlist;
- the per-call `tool_timeout`.

MCP parameter Schemas are normalized for compatibility before being passed to the model and ToolRegistry.

## Subagents

The main Agent uses `spawn` to submit independent work to SubagentManager. A subagent shares the model and corresponding workspace but receives a restricted tool set:

- file, command, and web tools remain available;
- `message` is excluded so the subagent cannot send externally;
- `spawn` is excluded to prevent recursive subagents;
- Cron, image generation, and OpenViking tools are excluded.

When a subagent finishes, it reports the result to the main session. The main Agent remains responsible for identity-sensitive actions and final delivery.

## Sandbox and Workspace

SandboxManager selects a workspace from SessionKey and `sandbox.mode`:

| Mode | Workspace scope |
|------|-----------------|
| `shared` | All sessions share `workspace/shared` |
| `per-session` | Every session has an independent directory |
| `per-channel` | Sessions on the same channel instance share a directory |

The current implementation provides these execution backends:

| Backend | Characteristics |
|---------|-----------------|
| `direct` | Executes directly on the Bot host and is not a strong isolation boundary by default |
| `srt` | Supports file and network allow/deny policies |
| `opensandbox` | Creates isolated environments through OpenSandbox Server |
| `aiosandbox` | Executes commands and file operations through AIO Sandbox |

In Direct mode, `restrict_to_workspace=false` may allow files and commands to access content outside the workspace. For services exposed to untrusted users, choose an isolated backend and configure explicit network and file policies.

On first use, SandboxManager copies bootstrap files such as AGENTS, SOUL, USER, TOOLS, and IDENTITY, together with enabled Skills.

## Multimodal Capabilities

VikingBot supports three kinds of multimodal interaction:

- channel image input becomes model vision content blocks;
- `generate_image` uses `agents.gen_image_model` for text-to-image and supported image-to-image operations;
- Telegram audio can be transcribed through GroqTranscriptionProvider.

Generated images can be delivered directly to the originating channel through a message callback. Whether the model can understand images depends on the selected Provider and model.

## Cron and Heartbeat

Both proactive execution mechanisms ultimately call AgentLoop:

| Capability | Trigger | Use case |
|------------|---------|----------|
| Cron | `at`, `every`, or a cron expression | Timed reminders and recurring jobs |
| Heartbeat | Periodically reads `HEARTBEAT.md` from the workspace | Continuously check a changing set of tasks |

Cron jobs are persisted in `cron/jobs.json` and retain the original SessionKey and channel metadata. When `deliver=true`, the result is sent back to the originating channel.

Heartbeat skips empty files, Sessions that explicitly disable heartbeat, and long-inactive Sessions. The Agent returns `HEARTBEAT_OK` when no work is required.

## Hooks

HookManager provides runtime extension points. The current built-in Hooks mainly handle:

- `message.compact`: synchronize OpenViking Session messages incrementally and commit at configured thresholds;
- `tool.post_call`: retrieve related Experiences after the Agent reads a Skill and append them to its content.

Custom Hooks can be loaded through `bot.hooks`.

## Implementation Locations

| Area | Path |
|------|------|
| Context and Skills | `vikingbot/agent/context.py`, `skills.py` |
| Tool system | `vikingbot/agent/tools/` |
| Subagents | `vikingbot/agent/subagent.py` |
| Sandbox | `vikingbot/sandbox/` |
| Automation | `vikingbot/cron/`, `vikingbot/heartbeat/` |
| Hooks | `vikingbot/hooks/` |

## Related Documentation

- [VikingBot Architecture](./01-architecture.md)
- [Channels, Gateway, and Operations](./03-channels-and-gateway.md)
- [OpenViking Integration](./04-openviking-integration.md)
