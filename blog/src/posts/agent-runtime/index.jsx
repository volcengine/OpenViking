import React from 'react';
import {
  Article, Lead, P, H2, H3, Pre, Quote, Callout, Hr,
  Ol, Li, Ul, A, InlineCode, Tag, Table,
} from '../../blog-components';

const LLM_PATH = '/post/agent-runtime/llm.txt';
const ZOUK_DELIVERY_DOC = 'https://github.com/ZaynJarvis/zouk/blob/main/docs/agent-delivery-routing.md';
const ZOUK_LIFECYCLE_DOC = 'https://github.com/ZaynJarvis/zouk/blob/main/docs/agent-lifecycle.md#idle-delivery-and-wake-policy';
const OPENVIKING_MCP_DOC = 'https://github.com/volcengine/OpenViking/blob/main/docs/en/guides/06-mcp-integration.md';
const OPENVIKING_CLAUDE_PLUGIN_DOC = 'https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md';

const STREAM_JSON_INPUT = `{"type":"user","message":{"role":"user","content":[{"type":"text","text":"Say hi back in one sentence."}]}}`;

const STREAM_JSON_OUTPUT = `{"type":"system","subtype":"init","session_id":"sess_demo_01","model":"claude-sonnet","tools":[]}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Hi! I am ready to help."}]}}
{"type":"result","subtype":"success","is_error":false,"stop_reason":"end_turn","session_id":"sess_demo_01"}`;

function StreamJsonDemo({ T }) {
  return (
    <details className="runtime-io-demo">
      <summary>
        <span className="runtime-io-demo__title">{T({
          en: 'Expandable example: stdin JSON and stdout stream',
          zh: '可展开演示：stdin 输入 JSON 与 stdout 输出流',
        })}</span>
      </summary>
      <div className="runtime-io-demo__body">
        <P className="runtime-io-demo__intro">{T({
          en: 'Write one NDJSON line to stdin. Claude then emits multiple JSON events on stdout; this sample is trimmed, so real events may include more fields.',
          zh: '向 stdin 写入一行 NDJSON。Claude 会在 stdout 连续输出多条 JSON 事件；这里是精简示例，真实事件可能带更多字段。',
        })}</P>
        <div className="runtime-io-demo__grid">
          <section className="runtime-io-demo__panel runtime-io-demo__panel--input">
            <div className="runtime-io-demo__label">{T({ en: 'stdin input', zh: 'stdin 输入' })}</div>
            <pre className="runtime-io-demo__code"><code>{STREAM_JSON_INPUT}</code></pre>
          </section>
          <section className="runtime-io-demo__panel runtime-io-demo__panel--output">
            <div className="runtime-io-demo__label">{T({ en: 'stdout output stream', zh: 'stdout 输出流' })}</div>
            <pre className="runtime-io-demo__code"><code>{STREAM_JSON_OUTPUT}</code></pre>
          </section>
        </div>
      </div>
    </details>
  );
}

