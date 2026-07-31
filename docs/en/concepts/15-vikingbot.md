# VikingBot: A Multi-Channel AI Agent Powered by OpenViking

VikingBot is a multi-channel AI Agent provided by OpenViking. OpenViking manages long-term context such as Resources, Memories, and Skills; VikingBot receives user messages, assembles context, invokes models and tools, and delivers results to a command line, chat platform, or HTTP client.

Together, they allow an Agent not only to complete the current task, but also to accumulate user memories, session summaries, and task experience for future work.

## Responsibilities of VikingBot and OpenViking

| Component | Primary responsibility | Typical capabilities |
|-----------|------------------------|----------------------|
| **OpenViking** | Context storage, organization, and retrieval | Resources, Memories, Skills, Sessions, semantic retrieval, memory and experience extraction |
| **VikingBot** | Agent runtime and interaction | Multi-channel messaging, model reasoning, tool calls, Skill execution, sandboxing, automation, and result delivery |


## System Overview

```text
CLI / Feishu / Slack / Telegram / Discord / Email / HTTP API
                              │
                              ▼
                    Channel + MessageBus
                              │
                              ▼
                         AgentLoop
                Context → Model → Tools → Model
                    │                   │
          ┌─────────┴─────────┐         ▼
          ▼                   ▼    Replies and events
  OpenViking Context     Tools / Skills
  Resource / Memory      Files / Shell / Web
  Experience / Session   MCP / Cron / Subagent
          │                   │
          └─────────┬─────────┘
                    ▼
        Session synchronization and learning
```

Every entry point ultimately uses the same AgentLoop. Channel-specific events are converted into common messages, so models and tools do not need to know whether a request came from the CLI, Feishu, or an HTTP API.

## Core Capabilities

### Multiple Entry Points and Channels

VikingBot supports three types of entry point:

- `vikingbot chat` and `ov chat`: one-shot or interactive command-line conversations;
- Feishu, Slack, Telegram, Discord, WhatsApp, DingTalk, QQ, Email, and MoChat: long-running chat bots;
- the `/bot/v1` HTTP API: synchronous Chat, SSE streaming events, Sessions, and feedback.

Each Channel handles platform authentication, sender allowlists, media parsing, reply formatting, and session routing. VikingBot uses `type + channel_id + chat_id` to isolate channel instances and conversations.

### Agent Execution Loop

AgentLoop is the execution core of VikingBot. Each message goes through the following flow:

1. load identity, workspace rules, Skills, session history, and OpenViking context;
2. call the configured model;
3. when the model returns a tool call, validate and execute it through ToolRegistry;
4. add the tool result to the context and call the model again;
5. produce the final response, save the Session, and deliver it to the originating Channel.

The Provider layer normalizes text, reasoning, streaming deltas, tool calls, and token usage. The Bot inherits OpenViking's root-level `vlm` by default, or it can use a dedicated model configured through `bot.agents`.

### Tools, Skills, and Subagents

VikingBot includes file, Shell, Web, image, scheduling, and OpenViking tools, and it can connect to external MCP Servers.

| Capability | Purpose |
|------------|---------|
| **Tool** | Perform a concrete action such as reading a file, running a command, searching, or sending a message |
| **Skill** | Provide the workflow, constraints, and supporting resources for a class of tasks |
| **MCP** | Register external service capabilities as ordinary Agent tools |
| **Subagent** | Run an independent complex task in the background and return its result to the main Agent |

Skills are loaded progressively, so complete instructions are read only when needed. Tool visibility is controlled by the runtime mode, Channel configuration, request parameters, and sandbox.

### Sandboxes and Workspaces

File and Shell tools execute through SandboxManager. Workspaces can be shared by every conversation or isolated by Session or Channel.

VikingBot supports Direct, SRT, OpenSandbox, and AIO Sandbox backends. `direct` uses the Bot process permissions directly and is not a strongly isolated environment. Deployments exposed to untrusted users should use an isolated backend with explicit filesystem and network policies.

### Automation and Proactive Tasks

VikingBot provides two proactive execution mechanisms:

- **Cron**: triggers the Agent at a one-time timestamp, fixed interval, or cron schedule;
- **Heartbeat**: periodically reads `HEARTBEAT.md` from the workspace to check ongoing tasks.

