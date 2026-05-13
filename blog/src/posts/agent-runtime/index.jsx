import React from 'react';
import {
  Article, Lead, P, H2, H3, H4, Pre, Quote, Pull, Callout, Hr,
  Cols, Col, Ol, Li, Ul, Table, A, InlineCode, Tag, Small,
} from '../../blog-components';

const card = {
  border: '1px solid var(--th-line)',
  borderRadius: 'var(--th-radius)',
  background: 'var(--th-bg-2)',
  padding: '1rem',
};

function BuildMap({ t }) {
  const steps = [
    ['01', '#process-owner', t({ en: 'Own the process', zh: '接管进程' }), t({ en: 'Spawn, stop, resume, and reset the CLI deliberately.', zh: '有意识地启动、停止、恢复和重置 CLI。' })],
    ['02', '#network-boundary', t({ en: 'Expose a control plane', zh: '暴露控制平面' }), t({ en: 'Keep files local while the product talks over WebSocket.', zh: '文件留在本机，产品通过 WebSocket 交互。' })],
    ['03', '#driver-contract', t({ en: 'Normalize runtimes', zh: '归一化运行时' }), t({ en: 'Convert Claude, Codex, Hermes, and others into one event shape.', zh: '把 Claude、Codex、Hermes 等转换成一种事件形态。' })],
    ['04', '#tool-plane', t({ en: 'Inject tools', zh: '注入工具' }), t({ en: 'Give the agent MCP tools for chat, tasks, files, and products.', zh: '通过 MCP 给 agent 聊天、任务、文件和产品工具。' })],
    ['05', '#openviking', t({ en: 'Attach memory', zh: '接入记忆' }), t({ en: 'Bind daemon identity to OpenViking context and long-term state.', zh: '把 daemon 身份绑定到 OpenViking 上下文和长期状态。' })],
  ];

  return (
    <div style={{ display: 'grid', gap: '0.75rem', margin: '1.5rem 0' }}>
      {steps.map(([n, href, title, detail]) => (
        <a
          key={n}
          href={href}
          style={{
            ...card,
            display: 'grid',
            gridTemplateColumns: '3.5rem 1fr',
            gap: '0.75rem',
            textDecoration: 'none',
            color: 'inherit',
          }}
        >
          <div style={{
            fontFamily: 'var(--th-font-mono)',
            color: 'var(--th-accent)',
            fontSize: '1.25rem',
            fontWeight: 700,
          }}>{n}</div>
          <div>
            <div style={{ fontFamily: 'var(--th-font-display)', fontWeight: 700 }}>{title}</div>
            <div style={{ color: 'var(--th-mute)', marginTop: '0.25rem' }}>{detail}</div>
          </div>
        </a>
      ))}
    </div>
  );
}

function BoundaryDiagram({ t }) {
  const nodes = [
    [t({ en: 'Web app', zh: 'Web 应用' }), t({ en: 'messages, tasks, UI state', zh: '消息、任务、界面状态' })],
    [t({ en: 'Server', zh: '服务端' }), t({ en: 'auth, routing, fan-out', zh: '鉴权、路由、广播' })],
    [t({ en: 'Daemon', zh: 'Daemon' }), t({ en: 'local process owner', zh: '本地进程所有者' })],
    [t({ en: 'Runtime driver', zh: '运行时驱动' }), t({ en: 'Claude, Codex, ACP...', zh: 'Claude、Codex、ACP...' })],
    [t({ en: 'Agent CLI', zh: 'Agent CLI' }), t({ en: 'files, shell, tools', zh: '文件、命令、工具' })],
  ];

  return (
    <div style={{ ...card, margin: '1.5rem 0' }}>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(8.5rem, 1fr))',
        gap: '0.6rem',
        alignItems: 'stretch',
      }}>
        {nodes.map(([title, detail], index) => (
          <div key={title} style={{
            border: '1px solid var(--th-line)',
            borderRadius: 'var(--th-radius)',
            padding: '0.85rem',
            background: index === 2 ? 'color-mix(in oklab, var(--th-accent) 12%, transparent)' : 'transparent',
          }}>
            <Tag>{index === 2 ? t({ en: 'owner', zh: '所有者' }) : `0${index + 1}`}</Tag>
            <H4 toc={false}>{title}</H4>
            <Small>{detail}</Small>
          </div>
        ))}
      </div>
    </div>
  );
}

function RuleCard({ title, detail }) {
  return (
    <div style={card}>
      <H4 toc={false}>{title}</H4>
      <P>{detail}</P>
    </div>
  );
}

