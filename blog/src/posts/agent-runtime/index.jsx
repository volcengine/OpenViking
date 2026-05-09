import React, { useState } from 'react';
import {
  Article, Lead, P, H2, H3, Pre, Quote, Pull, Callout, Hr,
  Cols, Col, Ol, Li, Ul, Table, A, InlineCode, Strong, Tag, Figure,
} from '../../blog-components';

function StageIndicator({ current }) {
  const stages = [
    { n: '0', label: 'Process' },
    { n: '1', label: 'WebSocket' },
    { n: '2', label: 'Protocol' },
    { n: '3', label: 'MCP Tools' },
  ];
  return (
    <div style={{
      display: 'flex', gap: 0, margin: '32px 0',
      borderRadius: 'var(--th-radius)', overflow: 'hidden',
      border: '1px solid var(--th-line)',
    }}>
      {stages.map((s, i) => (
        <div key={s.n} style={{
          flex: 1, padding: '12px 8px', textAlign: 'center',
          background: i <= current ? 'var(--th-accent)' : 'transparent',
          color: i <= current ? 'var(--th-bg)' : 'var(--th-mute)',
          fontFamily: 'var(--th-font-mono)', fontSize: 11,
          letterSpacing: '0.1em', transition: 'all 0.2s',
          borderRight: i < 3 ? '1px solid var(--th-line)' : 'none',
        }}>
          <div style={{ fontWeight: 700, fontSize: 16 }}>{s.n}</div>
          {s.label}
        </div>
      ))}
    </div>
  );
}

function EventStream() {
  const events = [
    { type: 'system', sub: 'init', color: 'var(--th-accent-2)' },
    { type: 'assistant', sub: 'text', color: 'var(--th-accent)' },
    { type: 'assistant', sub: 'tool_use', color: 'var(--th-mute)' },
    { type: 'result', sub: 'done', color: 'var(--th-accent-2)' },
  ];
  return (
    <div style={{
      fontFamily: 'var(--th-font-mono)', fontSize: 12,
      border: '1px solid var(--th-line)', borderRadius: 'var(--th-radius)',
      overflow: 'hidden', margin: '24px 0',
    }}>
      <div style={{
        padding: '8px 12px', background: 'var(--th-bg-2)',
        fontSize: 10, letterSpacing: '0.15em', textTransform: 'uppercase',
        color: 'var(--th-mute)',
      }}>stdout event stream</div>
      {events.map((e, i) => (
        <div key={i} style={{
          padding: '8px 12px',
          borderTop: '1px solid var(--th-line)',
          display: 'flex', gap: 8, alignItems: 'center',
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: e.color, flexShrink: 0,
          }} />
          <span style={{ color: e.color }}>{e.type}</span>
          <span style={{ color: 'var(--th-mute)' }}>.{e.sub}</span>
        </div>
      ))}
    </div>
  );
}