Both use the same AgentLoop and can deliver results to the original Session and Channel.

### Gateway and Service Operation

`vikingbot gateway` combines the following capabilities into a long-running service:

- configured chat Channels;
- the Bot HTTP API and SSE streaming events;
- AgentLoop, Sessions, Cron, and Heartbeat;
- OpenViking API proxying;
- user feedback, outcome evaluation, logs, and optional Langfuse observability.

After an OpenViking upstream is configured, Bot Chat and `/api/v1/*` can use the same Gateway address. The Gateway Token and OpenViking user identity remain separate security boundaries.

## How OpenViking Enhances VikingBot

### Resources: Task Knowledge

Resources provide documents, code, web pages, and other external knowledge. VikingBot can use semantic retrieval, browse paths, run grep or glob searches, and read complete content only when the current task needs it.

### Memories: User and Peer Context

VikingBot reads the Peer Profile for the trusted current `actor_peer_id` and recalls three categories of memory:

- `events`: relevant historical events and decisions;
- `entities`: people, projects, organizations, and other entities;
- `preferences`: user preferences, habits, and constraints.

This identity model allows users sharing one Gateway to retain isolated personal context.

### Experience: Reusable Task Knowledge

Experience stores methods that helped the Agent complete similar tasks in the past. VikingBot can recall relevant experience when a task starts, after reading a Skill, or before a write operation, reducing repeated trial and error.

### Sessions: From Conversation to Long-term Context

The local VikingBot Session stores runtime history and Channel state. The OpenViking Session handles message archiving, compressed summaries, and memory and experience extraction.

```text
Current task
  → Recall Resource / Memory / Experience
  → Agent executes with Skills and tools
  → Save the local Session
  → Incrementally synchronize and commit the OpenViking Session
  → Extract new Memory and Experience
  → Recall them in a future task
```

Ordinary conversations are synchronized according to policy. The Agent actively invokes the memory commit tool only when the user explicitly asks it to remember something long term.

## Three Runtime Entry Points

| Entry point | Best for | OpenViking connection |
|-------------|----------|-----------------------|
| `openviking-server --with-bot` | Complete local experience | Uses the OpenViking Server being started |
| `vikingbot chat` | Quick trials and Agent development | Optional; runs standalone when unavailable |
| `vikingbot gateway` | Long-running service, remote access, and chat platforms | Connects to an explicit or inherited Server, or runs standalone |

For installation, configuration, and startup instructions for each entry point, see [VikingBot Installation and Configuration](../guides/17-vikingbot.md).

## Identity and Security Boundaries

VikingBot applies access control at several layers:

- Channels restrict senders with policies such as `allow_from`;
- a non-localhost Gateway requires a Gateway Token;
- OpenViking Server validates User/Admin API Keys or trusted identities;
- request-scoped OpenViking connections are accepted only from a trusted Server proxy;
- the Sandbox controls filesystem, command, and network boundaries.

A Gateway Token protects only the Gateway entry point and does not replace an OpenViking user identity. Public or multi-user deployments should not process untrusted requests with the `direct` backend.

## Typical Use Cases

- personal or team assistants with long-term memory;
- knowledge and task bots connected to enterprise chat platforms;
- general-purpose Agents that need files, Shell, Web, MCP, and Skills;
- a unified Gateway exposing both Chat and OpenViking APIs;
- continuously improving Agents that retain feedback, outcomes, and task experience.

## Related Documentation

- [VikingBot Installation and Configuration](../guides/17-vikingbot.md)
- [Complete VikingBot documentation](https://github.com/volcengine/OpenViking/blob/main/bot/README.md)
- [VikingBot Architecture](https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/01-architecture.md)
- [Agent Capabilities](https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/02-agent-capabilities.md)
- [Channels, Gateway, and Operations](https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/03-channels-and-gateway.md)
- [VikingBot and OpenViking Integration](https://github.com/volcengine/OpenViking/blob/main/bot/docs/en/concepts/04-openviking-integration.md)
- [OpenViking Context Types](./02-context-types.md)
- [OpenViking Session Management](./08-session.md)