const AgentRuntime = ({ t }) => {
  const T = t;

  return (
    <Article>
      <Lead>{T({
        en: 'A CLI agent like Claude Code is designed for one human at one terminal. To make it a building block for a product — where agents receive messages while idle, participate in multi-agent workflows, survive restarts, and share durable context — you need a daemon plus a memory plane. This post builds the daemon in four runnable steps, then shows how OpenViking can provide the shared context layer.',
        zh: 'Claude Code 这样的 CLI agent 是为一个人坐在一个终端前设计的。要把它变成产品的构建模块——让 agent 在空闲时接收消息、参与多 agent 工作流、在重启后继续存活，并共享长期上下文——你需要一个 daemon，也需要一层 memory plane。本文先用四个可运行步骤构建 daemon，再讨论如何用 OpenViking 接上共享 context layer。',
      })}</Lead>

      <H2 id="step-1">{T({ en: 'I: Spawn and Talk to Claude', zh: 'I: 启动 Claude 并与之对话' })}</H2>

      <P>{T({
        en: 'Claude Code supports a programmatic mode: pipe JSON in, get JSON out. This is the entire foundation.',
        zh: 'Claude Code 支持编程模式：JSON 进，JSON 出。这就是全部的基础。',
      })}</P>

      <Pre lang="bash" filename="terminal">{`claude --output-format stream-json --input-format stream-json --model sonnet`}</Pre>

      <StreamJsonDemo T={T} />

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
// → "PINEAPPLE-42" — the old process exited, but the session continued`}</Pre>

      <Callout type="note">
        <P>{T({
          en: 'A process is disposable. A session is state. This distinction is the foundation of everything that follows.',
          zh: '进程是可以丢弃的，session 是状态。这个区别是后面所有内容的基础。',
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="step-2">{T({ en: 'II: Split Into Server and Daemon', zh: 'II: 拆分成 Server 和 Daemon' })}</H2>

      <P>{T({
        en: 'The product owns users, permissions, message routing, and shared cloud resources. The agent needs local files, shell access, and tools. To let the agent use those cloud resources without putting local execution inside the web app, split the system: a cloud server coordinates, and a local daemon owns the agent process over WebSocket.',
        zh: '云上产品负责用户、权限、消息路由和共享资源；本地 agent 需要访问文件、shell 和工具。要让 agent 使用这些云上资源，又不把本地执行能力塞进网页里，就把系统拆成两端：云上 server 负责协调，本地 daemon 负责启动 agent，并通过 WebSocket 收发任务和事件。',
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
        en: 'The server knows nothing about Claude. It sends product messages over WebSocket and receives runtime events back. The daemon does not need product semantics; it accepts WS messages, hands them to the matching agent process, and returns the output stream. This boundary is the entire architecture.',
        zh: 'Server 对 Claude 一无所知——它通过 WebSocket 发送产品侧消息，接收运行时事件。Daemon 不需要理解产品语义；它只接收 WS 发来的消息/事件，交给对应 agent 进程处理，再把输出流回传。这个边界就是整个架构。',
      })}</P>

      <Hr ornament />

      <H2 id="step-3">{T({ en: 'III: Add Multi-Agent and MCP Tools', zh: 'III: 添加多 Agent 和 MCP 工具' })}</H2>

      <P>{T({
        en: 'Raw stream-json events are Claude-specific. If you want to host multiple agents (possibly different runtimes), normalize them into one event shape: outer fields such as type, agentId, and entries tell the product how to route and display the event; the runtime-specific details stay inside. And if agents need to interact with the world, they need tools — injected by the daemon at spawn time via MCP.',
        zh: 'stream-json 事件是 Claude 特有的。如果你想托管多个 agent（甚至不同运行时），先把它们转成同一种事件格式：外层字段说明事件类型、agentId 和内容列表，产品按这一层做路由和展示；各运行时自己的细节放在里面。agent 要和外部世界交互时，再由 daemon 在启动时通过 MCP 注入工具。',
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
        en: 'The daemon also normalizes Claude\'s stream-json into that shared event shape — so the game server doesn\'t care whether it\'s Claude, Codex, or something else:',
        zh: 'Daemon 还会把 Claude 的 stream-json 归一化成这种统一事件格式——这样游戏服务器不关心运行的是 Claude、Codex 还是别的什么：',
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

      <H2 id="step-4">{T({ en: 'IV: Deliver Messages to Agents', zh: 'IV: 把消息投递给 agent' })}</H2>

      <P>{T({
        en: 'The gomoku demo is turn-based: the server asks an agent to move. A chat product is different. Users can send Alice a message at any time, so the platform needs a delivery path from "new chat message" to "the right agent process sees it".',
        zh: '五子棋示例是回合制：服务器轮到谁，就让哪个 agent 下棋。聊天产品不一样，用户随时都可能给 Alice 发消息。所以平台要解决的是一条投递链路：新的聊天消息来了，怎样让对应的 agent 进程收到它。',
      })}</P>

      <P>{T({
        en: 'This is the chat platform demo. An agent named Alice joins a team chat. The server turns user messages into agent:deliver events. The daemon receives those events and decides how to hand each message to the agent process.',
        zh: '这就是聊天平台示例。一个叫 Alice 的 agent 加入团队聊天。server 把用户消息转成 agent:deliver 事件；daemon 收到事件后，根据 agent 进程当前状态决定怎么把消息交给它。',
      })}</P>

      <P>{T({
        en: 'The production Zouk design splits this into two layers: delivery routing decides which agents should receive an agent:deliver frame, and lifecycle/wake policy decides how to wake the selected agent process.',
        zh: 'Zouk 的生产实现把这件事拆成两层：消息路由先决定哪些 agent 应该收到 agent:deliver；生命周期/唤醒策略再决定怎样唤醒被选中的 agent 进程。',
      })} <A href={ZOUK_DELIVERY_DOC}>{T({ en: 'Delivery routing doc', zh: '消息路由文档' })}</A>{T({ en: ' and ', zh: ' 和 ' })}<A href={ZOUK_LIFECYCLE_DOC}>{T({ en: 'idle wake policy', zh: 'idle 唤醒策略' })}</A>{T({ en: ' have the full contract.', zh: ' 里有完整设计。' })}</P>

      <H3>{T({ en: 'Message delivery paths', zh: '消息投递路径' })}</H3>

      <Pre lang="js" filename="4b_chat_daemon.js">{`function deliverMessage(agentId, message) {
  // 1. Process is busy: inject the message into the active turn
  if (proc && !isIdle) {
    writeUser(\`New message: \${formatMessage(message)}\`);
    return;
  }

  // 2. Process is alive but idle: wake it through stdin
  if (proc && isIdle) {
    isIdle = false;
    writeUser(\`New message: \${formatMessage(message)}\`);
    return;
  }

  // 3. Process has exited: start it again and resume context
  if (!proc && idleCache) {
    const cachedConfig = idleCache.config;
    const cachedSessionId = idleCache.sessionId;
    idleCache = null;
    startAgent(agentId, {
      ...cachedConfig,
      sessionId: cachedSessionId,
    }, message);
    return;
  }

  // 4. No process/config yet: queue for later
  inbox.push(message);
}`}</Pre>

      <P>{T({
        en: 'Only the third path needs session resume. Resume is not the product feature; it is one implementation detail that lets the daemon stop idle CLI processes while still keeping the next message in the same conversation.',
        zh: '只有第三条路径需要 resume session。Resume 不是产品功能本身，它只是一个实现细节：daemon 可以让空闲的 CLI 进程退出，等下一条消息来时再接回同一个对话上下文。',
      })}</P>

      <P>{T({
        en: 'To make that third path work, the daemon caches the session ID when the process exits cleanly:',
        zh: '为了让第三条路径可用，daemon 会在进程正常退出时保存 session ID：',
      })}</P>

      <Pre lang="js" filename="4b_chat_daemon.js">{`proc.on("exit", (code) => {
  proc = null;

  if (code === 0) {
    // Clean exit → keep enough state to deliver the next message
    idleCache = { config: agentConfig, sessionId };
    console.log(\`Agent idle; cached session \${sessionId}\`);

    // If messages arrived while exiting, restart immediately
    if (inbox.length > 0) {
      const nextMsg = inbox.shift();
      idleCache = null;
      startAgent(agentId, { ...agentConfig, sessionId }, nextMsg);
    }
  }
});`}</Pre>

      <H3>{T({ en: 'The recursive tool pattern', zh: '工具调用平台 API' })}</H3>

      <P>{T({
        en: 'The chat bridge MCP server is the most interesting part. Unlike the gomoku tools (which interact with an external game), these tools call back to the platform that spawned the agent:',
        zh: '聊天桥接 MCP 服务器是最有趣的部分。不同于五子棋工具（与外部游戏交互），这里的工具会调用托管 agent 的同一个平台：agent 调用 send_message/read_history，实际执行的是平台 API。',
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
# and receives the next message through the delivery path above.`}</Pre>

      <Callout type="note">
        <P>{T({
          en: 'From the chat-platform view, human-to-agent and agent-to-agent conversations are ordinary platform messages. The server selects relevant messages and pushes them over WebSocket as ',
          zh: '从群聊平台视角看，人和 agent、agent 和 agent 的沟通都先是平台消息。server 选出相关消息后，通过 WebSocket 的 ',
        })}<InlineCode>agent:deliver</InlineCode>{T({
          en: ' to the right agent daemon. When the agent needs to reply, read history, or check pending messages, it calls MCP tools such as ',
          zh: ' 发到对应 agent 的 daemon；agent 要回复、读历史或检查未投递消息时，再调用 ',
        })}<InlineCode>send_message</InlineCode>/<InlineCode>read_history</InlineCode>/<InlineCode>check_messages</InlineCode>{T({
          en: ' back into the platform API.',
          zh: ' 这些 MCP tools 回到平台 API。',
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="step-5">{T({ en: 'V: Externalize Memory', zh: 'V: 记忆上云' })}</H2>

      <P>{T({
        en: 'Once agents can receive messages, the next question is where durable context lives. A local file such as MEMORY.md is useful as a fast recovery index for one agent process, but it is not enough for a team platform. A platform needs a shared context plane: searchable, scoped, auditable, and usable across machines, restarts, and multiple agents.',
        zh: 'Agent 能收消息之后，下一个问题是长期上下文放在哪里。像 MEMORY.md 这样的本地文件适合作为单个 agent 的快速恢复索引，但它不够支撑团队平台。平台需要一个共享的 context plane：可检索、有权限边界、可审计，并且能跨机器、跨重启、跨 agent 使用。',
      })}</P>

      <P>{T({
        en: 'OpenViking plays that role. The point is not to upload the whole chat transcript forever. The useful writeback is distilled context: facts, user preferences, decisions, handoff notes, tool results, and resource references that should survive the current CLI process.',
        zh: 'OpenViking 扮演的就是这层 memory plane。重点不是把完整聊天 transcript 无脑上云，而是写入蒸馏后的上下文：事实、偏好、决策、handoff、工具结果、资源引用，以及其他应该跨当前 CLI 进程保留下来的内容。',
      })}</P>

      <H3>{T({ en: 'Two integration surfaces', zh: '两层集成边界' })}</H3>

      <Ol>
        <Li><strong>{T({ en: 'Agent / runtime layer', zh: 'Agent / runtime 层' })}</strong>{T({
          en: ' — run an OpenViking memory client or context client, either as a plugin or through the OpenViking MCP endpoint. It retrieves relevant memory/resources at startup or prompt submit, writes back distilled updates during the run, and commits a durable handoff before compaction, reset, or sleep.',
          zh: '——通过 plugin 或 OpenViking MCP endpoint 接一个 memory client / context client。启动或 prompt submit 时检索相关 memory/resource 并注入上下文；运行过程中增量 writeback；compaction、reset、sleep 前写 durable handoff。',
        })}</Li>
        <Li><strong>{T({ en: 'Platform / server layer', zh: 'Platform / server 层' })}</strong>{T({
          en: ' — do provisioning and governance. The platform binds workspace/account/user/agent identity, issues scoped credentials or bearer tokens, exposes the OpenViking endpoint to daemons/runtimes, and owns permission checks, key rotation, audit, resource ownership, and cross-agent sharing boundaries.',
          zh: '——负责 provisioning + governance。平台绑定 workspace/account/user/agent 身份，签发 scoped credential 或 bearer token，把 OpenViking endpoint 提供给 daemon/runtime，并负责权限、key rotation、审计、资源归属和跨 agent 共享边界。',
        })}</Li>
      </Ol>

      <Callout type="warn">
        <P>{T({
          en: 'Do not put OpenViking API keys in browser code. The browser can show context inspection UI, but credentials should stay on the server, daemon, or runtime side.',
          zh: '不要把 OpenViking API key 放进浏览器。浏览器可以展示 context inspect / search / debug UI，但凭证应该留在 server、daemon 或 runtime 侧。',
        })}</P>
      </Callout>

      <P>{T({
        en: 'The UI work is not just “visualization.” A useful platform lets humans inspect, search, debug, and manage context: where a memory came from, why it was injected, why a query missed, which agent owns it, and whether sensitive content should be deleted, exported, or re-scoped.',
        zh: 'UI 侧也不只是“可视化”。真正有用的平台能力是 inspect / search / debug / manage：一条 memory 的来源是什么，为什么被注入，为什么没命中，属于哪个 agent，敏感内容是否需要删除、导出或重新划分权限。',
      })}</P>

      <P>{T({
        en: 'A concrete Claude Code version already exists as an OpenViking memory plugin, and a runtime-agnostic path is the OpenViking MCP endpoint.',
        zh: '这条路已经有可落地形态：Claude Code 可以用 OpenViking memory plugin；更通用的运行时可以直接接 OpenViking MCP endpoint。',
      })} <A href={OPENVIKING_CLAUDE_PLUGIN_DOC}>{T({ en: 'Claude Code memory plugin', zh: 'Claude Code memory plugin' })}</A>{T({ en: ' and ', zh: ' 和 ' })}<A href={OPENVIKING_MCP_DOC}>{T({ en: 'OpenViking MCP guide', zh: 'OpenViking MCP 接入指南' })}</A>{T({ en: ' are the reference paths.', zh: ' 是参考路径。' })}</P>

      <Hr ornament />

      <H2 id="recap">{T({ en: 'The Build Order', zh: '构建顺序' })}</H2>

      <Ol>
        <Li><strong>{T({ en: 'Own the process', zh: '接管进程' })}</strong>{T({ en: ' — spawn Claude as a child, talk JSON over stdin/stdout. Save the session ID.', zh: '——以子进程方式启动 Claude，通过 stdin/stdout 传 JSON。保存 session ID。' })}</Li>
        <Li><strong>{T({ en: 'Split server and daemon', zh: '拆分 server 和 daemon' })}</strong>{T({ en: ' — server handles users and routing. Daemon handles the local process. WebSocket in between.', zh: '——server 处理用户和路由，daemon 处理本地进程，中间用 WebSocket 连接。' })}</Li>
        <Li><strong>{T({ en: 'Normalize and inject tools', zh: '归一化并注入工具' })}</strong>{T({ en: ' — translate runtime events into a shared product event shape. Give agents MCP tools to interact with the product.', zh: '——将运行时事件翻译成产品统一事件格式。通过 MCP 给 agent 注入与产品交互的工具。' })}</Li>
        <Li><strong>{T({ en: 'Deliver agent messages', zh: '实现 agent 消息投递' })}</strong>{T({ en: ' — route each new message into the right running, idle, or restarted agent process.', zh: '——每条新消息都按进程状态投递给正确的 agent；只有重启进程时才用保存的 session 接回上下文。' })}</Li>
        <Li><strong>{T({ en: 'Externalize memory', zh: '记忆上云' })}</strong>{T({ en: ' — connect agents to OpenViking as a shared context plane with scoped credentials, retrieval, writeback, audit, and human management UI.', zh: '——把 agent 接到 OpenViking 这层共享 context plane，用 scoped credential 做检索、写回、审计和人工管理。' })}</Li>
      </Ol>

      <P>{T({
        en: 'Once these pieces are in place, "multi-agent" stops being magic. It\'s a scheduler waking named processes that can work, use tools, and remember.',
        zh: '这些部件就位后，"多 agent"就不再神秘。它只是平台把消息投递给具体的 agent 进程，比如 Claude Code 或 Codex；这些进程能工作、能用工具、也能记住上下文。',
      })}</P>

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
        <Li><InlineCode>2a</InlineCode>/<InlineCode>2b</InlineCode>{T({ en: ' — event-shape normalization (zouk protocol)', zh: '——事件格式归一化（zouk 协议）' })}</Li>
        <Li><InlineCode>3a</InlineCode>/<InlineCode>3b</InlineCode>/<InlineCode>3c</InlineCode>{T({ en: ' — two-agent gomoku game with MCP tools', zh: '——双 agent 五子棋对弈 + MCP 工具' })}</Li>
        <Li><InlineCode>4a</InlineCode>/<InlineCode>4b</InlineCode>/<InlineCode>4c</InlineCode>{T({ en: ' — chat platform with agent message delivery', zh: '——聊天平台 + agent 消息投递' })}</Li>
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
      en: 'Four runnable demos plus one OpenViking integration step that turn a CLI agent into a managed daemon with message delivery and shared durable context.',
      zh: '四个可运行示例加一个 OpenViking 集成步骤，将 CLI agent 逐步变成有消息投递和共享长期上下文的托管 daemon。',
    },
    cover: '/assets/covers/runtime.png',
    publishedAt: '2026-05-08',
    updatedAt: '2026-05-20',
    readingTime: 10,
    category: { en: 'Engineering', zh: '工程' },
    tags: ['agent', 'daemon', 'mcp', 'claude-code'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [{ name: 'zayn', github: 'ZaynJarvis' }],
  },
};