const AgentRuntime = ({ t }) => {
  const T = t;
  const [stage, setStage] = useState(0);

  return (
    <Article>
      <Lead>{T({
        en: 'Claude Code is a terminal tool. But terminals are for humans. What happens when you pull the agent out and treat it as a long-running process?',
        zh: 'Claude Code 是一个终端工具。但终端是给人用的。如果把 agent 从终端里拿出来，当成一个长期运行的进程来管理，会发生什么？',
      })}</Lead>

      <P dropCap>{T({
        en: 'Most people use coding agents the same way: open a terminal, type a task, watch it work. This is fine for one person on one machine. But the moment you need two agents to collaborate, or want to control an agent from a web app, or need the agent to survive a terminal crash — the TUI model falls apart.',
        zh: '大多数人用编程 agent 的方式都一样：打开终端、输入任务、看它干活。一个人一台机器，这没问题。但当你需要两个 agent 协作，或者想从 web 端控制 agent，又或者需要 agent 在终端崩溃后还能继续运行 — TUI 模式就不够用了。',
      })}</P>

      <P>{T({
        en: 'This article walks through four steps to turn a CLI tool into a controllable runtime. Each step adds one capability: process management, network access, protocol abstraction, and external tools.',
        zh: '这篇文章走四步，把一个 CLI 工具变成可控的运行时。每一步加一个能力：进程管理、网络接入、协议抽象、外部工具。',
      })}</P>

      <StageIndicator current={stage} />

      <P>{T({
        en: 'Click any stage above to jump ahead, or keep reading in order.',
        zh: '点上面的阶段可以跳转，也可以按顺序读下去。',
      })}</P>

      <H2 id="process">{T({ en: 'Stage 0: The Agent as a Child Process', zh: '阶段 0：Agent 作为子进程' })}</H2>

      <P>{T({
        en: 'Claude CLI has a mode most people never use: stream-json. Instead of a pretty TUI, it reads and writes newline-delimited JSON on stdin/stdout. This is the seam we need.',
        zh: 'Claude CLI 有一个大多数人从来没用过的模式：stream-json。不渲染漂亮的 TUI，而是在 stdin/stdout 上读写换行分隔的 JSON。这就是我们需要的接缝。',
      })}</P>

      <Pre lang="js" filename="run_claude.js">{`const proc = spawn("claude", [
  "--dangerously-skip-permissions",
  "--output-format", "stream-json",
  "--input-format", "stream-json",
  "--model", "sonnet",
], { stdio: ["pipe", "pipe", "pipe"] });`}</Pre>

      <Callout type="tip">
        <P>{T({
          en: 'The two cat pipes in the full command trick the CLI into non-TTY mode. Without them, it tries to render a terminal UI and everything breaks.',
          zh: '完整命令里的两个 cat 管道是为了欺骗 CLI 进入非 TTY 模式。不加的话它会尝试渲染终端 UI，然后一切都炸了。',
        })}</P>
      </Callout>

      <P>{T({
        en: 'The output is a typed event stream, not just text:',
        zh: '输出是一个带类型的事件流，不只是文本：',
      })}</P>

      <EventStream />

      <P>{T({
        en: 'A system.init event gives you a session ID. assistant events carry text, thinking steps, and tool calls. result marks the end of a turn. To build anything reliable, you handle the full stream.',
        zh: 'system.init 事件给你一个 session ID。assistant 事件里有文本、思考步骤和工具调用。result 标志一个回合结束。要做任何靠谱的东西，必须处理完整的事件流。',
      })}</P>

      <H3>{T({ en: 'Sessions are lifecycles', zh: '会话是生命周期' })}</H3>

      <P>{T({
        en: 'In the TUI, /new feels like telling the agent to forget. Under the hood, the cleanest way to reset is to kill the process and start a new one. The session is the process.',
        zh: '在 TUI 里，/new 感觉像是让 agent 忘掉之前的事。但底层最干净的重置方式是杀掉进程、启动新的。会话就是进程。',
      })}</P>

      <Pre lang="js" filename="lifecycle.js">{`function gracefulStop(child) {
  return new Promise((resolve) => {
    if (!child || child.exitCode !== null) return resolve();
    const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
    child.on("exit", () => { clearTimeout(timer); resolve(); });
    child.stdin.end(); // closing stdin triggers session_end hook
  });
}`}</Pre>

      <P>{T({
        en: 'But processes and sessions are not the same thing. With --resume <session-id>, a new process can pick up where the old one left off. You get crash recovery for free.',
        zh: '但进程和会话又不完全是一回事。用 --resume <session-id>，新进程可以接着上一个的上下文继续。崩溃恢复白送。',
      })}</P>

      <Hr ornament />

      <H2 id="websocket">{T({ en: 'Stage 1: Bridging to the Network', zh: '阶段 1：接入网络' })}</H2>

      <P>{T({
        en: 'A controllable process is useful, but it is stuck on one machine. The next step is a WebSocket bridge: a server that clients connect to, and a daemon that manages the agent locally.',
        zh: '可控的进程很好用，但它困在一台机器上。下一步是做一个 WebSocket 桥：一个 server 让客户端连，一个 daemon 在本地管理 agent。',
      })}</P>

      <Cols count={2}>
        <Col>
          <H3>{T({ en: 'Server', zh: '服务端' })}</H3>
          <P>{T({
            en: 'A message broker. Clients connect to it. It knows nothing about Claude.',
            zh: '一个消息中间人。客户端连它。它对 Claude 一无所知。',
          })}</P>
        </Col>
        <Col>
          <H3>{T({ en: 'Daemon', zh: '守护进程' })}</H3>
          <P>{T({
            en: 'Runs on the local machine. Manages the Claude process. Forwards messages both ways.',
            zh: '跑在本地机器上。管理 Claude 进程。双向转发消息。',
          })}</P>
        </Col>
      </Cols>

      <Pull>{T({
        en: 'The agent stays local — where it can touch files and run commands. The server is just a relay.',
        zh: 'Agent 留在本地 — 能碰文件、能跑命令的地方。Server 只是中继。',
      })}</Pull>

      <H2 id="protocol">{T({ en: 'Stage 2: Protocol Normalization', zh: '阶段 2：协议归一化' })}</H2>

      <P>{T({
        en: 'The bridge works, but it leaks Claude-specific details to every client. stream-json is a proprietary format. If you swap the agent, you rewrite the world.',
        zh: '桥能用了，但它把 Claude 的私有细节泄露给了每个客户端。stream-json 是私有格式。换一个 agent，你得重写所有下游。',
      })}</P>

      <P>{T({
        en: 'The fix is a normalization layer in the daemon. Raw Claude events go in, generic agent events come out:',
        zh: '解决办法是在 daemon 里加一层归一化。原始 Claude 事件进去，通用 agent 事件出来：',
      })}</P>

      <Table
        headers={[
          T({ en: 'Claude event', zh: 'Claude 事件' }),
          T({ en: 'Normalized event', zh: '归一化事件' }),
          T({ en: 'What it means', zh: '含义' }),
        ]}
        rows={[
          [<InlineCode>system.init</InlineCode>, <InlineCode>agent:session</InlineCode>, T({ en: 'New session started', zh: '新会话开始' })],
          [<InlineCode>assistant</InlineCode>, <InlineCode>agent:activity</InlineCode>, T({ en: 'Agent is working', zh: 'Agent 在工作' })],
          [<InlineCode>result</InlineCode>, <InlineCode>agent:status</InlineCode>, T({ en: 'Turn finished', zh: '回合结束' })],
        ]}
      />

      <Callout type="note">
        <P>{T({
          en: 'This is more than re-wrapping JSON. The server now deals with a generic "agent resource," not "Claude output." Swap in a different agent runtime and the server code does not change.',
          zh: '这不只是换个 JSON 外壳。Server 现在面对的是通用的「agent 资源」，不是「Claude 的输出」。换一个 agent 运行时，server 代码不用动。',
        })}</P>
      </Callout>

      <H3>{T({ en: 'ACP vs MCP vs WebSocket', zh: 'ACP、MCP、WebSocket 的区别' })}</H3>

      <Ul>
        <Li><Strong>WebSocket</Strong> — {T({ en: 'transport layer, the pipe', zh: '传输层，管道' })}</Li>
        <Li><Strong>ACP</Strong> — {T({ en: 'agent-host protocol (stream-json is a proprietary ACP)', zh: 'agent-host 协议（stream-json 是私有的 ACP）' })}</Li>
        <Li><Strong>MCP</Strong> — {T({ en: 'tool-calling protocol, how agents discover and use external capabilities', zh: '工具调用协议，agent 发现和使用外部能力的方式' })}</Li>
      </Ul>

      <Hr ornament />

      <H2 id="mcp">{T({ en: 'Stage 3: Giving the Agent Hands', zh: '阶段 3：给 Agent 装上手' })}</H2>

      <P>{T({
        en: 'The last piece: external tools via MCP. The example is a Gomoku game — two agents playing five-in-a-row against each other.',
        zh: '最后一块拼图：通过 MCP 接入外部工具。示例是一个五子棋游戏 — 两个 agent 对弈。',
      })}</P>

      <Cols count={2}>
        <Col>
          <H3>{T({ en: 'Game server', zh: '游戏服务器' })}</H3>
          <P>{T({ en: 'Maintains board state. Exposes /board and /move endpoints. Schedules turns.', zh: '维护棋盘状态。暴露 /board 和 /move 端点。调度回合。' })}</P>
        </Col>
        <Col>
          <H3>{T({ en: 'MCP server', zh: 'MCP 服务器' })}</H3>
          <P>{T({ en: 'Translates agent tool calls into HTTP requests. Two tools: view_board and place_stone.', zh: '把 agent 的工具调用翻译成 HTTP 请求。两个工具：view_board 和 place_stone。' })}</P>
        </Col>
      </Cols>

      <Pre lang="js" filename="mcp_config.json">{`{
  "mcpServers": {
    "gomoku": {
      "command": "node",
      "args": ["gomoku_mcp.js"],
      "env": {
        "GOMOKU_COLOR": "black",
        "GOMOKU_SERVER": "http://localhost:3000"
      }
    }
  }
}`}</Pre>

      <Callout type="warn">
        <P>{T({
          en: 'MCP is not magic. It is an adapter layer. The agent decides when to use a tool; the tool itself is just a wrapper around an HTTP call. Game logic stays in the game server.',
          zh: 'MCP 不是魔法。它是适配层。Agent 决定什么时候用工具；工具本身只是 HTTP 调用的包装。游戏逻辑还是在游戏服务器里。',
        })}</P>
      </Callout>

      <H2>{T({ en: 'Multi-agent: less than you think', zh: '多 agent：比你想的简单' })}</H2>

      <P>{T({
        en: 'The two Gomoku agents never talk to each other. They do not even know the other exists. They interact through shared state — the board — mediated by a scheduler that sends "your turn" messages. This is how most useful multi-agent systems work in practice: not conversation, but shared state plus a simple coordination protocol.',
        zh: '两个五子棋 agent 从不互相说话。它们甚至不知道对方存在。它们通过共享状态 — 棋盘 — 交互，由一个调度器发送「轮到你了」消息。这就是大多数实用多 agent 系统的工作方式：不是对话，而是共享状态加简单的协调协议。',
      })}</P>

      <Quote cite={T({ en: 'From the codebase', zh: '来自代码库' })}>
        {T({
          en: 'The server tracks state, determines whose turn it is, and sends a simple "your_turn" message. The agents react to changes in the shared world.',
          zh: '服务器追踪状态，判断谁该走，发一条 "your_turn" 消息。Agent 对共享世界的变化做出反应。',
        })}
      </Quote>

      <Hr ornament />

      <H2>{T({ en: 'What this buys you', zh: '这带来了什么' })}</H2>

      <P>{T({
        en: 'Four scripts, four capabilities. A CLI tool becomes a managed process, reachable over the network, protocol-agnostic, and equipped with external tools. The foundation for any multi-agent system is not a framework — it is these four boundaries, drawn correctly.',
        zh: '四个脚本，四种能力。一个 CLI 工具变成可管理的进程、可网络访问、协议无关、能用外部工具。多 agent 系统的基础不是框架 — 是这四条边界，画对了就行。',
      })}</P>
    </Article>
  );
};

export default {
  id: 'agent-runtime',
  Component: AgentRuntime,
  meta: {
    title: { en: 'Building a Coding Agent Runtime', zh: '构建编程 Agent 运行时' },
    description: {
      en: 'Four steps to pull a CLI agent out of the terminal and turn it into a controllable, networked service.',
      zh: '四步把 CLI agent 从终端里拉出来，变成可控的、联网的服务。',
    },
    cover: 'assets/covers/runtime.svg',
    publishedAt: '2026-05-08',
    readingTime: 10,
    category: { en: 'Engineering', zh: '工程' },
    tags: ['agent', 'mcp', 'system'],
    languages: ['en', 'zh'],
    authors: [{ name: 'Zayn', github: 'ZaynJarvis', role: { en: 'Engineer', zh: '工程师' } }],
  },
};