const AgentRuntime = ({ t }) => {
  const T = t;

  return (
    <Article>
      <Lead>{T({
        en: 'An agent daemon is the missing layer between a terminal coding assistant and a collaborative product: it owns the local process, translates runtime-specific events, injects tools, and gives the agent a durable identity.',
        zh: 'Agent daemon 是终端编程助手和协作型产品之间缺失的那一层：它接管本地进程，翻译不同运行时的事件，注入工具，并给 agent 一个可持久化的身份。',
      })}</Lead>

      <P dropCap>
        {T({
          en: 'A CLI agent is designed for one human sitting at one terminal. That is the wrong boundary for a product like ',
          zh: 'CLI agent 的默认使用方式，是一个人坐在一个终端前操作。对 ',
        })}
        <A href="https://github.com/ZaynJarvis/zouk">Zouk</A>
        {T({
          en: ', where agents are colleagues: they receive messages while asleep, claim tasks, reply in threads, survive browser reloads, and carry memory across days. The daemon is not a smarter prompt. It is the operating boundary around the agent.',
          zh: ' 这样的协作产品来说，这个边界不对：agent 是同事，它会在休眠时收到消息，会认领任务，会在线程里回复，会穿过浏览器刷新继续存在，也会把记忆带到几天之后。Daemon 不是更聪明的 prompt，而是 agent 外面那层运行边界。',
        })}
      </P>

      <P>{T({
        en: 'The shape below comes from building Zouk Daemon against several real runtimes: Claude Code stream-json, Codex app-server JSON-RPC, Hermes/Coco/OpenCode over ACP, plus custom HTTP-backed agents. The details differ, but the construction sequence stays stable.',
        zh: '下面这套形态来自 Zouk Daemon 的真实实现：Claude Code 的 stream-json、Codex app-server 的 JSON-RPC、Hermes/Coco/OpenCode 的 ACP，以及自定义 HTTP agent。细节各不相同，但构建顺序是稳定的。',
      })}</P>

      <BuildMap t={T} />

      <H2 id="process-owner">{T({ en: 'Start With Process Ownership', zh: '从进程所有权开始' })}</H2>

      <P>{T({
        en: 'The first job is boring and decisive: the daemon, not the web app and not the terminal, must own the child process. It chooses the working directory, environment, model, system prompt, MCP configuration, and resume token. It also decides when the process is allowed to die.',
        zh: '第一件事很朴素，但决定成败：子进程必须由 daemon 拥有，而不是由 web app 或某个终端拥有。Daemon 选择工作目录、环境变量、模型、系统提示词、MCP 配置和恢复用的 session token，也决定进程什么时候可以退出。',
      })}</P>

      <Pre lang="js" filename="driver-contract.ts">{`export interface Driver {
  id: string;
  supportsStdinNotification: boolean;
  busyDeliveryMode: "notification" | "direct" | "none";

  spawn(ctx: SpawnContext): { process: ChildProcess };
  parseLine(line: string): ParsedEvent[];
  encodeStdinMessage(text: string, sessionId: string | null): string | null;
  buildSystemPrompt(config: AgentConfig, agentId: string): string;
}`}</Pre>

      <P>{T({
        en: 'That small interface is the useful abstraction. Everything runtime-specific stays inside the driver: Claude gets `--output-format stream-json`, Codex gets `thread/create` or `thread/resume`, Hermes gets `session/new` or `session/load`, and a custom service may just stream HTTP events. The rest of the daemon sees one process and one event stream.',
        zh: '这个小接口就是有用的抽象。所有运行时私有细节都留在 driver 里：Claude 走 `--output-format stream-json`，Codex 走 `thread/create` 或 `thread/resume`，Hermes 走 `session/new` 或 `session/load`，自定义服务也许只是流式 HTTP 事件。Daemon 其他部分只看到一个进程和一种事件流。',
      })}</P>

      <Callout type="note">
        <P>{T({
          en: 'Do not start by designing a multi-agent framework. Start by making one agent process observable, restartable, and boring to operate.',
          zh: '不要一上来就设计多 agent 框架。先把一个 agent 进程做成可观测、可重启、可稳定运维的东西。',
        })}</P>
      </Callout>

      <H3>{T({ en: 'Sessions Are Not Processes', zh: '会话不等于进程' })}</H3>

      <P>{T({
        en: 'A process is disposable. A session is the agent runtime’s conversation state. Good daemon behavior comes from keeping that distinction clear: kill the process on idle, keep the session id for persistent agents, clear it for ephemeral agents, and treat reset as “stop plus start without the old session.”',
        zh: '进程是可以丢弃的；session 是 agent runtime 的对话状态。Daemon 的行为是否清晰，关键在于把两者分开：空闲时可以杀进程，持久 agent 保留 session id，临时 agent 清掉 session id，而 reset 应该是“停止后不带旧 session 重新启动”。',
      })}</P>

      <Pre lang="js" filename="lifecycle.js">{`async function stopProcess(child) {
  if (!child || child.exitCode !== null) return;
  child.kill("SIGTERM");
  await waitForExit(child, 5000).catch(() => child.kill("SIGKILL"));
}

function cacheIdleAgent(agent) {
  return {
    config: { ...agent.config, sessionId: agent.sessionId },
    workingDirectory: agent.workingDirectory,
    lastActivity: agent.lastActivity,
  };
}`}</Pre>

      <P>{T({
        en: 'This is where many daemons become flaky. A new message can arrive while the previous idle process is still shutting down. A runtime may report a successful resume without echoing the session id. Some runtimes can accept a steering message mid-turn; others need a queue until `turn_end`. These are lifecycle rules, not UI rules, so they belong in the daemon.',
        zh: '很多 daemon 的不稳定都出在这里：上一轮空闲进程还没完全退出，新消息已经来了；某个运行时成功恢复了 session，却没有把 session id 回显回来；有些运行时能在回合中接收 steer 消息，有些必须等到 `turn_end` 再投递。这些是生命周期规则，不是 UI 规则，所以应该放在 daemon 里。',
      })}</P>

      <Pre lang="js" filename="same-agent-race.js">{`const pendingStop = agentsStopping.get(agentId);
if (pendingStop) await pendingStop;

if (agents.has(agentId)) {
  await stopAgent(agentId, { wait: true });
}

agentsStarting.set(agentId, startAgentProcess(agentId, config));`}</Pre>

      <P>{T({
        en: 'That guard looks small, but it prevents a subtle class of bugs: two processes believing they own the same agent slot, same workspace, and same session. A daemon is mostly a set of these small invariants.',
        zh: '这个保护看起来很小，但它防住了一类隐蔽问题：两个进程同时以为自己拥有同一个 agent slot、同一个 workspace 和同一个 session。Daemon 很大一部分价值，就是这些小不变量。',
      })}</P>

      <Hr ornament />

      <H2 id="network-boundary">{T({ en: 'Put The Network Boundary Above The Daemon', zh: '把网络边界放在 Daemon 之上' })}</H2>

      <P>{T({
        en: 'The product needs network access. The agent needs local access. Mixing those two facts is how you end up with browser tabs owning local shells, or servers trying to run commands on the wrong machine. Keep the server as a relay and the daemon as the local authority.',
        zh: '产品需要网络访问，agent 需要本地访问。把这两件事混在一起，就会变成浏览器标签页拥有本地 shell，或者服务端试图在错误的机器上执行命令。让 server 做中继，让 daemon 做本地权威。',
      })}</P>

      <BoundaryDiagram t={T} />

      <Cols count={2}>
        <Col>
          <H3>{T({ en: 'Server responsibilities', zh: 'Server 的职责' })}</H3>
          <Ul>
            <Li>{T({ en: 'Authenticate humans and agents.', zh: '给人和 agent 做鉴权。' })}</Li>
            <Li>{T({ en: 'Route channel, DM, thread, and task messages.', zh: '路由频道、DM、线程和任务消息。' })}</Li>
            <Li>{T({ en: 'Broadcast status and activity to clients.', zh: '把状态和活动广播给客户端。' })}</Li>
          </Ul>
        </Col>
        <Col>
          <H3>{T({ en: 'Daemon responsibilities', zh: 'Daemon 的职责' })}</H3>
          <Ul>
            <Li>{T({ en: 'Resolve the local workspace and runtime config.', zh: '解析本地 workspace 和运行时配置。' })}</Li>
            <Li>{T({ en: 'Start, stop, idle-cache, and resume processes.', zh: '启动、停止、空闲缓存和恢复进程。' })}</Li>
            <Li>{T({ en: 'Translate raw runtime output into product events.', zh: '把运行时原始输出翻译成产品事件。' })}</Li>
          </Ul>
        </Col>
      </Cols>

      <Pre lang="js" filename="wire-events.json">{`{ "type": "agent:start", "agentId": "agent-louise", "config": { "runtime": "codex" } }
{ "type": "agent:deliver", "agentId": "agent-louise", "message": "Fix task #82" }
{ "type": "agent:activity", "agentId": "agent-louise", "activity": "Reading files..." }
{ "type": "agent:status", "agentId": "agent-louise", "status": "idle" }`}</Pre>

      <Pull>{T({
        en: 'The server should know that an agent is active. It should not need to know whether that activity came from Claude stream-json, Codex JSON-RPC, or ACP.',
        zh: 'Server 应该知道某个 agent 正在工作，但不应该关心这份活动来自 Claude stream-json、Codex JSON-RPC，还是 ACP。',
      })}</Pull>

      <H3>{T({ en: 'Reconnect Should Be Boring', zh: '重连应该很无聊' })}</H3>

      <P>{T({
        en: 'The daemon connects outward to the server, so it should assume the socket will drop. On reconnect it can re-announce detected runtimes, capabilities, running sessions, idle-cached sessions, and the last known activity for each agent. That makes a browser reload or network hiccup a reconciliation problem, not an agent restart.',
        zh: 'Daemon 是主动向 server 建立连接的，所以它应该假设 WebSocket 会断。重连时，它重新声明已探测到的运行时、能力、运行中的 session、idle-cache 里的 session，以及每个 agent 最后的活动状态。这样浏览器刷新或网络抖动只是状态对齐问题，而不是 agent 重启问题。',
      })}</P>

      <H2 id="driver-contract">{T({ en: 'Normalize The Runtime Stream', zh: '归一化运行时事件流' })}</H2>

      <P>{T({
        en: 'Every useful runtime emits a different stream. Claude sends `system`, `assistant`, and `result` records. Codex uses thread events with item updates. ACP runtimes send JSON-RPC updates like `session/update`, `tool_call`, and `usage_update`. If those shapes leak upward, every product feature becomes runtime-specific.',
        zh: '每个有用的运行时都会发出不同的事件流。Claude 发送 `system`、`assistant`、`result` 记录；Codex 使用 thread events 和 item updates；ACP 运行时发送 `session/update`、`tool_call`、`usage_update` 这类 JSON-RPC 更新。如果这些形态泄漏到上层，每个产品功能都会变成运行时特化。',
      })}</P>

      <Pre lang="js" filename="parsed-event.ts">{`type ParsedEvent =
  | { kind: "session_init"; sessionId: string }
  | { kind: "thinking"; text: string }
  | { kind: "text"; text: string }
  | { kind: "tool_call"; name: string; input?: unknown }
  | { kind: "context_usage"; contextUsage: ContextUsageSnapshot }
  | { kind: "turn_end" }
  | { kind: "error"; message: string };`}</Pre>

      <Table
        headers={[
          T({ en: 'Raw signal', zh: '原始信号' }),
          T({ en: 'Daemon event', zh: 'Daemon 事件' }),
          T({ en: 'Product behavior', zh: '产品行为' }),
        ]}
        rows={[
          [<InlineCode>system.init</InlineCode>, <InlineCode>session_init</InlineCode>, T({ en: 'Store the session id for resume.', zh: '保存 session id，用于恢复。' })],
          [<InlineCode>assistant.text_delta</InlineCode>, <InlineCode>text</InlineCode>, T({ en: 'Append visible assistant output.', zh: '追加可见的 assistant 输出。' })],
          [<InlineCode>tool_call</InlineCode>, <InlineCode>tool_call</InlineCode>, T({ en: 'Show activity, summarize inputs, keep users oriented.', zh: '展示活动、摘要输入，让用户知道 agent 在做什么。' })],
          [<InlineCode>usage_update</InlineCode>, <InlineCode>context_usage</InlineCode>, T({ en: 'Render token/context pressure before the turn ends.', zh: '在回合结束前展示 token/context 压力。' })],
          [<InlineCode>result</InlineCode>, <InlineCode>turn_end</InlineCode>, T({ en: 'Flush queued messages and transition to idle.', zh: '清空排队消息，并进入 idle。' })],
        ]}
      />

      <P>{T({
        en: 'The important part is not the names. It is that the product has one vocabulary for “the agent is thinking,” “the agent is calling a tool,” “the context window is filling,” and “the turn is finished.” That is what makes model switching and runtime switching product features instead of rewrites.',
        zh: '重点不在这些名字本身，而在于产品拥有一套统一词汇来表达“agent 正在思考”“agent 正在调用工具”“上下文窗口正在变满”“这一轮结束了”。这样模型切换和运行时切换才是产品能力，而不是重写工程。',
      })}</P>

      <H3>{T({ en: 'Activity Is Part Of The UX', zh: '活动状态是用户体验的一部分' })}</H3>

      <P>{T({
        en: 'A daemon that only forwards final text makes the agent feel frozen. Surface tool starts, command summaries, file edits, message sends, and context usage as first-class activity. Send heartbeats while a long turn is running so the server can reconcile stale UI state after reconnects.',
        zh: '只转发最终文本的 daemon，会让 agent 看起来像卡住了。工具开始、命令摘要、文件编辑、发送消息、上下文使用量，都应该作为一等活动状态暴露出来。长回合运行时还要发 heartbeat，这样重连后 server 能修正过期的界面状态。',
      })}</P>

      <Hr ornament />

      <H2 id="tool-plane">{T({ en: 'Give The Agent Product Tools', zh: '给 Agent 产品工具' })}</H2>

      <P>{T({
        en: 'Once the process and event stream are stable, the next boundary is the tool plane. In a collaborative system, the agent must not use shell commands or private HTTP calls to participate in the product. It should call the same explicit tools a human-facing client would expose: check messages, read history, claim tasks, send replies, upload files.',
        zh: '进程和事件流稳定之后，下一个边界是工具层。在协作系统里，agent 不应该靠 shell 命令或私有 HTTP 调用参与产品，而应该调用产品显式暴露的工具：检查消息、阅读历史、认领任务、发送回复、上传文件。',
      })}</P>

      <Pre lang="js" filename="chat-bridge-mcp.json">{`{
  "name": "chat",
  "command": "node",
  "args": [
    "dist/chat-bridge.js",
    "--agent-id", "agent-louise",
    "--server-url", "https://zouk.example",
    "--auth-token", "daemon-scoped-token"
  ]
}`}</Pre>

      <P>{T({
        en: 'Different runtimes accept that MCP bridge differently. Claude, Gemini, Copilot, and Kimi can read a generated MCP config file. ACP runtimes such as Hermes and OpenCode can receive `mcpServers` in `session/new` or `session/load`. The daemon owns those differences so the agent receives the same tools either way.',
        zh: '不同运行时接入这个 MCP bridge 的方式不同。Claude、Gemini、Copilot、Kimi 可以读取生成的 MCP 配置文件；Hermes、OpenCode 这类 ACP 运行时可以在 `session/new` 或 `session/load` 里接收 `mcpServers`。这些差异由 daemon 吸收，agent 最终拿到的是同一组工具。',
      })}</P>

      <Callout type="warn">
        <P>{T({
          en: 'Tool calls are also activity. If the runtime emits a direct tool-call shape and a nested tool-call shape, parse both and dedupe by tool-call id. Otherwise the agent may be doing real work while the product shows nothing.',
          zh: '工具调用也是活动状态。如果某个运行时既可能发直接的 tool-call 形态，也可能发嵌套的 tool-call 形态，就要两种都解析，并按 tool-call id 去重。否则 agent 明明在工作，产品界面却什么都不显示。',
        })}</P>
      </Callout>

      <H3>{T({ en: 'Multi-agent Is Scheduling, Not Telepathy', zh: '多 Agent 是调度，不是心灵感应' })}</H3>

      <P>{T({
        en: 'Two agents do not need to secretly talk to each other. They need shared state and a scheduler. A task board, a thread, a game board, or a queue can decide whose turn it is. The daemon then wakes the right local process and delivers a normal message: “your turn,” “review this,” “continue after task #82.”',
        zh: '两个 agent 不需要彼此偷偷对话。它们需要共享状态和调度器。任务板、线程、棋盘、队列都可以决定轮到谁。Daemon 只要唤醒正确的本地进程，并投递一条普通消息：“轮到你了”“评审这个”“接 task #82 继续”。',
      })}</P>

      <Quote cite={T({ en: 'Daemon design rule', zh: 'Daemon 设计规则' })}>
        {T({
          en: 'Let the product own coordination. Let the daemon own the local process. Let tools be the only way the agent changes shared state.',
          zh: '让产品负责协调，让 daemon 负责本地进程，让工具成为 agent 改变共享状态的唯一方式。',
        })}
      </Quote>

      <Hr ornament />

      <H2 id="openviking">{T({ en: 'Connect The Daemon To OpenViking', zh: '把 Daemon 接到 OpenViking' })}</H2>

      <P>{T({
        en: 'The last step is memory and context. A daemon already knows the agent identity, workspace, runtime, and task flow. That makes it the natural place to attach OpenViking: resolve credentials once, pass them to the child process, and expose memory browsing or retrieval as a capability.',
        zh: '最后一步是记忆和上下文。Daemon 已经知道 agent 身份、workspace、运行时和任务流，所以它天然适合接入 OpenViking：统一解析凭证，把它们传给子进程，并把记忆浏览或检索暴露成能力。',
      })}</P>

      <P>{T({
        en: 'Keep the roles separate. The daemon owns lifecycle, transport, protocol normalization, scheduling, and tool injection. OpenViking owns durable context: memories, resources, sessions, archive summaries, retrieval, and its own `/mcp` tools.',
        zh: '这里要分清职责。Daemon 负责生命周期、传输、协议归一化、调度和工具注入；OpenViking 负责持久上下文：记忆、资源、session、归档摘要、检索，以及它自己的 `/mcp` 工具。',
      })}</P>

      <P>{T({
        en: 'The OpenViking docs now frame this as an integration-depth choice: generic MCP clients call OpenViking on demand; hooks-based plugins drive recall and capture from lifecycle events; SDK integrations wire retrieval and storage into framework-native abstractions. A daemon sits closest to the hooks-based path because it already sees start, prompt, turn end, compaction, idle, and session end.',
        zh: 'OpenViking 文档现在把这件事描述成“集成深度”的选择：通用 MCP 客户端按需调用 OpenViking；hooks-based plugin 通过生命周期事件驱动 recall 和 capture；SDK 集成则把检索和存储接入框架原生抽象。Daemon 最接近 hooks-based 路线，因为它本来就能看到 start、prompt、turn end、compaction、idle 和 session end。',
      })}{' '}
        <A href="https://docs.openviking.ai/en/agent-integrations/01-overview">{T({ en: 'Agent Integrations Overview', zh: 'Agent 集成概览' })}</A>
      </P>

      <Table
        headers={[
          T({ en: 'Integration path', zh: '集成路径' }),
          T({ en: 'What it gives the daemon', zh: '给 daemon 带来的能力' }),
        ]}
        rows={[
          [
            <InlineCode>/mcp</InlineCode>,
            T({ en: 'Explicit tools such as search, read, store, list, grep, glob, and add_resource. The model chooses when to call them.', zh: '显式工具，例如 search、read、store、list、grep、glob、add_resource。模型决定什么时候调用。' }),
          ],
          [
            T({ en: 'Lifecycle hooks', zh: '生命周期 hooks' }),
            T({ en: 'Automatic recall before a turn and automatic capture/commit after a turn, without asking the model to remember the memory protocol.', zh: '回合前自动 recall，回合后自动 capture/commit，不需要模型记住记忆协议。' }),
          ],
          [
            T({ en: 'Runtime plugins', zh: '运行时插件' }),
            T({ en: 'Codex/OpenCode-style explicit memory tools or session-sync plugins when the runtime has its own extension surface.', zh: '当运行时有自己的扩展面时，可以接 Codex/OpenCode 风格的显式记忆工具或 session-sync 插件。' }),
          ],
          [
            T({ en: 'SDK / framework', zh: 'SDK / 框架' }),
            T({ en: 'LangChain/LangGraph-style retrievers, stores, middleware, and chat-history backends for agents built inside a framework.', zh: '面向框架内 agent 的 LangChain/LangGraph 风格 retriever、store、middleware 和 chat-history backend。' }),
          ],
        ]}
      />

      <P>{T({
        en: 'For an agent daemon, the practical answer is usually both: use lifecycle integration for the things that must always happen, and also register OpenViking’s `/mcp` endpoint so the model can make explicit context decisions when it needs to inspect or store something.',
        zh: '对 agent daemon 来说，实际答案通常是两者都要：必须稳定发生的事情走生命周期集成；同时注册 OpenViking 的 `/mcp` 端点，让模型在需要主动检查或存储内容时能显式做上下文决策。',
      })}</P>

      <Pre lang="js" filename="openviking-config.js">{`const openviking = {
  baseUrl: process.env.OPENVIKING_URL
    ?? process.env.OPENVIKING_BASE_URL
    ?? "http://127.0.0.1:1933",
  apiKey: process.env.OPENVIKING_API_KEY
    ?? process.env.OPENVIKING_BEARER_TOKEN
    ?? "",
  accountId: process.env.OPENVIKING_ACCOUNT ?? "",
  userId: process.env.OPENVIKING_USER ?? "",
  agentId: process.env.OPENVIKING_AGENT_ID ?? "agent-runtime",
  timeoutMs: 15000,
  recallLimit: 6,
  recallTokenBudget: 2000,
};`}</Pre>

      <Pre lang="js" filename="openviking-env.sh">{`OPENVIKING_URL=https://ov.example
OPENVIKING_API_KEY=ov_user_key
OPENVIKING_ACCOUNT=default
OPENVIKING_USER=zayn
OPENVIKING_AGENT_ID=louise
OPENVIKING_CLI_CONFIG_FILE=/agent-data/louise/openviking/ovcli.conf`}</Pre>

      <P>{T({
        en: 'In Zouk Daemon, daemon-wide OpenViking config can come from environment variables, `~/.openviking/ovcli.conf`, or `~/.openviking/ov.conf`. Server-provisioned per-agent credentials can override that. The daemon then writes a per-agent `ovcli.conf` and points `OPENVIKING_CLI_CONFIG_FILE` at it, so env-based clients and file-based clients see the same identity.',
        zh: '在 Zouk Daemon 里，daemon 级 OpenViking 配置可以来自环境变量、`~/.openviking/ovcli.conf` 或 `~/.openviking/ov.conf`。服务端下发的 per-agent 凭证可以覆盖这些配置。Daemon 随后会写入 per-agent 的 `ovcli.conf`，并把 `OPENVIKING_CLI_CONFIG_FILE` 指向它，这样读环境变量的客户端和读配置文件的客户端看到的是同一个身份。',
      })}</P>

      <Table
        headers={[
          T({ en: 'Daemon input', zh: 'Daemon 输入' }),
          T({ en: 'HTTP header', zh: 'HTTP Header' }),
          T({ en: 'Meaning', zh: '含义' }),
        ]}
        rows={[
          [<InlineCode>OPENVIKING_API_KEY</InlineCode>, <InlineCode>Authorization</InlineCode>, T({ en: 'Authentication. OpenViking also accepts `X-API-Key`.', zh: '鉴权。OpenViking 也接受 `X-API-Key`。' })],
          [<InlineCode>OPENVIKING_ACCOUNT</InlineCode>, <InlineCode>X-OpenViking-Account</InlineCode>, T({ en: 'Workspace or tenant boundary.', zh: 'Workspace 或租户边界。' })],
          [<InlineCode>OPENVIKING_USER</InlineCode>, <InlineCode>X-OpenViking-User</InlineCode>, T({ en: 'Human or service principal whose memory/session is accessed.', zh: '被访问记忆和 session 的人或服务主体。' })],
          [<InlineCode>OPENVIKING_AGENT_ID</InlineCode>, <InlineCode>X-OpenViking-Agent</InlineCode>, T({ en: 'Stable agent identity for agent memory, skills, and routing.', zh: '用于 agent 记忆、技能和路由的稳定 agent 身份。' })],
        ]}
      />

      <Callout type="note">
        <P>{T({
          en: 'Account, user, and agent are routing identity, not a substitute for authentication. In normal API-key mode, use a user key for data access. If a root key touches tenant data, it must provide account and user headers. In trusted mode, only let a trusted gateway inject those headers.',
          zh: 'Account、user、agent 是路由身份，不是鉴权本身。在普通 API key 模式下，数据访问优先使用 user key。Root key 如果访问租户数据，必须带 account 和 user headers。Trusted mode 下，只应该让可信网关注入这些 headers。',
        })}</P>
      </Callout>

      <Cols count={3}>
        <Col>
          <RuleCard
            title={T({ en: 'Probe capability', zh: '探测能力' })}
            detail={T({ en: 'Probe daemon-wide OpenViking on connect, but keep memory browsing available because per-agent credentials may arrive later.', zh: '连接时探测 daemon 级 OpenViking，但保留记忆浏览能力，因为 per-agent 凭证可能稍后到达。' })}
          />
        </Col>
        <Col>
          <RuleCard
            title={T({ en: 'Pass identity down', zh: '向下传递身份' })}
            detail={T({ en: 'The daemon and the child process should agree on `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, and `OPENVIKING_AGENT_ID`.', zh: 'Daemon 和子进程应该对 `OPENVIKING_ACCOUNT`、`OPENVIKING_USER`、`OPENVIKING_AGENT_ID` 保持一致。' })}
          />
        </Col>
        <Col>
          <RuleCard
            title={T({ en: 'Use URI boundaries', zh: '使用 URI 边界' })}
            detail={T({ en: 'Expose memory and resources as `viking://` URIs so the agent can cite, browse, and retrieve context without prompt stuffing.', zh: '把记忆和资源暴露为 `viking://` URI，让 agent 能引用、浏览和检索上下文，而不是只靠塞 prompt。' })}
          />
        </Col>
      </Cols>

      <P>{T({
        en: 'This is the point where the daemon stops being just a launcher. It becomes the agent’s durable host. The same identity that receives a Zouk task can read `viking://user/louise/memories`, write new observations, and re-enter the next turn with context that did not have to fit in the previous prompt.',
        zh: '到这里，daemon 就不只是 launcher 了，而是 agent 的持久宿主。同一个收到 Zouk 任务的身份，可以读取 `viking://user/louise/memories`，写入新的观察，并在下一轮带着不需要塞进上一轮 prompt 的上下文回来。',
      })}</P>

      <Pre lang="js" filename="memory-rpc.json">{`{ "type": "agent:memory:list", "agentId": "agent-louise", "uri": "/" }
{ "type": "agent:memory:read", "agentId": "agent-louise", "uri": "viking://user/louise/memories/project-notes" }
{ "type": "agent:memory:content", "agentId": "agent-louise", "content": "..." }`}</Pre>

      <H3>{T({ en: 'The Operational Loop', zh: '运行循环' })}</H3>

      <Ol>
        <Li>{T({ en: 'Start or point at an OpenViking server and verify `GET /health`.', zh: '启动或指向一个 OpenViking server，并验证 `GET /health`。' })}</Li>
        <Li>{T({ en: 'Load daemon-level or per-agent config, then pass the resolved identity to the spawned runtime.', zh: '加载 daemon 级或 per-agent 配置，再把解析后的身份传给被启动的运行时。' })}</Li>
        <Li>{T({ en: 'Before a user turn, retrieve bounded memories or resources and inject only the useful context block.', zh: '用户回合前，检索有边界的记忆或资源，只注入有用的上下文块。' })}</Li>
        <Li>{T({ en: 'After a turn, append sanitized user and assistant messages into a persistent OpenViking session.', zh: '回合后，把清理过的用户和 assistant 消息追加到持久 OpenViking session。' })}</Li>
        <Li>{T({ en: 'On compaction or session end, commit the session so archive and memory extraction can run.', zh: '压缩或 session 结束时 commit session，让归档和记忆抽取流程运行。' })}</Li>
        <Li>{T({ en: 'Register `${OPENVIKING_URL}/mcp` with the same auth so the model can explicitly search, read, store, and add resources.', zh: '用同一套鉴权注册 `${OPENVIKING_URL}/mcp`，让模型能显式 search、read、store、add_resource。' })}</Li>
      </Ol>

      <Callout type="tip">
        <P>{T({
          en: 'Treat OpenViking connection as the final daemon boundary, not as an afterthought inside the prompt. The daemon can enforce identity, discover availability, and make the same context substrate available to every runtime.',
          zh: '把 OpenViking 接入当成 daemon 的最后一条边界，而不是 prompt 里的补丁。Daemon 能执行身份约束、发现可用性，并把同一个上下文底座提供给所有运行时。',
        })}</P>
      </Callout>

      <Hr ornament />

      <H2>{T({ en: 'The Checklist', zh: '落地清单' })}</H2>

      <P>{T({
        en: 'A production agent daemon does not need to be large. It does need the right invariants. If you can answer these questions, the architecture is probably sound.',
        zh: '生产级 agent daemon 不一定很大，但必须有正确的不变量。如果下面这些问题都有答案，架构大概率是稳的。',
      })}</P>

      <Ol>
        <Li>{T({ en: 'Can the daemon kill an idle process without losing the persistent session?', zh: 'Daemon 能不能杀掉空闲进程，同时不丢失持久 session？' })}</Li>
        <Li>{T({ en: 'Can a reset start a truly fresh runtime instead of accidentally resuming old state?', zh: 'Reset 能不能启动真正干净的新运行时，而不是意外恢复旧状态？' })}</Li>
        <Li>{T({ en: 'Can the server render activity without understanding runtime-specific event formats?', zh: 'Server 能不能在不了解运行时私有事件格式的情况下渲染活动状态？' })}</Li>
        <Li>{T({ en: 'Can messages arriving mid-turn be queued or steered according to that runtime’s capabilities?', zh: '回合中到达的新消息，能不能按该运行时能力选择排队或 steer？' })}</Li>
        <Li>{T({ en: 'Can the agent change shared product state only through explicit tools?', zh: 'Agent 改变共享产品状态时，是否只能通过显式工具？' })}</Li>
        <Li>{T({ en: 'Can OpenViking identify the account, user, and agent behind every memory operation?', zh: '每次记忆操作背后的 account、user、agent，OpenViking 是否都能识别？' })}</Li>
      </Ol>

      <P>{T({
        en: 'That is the build order: own the process, put a network control plane above it, normalize runtime streams, inject product tools, and attach durable context. Once those boundaries exist, “multi-agent” is no longer magic. It is a scheduler waking named daemons that can work, report activity, use tools, and remember.',
        zh: '构建顺序就是这样：接管进程，在它之上放网络控制面，归一化运行时事件流，注入产品工具，再接上持久上下文。有了这些边界之后，“多 agent”就不再神秘。它只是一个调度器在唤醒具名 daemon；这些 daemon 能工作、能汇报活动、能使用工具，也能记住。',
      })}</P>
    </Article>
  );
};

export default {
  id: 'agent-runtime',
  Component: AgentRuntime,
  meta: {
    title: { en: 'Building an Agent Daemon', zh: '构建 Agent Daemon' },
    description: {
      en: 'A practical build order for turning a terminal agent into a managed daemon with tools, runtime adapters, and OpenViking-backed memory.',
      zh: '把终端 agent 变成托管 daemon 的实践路径：进程管理、运行时适配、工具注入，以及接入 OpenViking 记忆。',
    },
    cover: '/assets/covers/runtime.png',
    publishedAt: '2026-05-08',
    updatedAt: '2026-05-13',
    readingTime: 14,
    category: { en: 'Engineering', zh: '工程' },
    tags: ['agent', 'daemon', 'mcp', 'openviking'],
    languages: ['en', 'zh'],
    authors: [{ name: 'Zayn', github: 'ZaynJarvis', role: { en: 'Engineer', zh: '工程师' } }],
  },
};
