import React from 'react';
import {
  Article, Lead, P, H2, H3, Pre, Quote, Callout, Hr,
  Ol, Li, Ul, A, InlineCode, Tag, Table,
} from '../../blog-components';

const AgentRuntime = ({ t }) => {
  const T = t;

  return (
    <Article>
      <Lead>{T({
        en: 'A CLI agent like Claude Code is designed for one human at one terminal. To make it a building block for a product — where agents receive messages while idle, participate in multi-agent workflows, and survive restarts — you need a daemon. This post builds one from scratch, in four progressive steps, with runnable code at each stage.',
        zh: 'Claude Code 这样的 CLI agent 是为一个人坐在一个终端前设计的。要把它变成产品的构建模块——让 agent 在空闲时接收消息、参与多 agent 工作流、在重启后继续存活——你需要一个 daemon。本文从零开始，分四步递进构建一个 daemon，每一步都有可运行的代码。',
      })}</Lead>

      <H2 id="step-1">{T({ en: 'Step 1: Spawn and Talk to Claude', zh: '第一步：启动 Claude 并与之对话' })}</H2>

      <P>{T({
        en: 'Claude Code supports a programmatic mode: pipe JSON in, get JSON out. This is the entire foundation.',
        zh: 'Claude Code 支持编程模式：JSON 进，JSON 出。这就是全部的基础。',
      })}</P>

      <Pre lang="bash" filename="terminal">{`claude --output-format stream-json --input-format stream-json --model sonnet`}</Pre>

      <P>{T({
        en: 'In Node.js, you spawn this as a child process and talk to it over stdin/stdout using NDJSON (newline-delimited JSON):',
        zh: '在 Node.js 里，你把它作为子进程启动，通过 stdin/stdout 用 NDJSON（换行分隔的 JSON）通信：',
      })}</P>

      <Pre lang="js" filename="0_run_claude.js">{`import { spawn } from "child_process";

const proc = spawn("claude", [
  "--output-format", "stream-json",
  "--input-format", "stream-json",
  "--model", "sonnet",
], { stdio: ["pipe", "pipe", "pipe"] });

// Send a message
function send(text) {
  proc.stdin.write(JSON.stringify({
    type: "user",
    message: { role: "user", content: [{ type: "text", text }] },
  }) + "\\n");
}

// Read the response stream
let buf = "";
proc.stdout.on("data", (chunk) => {
  buf += chunk.toString();
  const lines = buf.split("\\n");
  buf = lines.pop();
  for (const line of lines) {
    if (!line.trim()) continue;
    const ev = JSON.parse(line);

    if (ev.type === "system" && ev.subtype === "init") {
      console.log("session:", ev.session_id);
    }
    if (ev.type === "assistant") {
      for (const block of ev.message?.content || []) {
        if (block.type === "text") process.stdout.write(block.text);
        if (block.type === "tool_use")
          console.log("[tool]", block.name, block.input);
      }
    }
    if (ev.type === "result") {
      console.log("\\n[done]", ev.stop_reason);
    }
  }
});

send("Hello. Say hi back in one sentence.");`}</Pre>

      <P>{T({
        en: 'Three event types matter: ',
        zh: '三种事件类型需要关注：',
      })}
        <InlineCode>system.init</InlineCode>{T({ en: ' gives you the session ID, ', zh: ' 给你 session ID，' })}
        <InlineCode>assistant</InlineCode>{T({ en: ' carries text and tool calls, ', zh: ' 包含文本和工具调用，' })}
        <InlineCode>result</InlineCode>{T({ en: ' signals the turn is done. That\'s it. Everything else builds on this.', zh: ' 表示这轮结束了。就这些。其他一切都建立在此之上。' })}
      </P>

      <P>{T({
        en: 'You can also resume a previous conversation. When the process exits, save the session ID. Next time, pass it back:',
        zh: '你也可以恢复之前的对话。当进程退出时，保存 session ID，下次传回去：',
      })}</P>

      <Pre lang="js" filename="test_resume.js">{`// Phase 1: create session, teach it a secret
const proc1 = spawn("claude", [...baseArgs], { stdio: ["pipe", "pipe", "pipe"] });
send(proc1, "Remember: my secret code is PINEAPPLE-42.");
// ... wait for result, extract session_id, gracefully stop

// Phase 2: resume in a new process — no session_id in JSON needed
const proc2 = spawn("claude", [...baseArgs, "--resume", sessionId], {
  stdio: ["pipe", "pipe", "pipe"],
});
send(proc2, "What is my secret code?");
// → "PINEAPPLE-42" — the process died and came back, but the session survived`}</Pre>

      <Callout type="note">
        <P>{T({
          en: 'A process is disposable. A session is state. This distinction is the foundation of everything that follows.',
          zh: '进程是可以丢弃的，session 是状态。这个区别是后面所有内容的基础。',
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="step-2">{T({ en: 'Step 2: Split Into Server and Daemon', zh: '第二步：拆分成 Server 和 Daemon' })}</H2>

      <P>{T({
        en: 'A product needs network access. An agent needs local file access. Mixing the two is how you end up with browser tabs owning local shells. The fix: split into two processes connected by WebSocket.',
        zh: '产品需要网络访问，agent 需要本地文件访问。把两者混在一起，就会变成浏览器标签页拥有本地 shell。解法：拆成两个进程，用 WebSocket 连接。',
      })}</P>

      <Pre lang="js" filename="1a_ws_server.js">{`// NO-AGENT SIDE — accepts a WebSocket connection from the daemon,
// forwards user input, and logs what comes back.

const wss = new WebSocketServer({ port: 9876 });

wss.on("connection", (ws) => {
  console.log("Daemon connected. Type a prompt.");

  ws.on("message", (data) => {
    const ev = JSON.parse(data.toString());
    if (ev.type === "assistant") {
      for (const block of ev.message?.content || []) {
        if (block.type === "text") process.stdout.write(block.text);
        if (block.type === "tool_use")
          console.log("[tool]", block.name);
      }
    }
    if (ev.type === "result") {
      console.log("\\n[done]");
    }
  });
});

// Read from stdin, send to daemon
rl.on("line", (text) => {
  serverSocket.send(text);
});`}</Pre>

      <Pre lang="js" filename="1b_ws_daemon.js">{`// WITH-AGENT SIDE — connects to the server, spawns Claude locally,
// pipes prompts down and events up.

const ws = new WebSocket("ws://localhost:9876");

ws.on("open", () => {
  const proc = spawn("claude", CLAUDE_ARGS, {
    stdio: ["pipe", "pipe", "pipe"],
  });

  // Claude stdout → WebSocket (events flow UP)
  proc.stdout.on("data", (chunk) => {
    // ... parse NDJSON lines ...
    ws.send(JSON.stringify(ev));
  });

  // WebSocket → Claude stdin (prompts flow DOWN)
  ws.on("message", (data) => {
    const msg = { type: "user", message: { role: "user",
      content: [{ type: "text", text: data.toString() }] } };
    proc.stdin.write(JSON.stringify(msg) + "\\n");
  });
});`}</Pre>

      <P>{T({
        en: 'Run it in two terminals:',
        zh: '在两个终端中运行：',
      })}</P>

      <Pre lang="bash" filename="terminal">{`# Terminal 1: start the server
node 1a_ws_server.js

# Terminal 2: start the daemon
node 1b_ws_daemon.js`}</Pre>

      <P>{T({
        en: 'The server knows nothing about Claude. It sends text, receives events. The daemon knows nothing about the product. It owns the process and translates. This boundary is the entire architecture.',
        zh: 'Server 对 Claude 一无所知——它发送文本，接收事件。Daemon 对产品一无所知——它管理进程并做翻译。这个边界就是整个架构。',
      })}</P>

      <Hr ornament />

      <H2 id="step-3">{T({ en: 'Step 3: Add Multi-Agent and MCP Tools', zh: '第三步：添加多 Agent 和 MCP 工具' })}</H2>

      <P>{T({
        en: 'Raw stream-json events are Claude-specific. If you want to host multiple agents (possibly different runtimes), you need an envelope. And if agents need to interact with the world, they need tools — injected by the daemon at spawn time via MCP.',
        zh: 'stream-json 事件是 Claude 特有的。如果你想托管多个 agent（甚至不同运行时），你需要一个信封协议。如果 agent 需要与外部世界交互，它需要工具——由 daemon 在启动时通过 MCP 注入。',
      })}</P>

      <P>{T({
        en: 'Here\'s a full example: two Claude agents play Gomoku against each other. A game server manages the board. Each agent gets MCP tools to view the board and place stones.',
        zh: '这是一个完整的例子：两个 Claude agent 互相下五子棋。游戏服务器管理棋盘。每个 agent 通过 MCP 工具查看棋盘和落子。',
      })}</P>

      <H3>{T({ en: 'The MCP tool server', zh: 'MCP 工具服务器' })}</H3>

      <Pre lang="js" filename="3c_gomoku_mcp.js">{`// MCP stdio server — gives each Claude agent two tools:
//   view_board()       → GET /board from game server
//   place_stone(x, y)  → POST /move to game server

const tools = [
  {
    name: "view_board",
    description: "See the current board, whose turn, and last move.",
    inputSchema: { type: "object", properties: {}, required: [] },
  },
  {
    name: "place_stone",
    description: "Place your stone at (x, y). Server validates the move.",
    inputSchema: {
      type: "object",
      properties: {
        x: { type: "integer", description: "Column 0..14" },
        y: { type: "integer", description: "Row 0..14" },
      },
      required: ["x", "y"],
    },
  },
];

async function runTool(name, args) {
  if (name === "view_board") {
    const res = await fetch(\`\${GAME_SERVER}/board\`);
    const d = await res.json();
    return \`Turn: \${d.turn}  Moves: \${d.moveCount}\\n\\n\${d.board}\`;
  }
  if (name === "place_stone") {
    const res = await fetch(\`\${GAME_SERVER}/move\`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ color: COLOR, x: args.x, y: args.y }),
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || "move rejected");
    return \`Placed \${COLOR} at (\${args.x}, \${args.y}). Next: \${d.turn}.\`;
  }
}`}</Pre>

      <H3>{T({ en: 'The daemon injects tools at spawn time', zh: 'Daemon 在启动时注入工具' })}</H3>

      <Pre lang="js" filename="3b_gomoku_daemon.js">{`// Write a temporary MCP config that points at the tool server
const mcpConfig = {
  mcpServers: {
    gomoku: {
      command: "node",
      args: ["3c_gomoku_mcp.js"],
      env: { GOMOKU_COLOR: COLOR, GOMOKU_SERVER: "http://localhost:9879" },
    },
  },
};
fs.writeFileSync(tmpMcpPath, JSON.stringify(mcpConfig));

// Spawn Claude with rules and tools
const proc = spawn("claude", [
  "--output-format", "stream-json",
  "--input-format", "stream-json",
  "--model", "sonnet",
  "--mcp-config", tmpMcpPath,
  "--append-system-prompt", GOMOKU_RULES,
], { stdio: ["pipe", "pipe", "pipe"] });

// When the game server says "your_turn", nudge Claude
ws.on("message", (data) => {
  const msg = JSON.parse(data.toString());
  if (msg.type === "your_turn") {
    writeUser("Make your move.");
  }
});`}</Pre>

      <P>{T({
        en: 'The daemon also normalizes Claude\'s stream-json into a generic envelope — so the game server doesn\'t care whether it\'s Claude, Codex, or something else:',
        zh: 'Daemon 还会将 Claude 的 stream-json 归一化为通用信封——这样游戏服务器不关心运行的是 Claude、Codex 还是别的什么：',
      })}</P>

      <Pre lang="js" filename="normalizeAndEmit()">{`function normalizeAndEmit(agentId, ev) {
  if (ev.type === "system" && ev.subtype === "init") {
    ws.send(JSON.stringify({
      type: "agent:session", agentId, sessionId: ev.session_id,
    }));
    return;
  }

  if (ev.type === "assistant") {
    const entries = [];
    for (const block of ev.message?.content || []) {
      if (block.type === "text")
        entries.push({ kind: "text", text: block.text });
      if (block.type === "tool_use")
        entries.push({ kind: "tool_start", toolName: block.name,
                       toolInput: block.input });
    }
    if (entries.length) {
      ws.send(JSON.stringify({
        type: "agent:activity", agentId, entries,
      }));
    }
    return;
  }

  if (ev.type === "result") {
    ws.send(JSON.stringify({
      type: "agent:status", agentId, status: "idle",
    }));
  }
}`}</Pre>

      <P>{T({
        en: 'Run the gomoku demo in three terminals:',
        zh: '在三个终端中运行五子棋示例：',
      })}</P>

      <Pre lang="bash" filename="terminal">{`# Terminal 1: game server (board + TUI)
node 3a_gomoku_server.js

# Terminal 2: black agent
node 3b_gomoku_daemon.js black

# Terminal 3: white agent
node 3b_gomoku_daemon.js white`}</Pre>

      <P>{T({
        en: 'Two agents play a full game. The game server manages turns. Each agent only sees two tools. Neither agent knows the other exists — they just see a board and play.',
        zh: '两个 agent 完成一局完整的对弈。游戏服务器管理回合。每个 agent 只看到两个工具。它们互不知道对方的存在——只看到棋盘，然后下棋。',
      })}</P>

      <Quote cite={T({ en: 'Design rule', zh: '设计规则' })}>
        {T({
          en: 'Multi-agent is scheduling, not telepathy. Agents don\'t talk to each other. They share state (a board, a thread, a task queue) and a scheduler decides whose turn it is.',
          zh: '多 agent 是调度，不是心灵感应。Agent 之间不直接对话。它们共享状态（棋盘、线程、任务队列），由调度器决定轮到谁。',
        })}
      </Quote>

      <Hr ornament />

      <H2 id="step-4">{T({ en: 'Step 4: Session Resurrection (the Serverless Agent)', zh: '第四步：Session 复活（无服务器 Agent）' })}</H2>

      <P>{T({
        en: 'The gomoku demo keeps agents alive for the whole game. But in a real product, agents are mostly idle. You don\'t want a Claude process running 24/7 waiting for messages. The solution: kill the process when it\'s idle, resurrect it when a message arrives.',
        zh: '五子棋示例在整局游戏中保持 agent 存活。但在真实产品中，agent 大部分时间都在空闲。你不想让一个 Claude 进程 7×24 小时运行只为等消息。解法：空闲时杀掉进程，有消息来时复活它。',
      })}</P>

      <P>{T({
        en: 'This is the chat platform demo. An agent named Alice joins a team chat. She has tools to send messages, read history, and check for new messages — injected by the daemon at spawn time, calling back to the same platform that hosts her.',
        zh: '这就是聊天平台示例。一个叫 Alice 的 agent 加入团队聊天。她有发消息、读历史、检查新消息的工具——由 daemon 在启动时注入，回调到托管她的同一个平台。',
      })}</P>

      <H3>{T({ en: 'Three delivery modes', zh: '三种消息投递模式' })}</H3>

      <Pre lang="js" filename="4b_chat_daemon.js">{`function deliverMessage(agentId, message) {
  // Mode 1: agent is running and busy → write to stdin
  if (proc && !isIdle) {
    writeUser(\`New message: \${formatMessage(message)}\`);
    return;
  }

  // Mode 2: agent process exited → respawn with --resume
  if (!proc && idleCache) {
    startAgent(agentId, {
      ...cachedConfig,
      sessionId: idleCache.sessionId,  // ← resume the old conversation
    }, message);
    return;
  }

  // Mode 3: no agent at all → queue for later
  inbox.push(message);
}`}</Pre>

      <P>{T({
        en: 'The key is what happens when the process exits cleanly:',
        zh: '关键在于进程正常退出时发生了什么：',
      })}</P>

      <Pre lang="js" filename="4b_chat_daemon.js">{`proc.on("exit", (code) => {
  proc = null;

  if (code === 0) {
    // Clean exit → cache session for future --resume
    idleCache = { config: agentConfig, sessionId };
    console.log(\`Cached session \${sessionId} — will --resume on next message\`);

    // If messages arrived while exiting, restart immediately
    if (inbox.length > 0) {
      const nextMsg = inbox.shift();
      idleCache = null;
      startAgent(agentId, { ...agentConfig, sessionId }, nextMsg);
    }
  }
});`}</Pre>

      <H3>{T({ en: 'The recursive tool pattern', zh: '递归工具模式' })}</H3>

      <P>{T({
        en: 'The chat bridge MCP server is the most interesting part. Unlike the gomoku tools (which interact with an external game), these tools call back to the platform that spawned the agent:',
        zh: '聊天桥接 MCP 服务器是最有趣的部分。不同于五子棋工具（与外部游戏交互），这些工具回调到了启动 agent 的同一个平台：',
      })}</P>

      <Pre lang="js" filename="4c_chat_bridge_mcp.js">{`// The platform spawns the agent.
// The agent gets tools.
// The tools call back to the platform.
// The agent doesn't know this loop exists.

const tools = [
  {
    name: "send_message",
    description: "Send a message to a channel (#general, #random).",
    inputSchema: {
      type: "object",
      properties: {
        target: { type: "string" },
        content: { type: "string" },
      },
      required: ["target", "content"],
    },
  },
  {
    name: "read_history",
    description: "Read recent messages from a channel.",
    inputSchema: {
      type: "object",
      properties: {
        channel: { type: "string" },
      },
      required: ["channel"],
    },
  },
  {
    name: "check_messages",
    description: "Check for new undelivered messages.",
    inputSchema: { type: "object", properties: {}, required: [] },
  },
];

async function runTool(name, args) {
  if (name === "send_message") {
    const res = await fetch(
      \`\${SERVER_URL}/internal/agent/\${AGENT_ID}/send\`,
      { method: "POST", body: JSON.stringify(args) },
    );
    return \`Message sent to \${args.target}.\`;
  }
  // ... read_history, check_messages similarly
}`}</Pre>

      <Pre lang="bash" filename="terminal">{`# Terminal 1: chat server (multi-channel TUI)
node 4a_chat_server.js

# Terminal 2: agent daemon
node 4b_chat_daemon.js

# Then type in the server terminal. The agent responds, goes idle,
# and gets resurrected when you send another message.`}</Pre>

      <Callout type="note">
        <P>{T({
          en: 'The agent never knows it died. From Claude\'s perspective, the conversation is continuous. From the daemon\'s perspective, it\'s a sequence of short-lived processes sharing one session.',
          zh: 'Agent 从不知道自己死过。从 Claude 的视角看，对话是连续的。从 daemon 的视角看，这是一系列短命进程共享一个 session。',
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="recap">{T({ en: 'The Build Order', zh: '构建顺序' })}</H2>

      <Ol>
        <Li><strong>{T({ en: 'Own the process', zh: '接管进程' })}</strong>{T({ en: ' — spawn Claude as a child, talk JSON over stdin/stdout. Save the session ID.', zh: '——以子进程方式启动 Claude，通过 stdin/stdout 传 JSON。保存 session ID。' })}</Li>
        <Li><strong>{T({ en: 'Split server and daemon', zh: '拆分 server 和 daemon' })}</strong>{T({ en: ' — server handles users and routing. Daemon handles the local process. WebSocket in between.', zh: '——server 处理用户和路由，daemon 处理本地进程，中间用 WebSocket 连接。' })}</Li>
        <Li><strong>{T({ en: 'Normalize and inject tools', zh: '归一化并注入工具' })}</strong>{T({ en: ' — translate runtime events into a generic envelope. Give agents MCP tools to interact with the product.', zh: '——将运行时事件翻译成通用信封。通过 MCP 给 agent 注入与产品交互的工具。' })}</Li>
        <Li><strong>{T({ en: 'Add session resurrection', zh: '添加 session 复活' })}</strong>{T({ en: ' — kill idle processes, resume them on demand. The agent becomes serverless.', zh: '——杀掉空闲进程，按需恢复。Agent 变成无服务器的。' })}</Li>
      </Ol>

      <P>{T({
        en: 'Once these pieces are in place, "multi-agent" stops being magic. It\'s a scheduler waking named processes that can work, use tools, and remember.',
        zh: '这些部件就位后，"多 agent"就不再神秘。它只是一个调度器在唤醒具名进程——这些进程能工作、能用工具、也能记住。',
      })}</P>

      <Quote cite={T({ en: 'One rule', zh: '一条规则' })}>
        {T({
          en: 'Let the product own coordination. Let the daemon own the local process. Let tools be the only way the agent changes shared state.',
          zh: '让产品负责协调，让 daemon 负责本地进程，让工具成为 agent 改变共享状态的唯一方式。',
        })}
      </Quote>

      <Hr ornament />

      <H2 id="drivers">{T({ en: 'Beyond Claude: Runtime Drivers', zh: '不止 Claude：运行时驱动' })}</H2>

      <P>{T({
        en: 'Every example above uses Claude Code, but the daemon architecture is runtime-agnostic. The trick is a thin abstraction called a ',
        zh: '以上所有示例都使用 Claude Code，但 daemon 架构与运行时无关。关键是一个叫做 ',
      })}<strong>{T({ en: 'runtime driver', zh: '运行时驱动' })}</strong>{T({
        en: ' — each driver knows how to spawn a specific CLI, parse its event stream, and encode messages into its stdin protocol:',
        zh: ' 的薄抽象层——每个 driver 知道如何启动一个特定的 CLI、解析它的事件流、将消息编码成它的 stdin 协议：',
      })}</P>

      <Pre lang="ts" filename="driver interface">{`interface Driver {
  id: string;
  spawn(ctx: SpawnContext): { process: ChildProcess };
  parseLine(line: string): ParsedEvent[];
  encodeStdinMessage(text: string, sessionId: string | null): string | null;
  buildSystemPrompt(config: AgentConfig, agentId: string): string;
  busyDeliveryMode: "notification" | "direct" | "none";
}`}</Pre>

      <P>{T({
        en: 'In practice, agent CLIs fall into three protocol families:',
        zh: '实际上，agent CLI 分为三类协议：',
      })}</P>

      <Table
        headers={[
          T({ en: 'Protocol', zh: '协议' }),
          T({ en: 'Runtimes', zh: '运行时' }),
          T({ en: 'How it works', zh: '工作方式' }),
        ]}
        rows={[
          [
            T({ en: 'stream-json', zh: 'stream-json' }),
            'Claude, Cursor',
            T({ en: 'NDJSON over stdio. Events: system.init, assistant (text/tool_use/thinking), result.', zh: 'stdio 上的 NDJSON。事件：system.init、assistant（text/tool_use/thinking）、result。' }),
          ],
          [
            T({ en: 'ACP (JSON-RPC)', zh: 'ACP (JSON-RPC)' }),
            'Codex, Hermes, OpenCode, Coco',
            T({ en: 'JSON-RPC 2.0 over stdio. Methods: session/new, session/prompt, session/update. MCP servers passed in session/new.', zh: 'stdio 上的 JSON-RPC 2.0。方法：session/new、session/prompt、session/update。MCP 服务器在 session/new 中传入。' }),
          ],
          [
            T({ en: 'Custom JSON events', zh: '自定义 JSON 事件' }),
            'Copilot, Gemini',
            T({ en: 'JSON event streams with runtime-specific shapes. One-shot per turn — no stdin delivery.', zh: '运行时特有的 JSON 事件流。每轮单次——不支持 stdin 投递。' }),
          ],
        ]}
      />

      <P>{T({
        en: 'The delivery mode matters for product behavior. When a message arrives while the agent is busy:',
        zh: '投递模式影响产品行为。当 agent 忙碌时收到新消息：',
      })}</P>

      <Ul>
        <Li><InlineCode>direct</InlineCode>{T({ en: ' (Codex, Hermes) — pipe into the active turn via stdin. Agent sees it mid-work.', zh: '（Codex、Hermes）——通过 stdin 注入活跃回合。Agent 在工作中看到它。' })}</Li>
        <Li><InlineCode>notification</InlineCode>{T({ en: ' (Claude) — store and surface via check_messages tool. Agent polls when ready.', zh: '（Claude）——存储并通过 check_messages 工具暴露。Agent 准备好时轮询。' })}</Li>
        <Li><InlineCode>none</InlineCode>{T({ en: ' (Copilot, Gemini) — queue until the turn ends, then restart with the queued message.', zh: '（Copilot、Gemini）——排队等回合结束，然后用排队的消息重启。' })}</Li>
      </Ul>

      <P>{T({
        en: 'The daemon absorbs all of this. The server sends ',
        zh: 'Daemon 吸收了所有差异。Server 发送 ',
      })}<InlineCode>agent:deliver</InlineCode>{T({
        en: ' regardless of runtime — the driver decides how to get the message to the agent.',
        zh: '，不管运行时是什么——由 driver 决定如何把消息送达 agent。',
      })}</P>

      <Hr ornament />

      <H2>{T({ en: 'All Examples', zh: '所有示例' })}</H2>

      <P>{T({
        en: 'Every code snippet in this post is from a runnable demo. The full source is at ',
        zh: '本文所有代码片段都来自可运行的示例。完整源码在 ',
      })}
        <A href="https://github.com/ZaynJarvis/agent-runtime">github.com/ZaynJarvis/agent-runtime</A>
        {T({ en: ':', zh: '：' })}
      </P>

      <Ul>
        <Li><InlineCode>0_run_claude.js</InlineCode>{T({ en: ' — basic spawn + NDJSON communication', zh: '——基础启动 + NDJSON 通信' })}</Li>
        <Li><InlineCode>1a_ws_server.js</InlineCode> + <InlineCode>1b_ws_daemon.js</InlineCode>{T({ en: ' — WebSocket server/daemon split', zh: '——WebSocket server/daemon 拆分' })}</Li>
        <Li><InlineCode>2a</InlineCode>/<InlineCode>2b</InlineCode>{T({ en: ' — envelope normalization (zouk protocol)', zh: '——信封归一化（zouk 协议）' })}</Li>
        <Li><InlineCode>3a</InlineCode>/<InlineCode>3b</InlineCode>/<InlineCode>3c</InlineCode>{T({ en: ' — two-agent gomoku game with MCP tools', zh: '——双 agent 五子棋对弈 + MCP 工具' })}</Li>
        <Li><InlineCode>4a</InlineCode>/<InlineCode>4b</InlineCode>/<InlineCode>4c</InlineCode>{T({ en: ' — chat platform with session resurrection', zh: '——聊天平台 + session 复活' })}</Li>
        <Li><InlineCode>test_resume.js</InlineCode>{T({ en: ' — session resume proof', zh: '——session 恢复验证' })}</Li>
      </Ul>
    </Article>
  );
};

export default {
  id: 'agent-runtime',
  Component: AgentRuntime,
  meta: {
    title: { en: 'Building an Agent Daemon', zh: '构建 Agent Daemon' },
    description: {
      en: 'Four runnable demos that turn a CLI agent into a managed daemon — from process spawning to multi-agent games to serverless session resurrection.',
      zh: '四个可运行的示例，将 CLI agent 逐步变成托管 daemon——从进程管理到多 agent 对弈再到无服务器 session 复活。',
    },
    cover: '/assets/covers/runtime.png',
    publishedAt: '2026-05-08',
    updatedAt: '2026-05-20',
    readingTime: 10,
    category: { en: 'Engineering', zh: '工程' },
    tags: ['agent', 'daemon', 'mcp', 'claude-code'],
    languages: ['en', 'zh'],
    authors: [{ name: 'zayn', github: 'ZaynJarvis', role: { en: 'Engineer', zh: '工程师' } }],
  },
};
