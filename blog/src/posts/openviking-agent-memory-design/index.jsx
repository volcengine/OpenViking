import React from 'react';
import {
  Article, Lead, P, H2, H3, Callout, Hr, Table, A, Strong, InlineCode, Figure,
} from '../../blog-components';

const LLM_PATH = '/post/openviking-agent-memory-design/llm.txt';
const COVER = '/assets/covers/openviking-agent-memory-design.png';
const SOURCE = 'https://juejin.cn/post/7648649574736134196';
const REPO = 'https://github.com/volcengine/OpenViking';
const DOCS = 'https://docs.openviking.ai/';
const MCP_GUIDE = 'https://docs.openviking.ai/en/guides/06-mcp-integration';

function MemoryDesignStyles() {
  return (
    <style>{`
      .ovamd {
        --ovamd-card: color-mix(in oklab, var(--th-bg-2) 88%, transparent);
        --ovamd-tint: color-mix(in oklab, var(--th-accent) 10%, transparent);
        --ovamd-radius: 8px;
      }
      .ovamd *,
      .ovamd *::before,
      .ovamd *::after {
        box-sizing: border-box;
        min-width: 0;
      }
      .ovamd-flow {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 10px;
        margin: 20px 0 28px;
      }
      .ovamd-flow__item {
        border: 1px solid var(--th-line);
        border-radius: var(--ovamd-radius);
        background: var(--ovamd-card);
        padding: 15px;
      }
      .ovamd-flow__label {
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 11px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }
      .ovamd-flow__title {
        margin-top: 8px;
        color: var(--th-ink);
        font-family: var(--th-font-display);
        font-size: 17px;
        font-weight: 700;
        line-height: 1.25;
      }
      .ovamd-flow__body {
        margin-top: 7px;
        color: var(--th-mute);
        font-size: 14px;
        line-height: 1.55;
      }
      .ovamd-split {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        gap: 14px;
        margin: 20px 0 28px;
      }
      .ovamd-card {
        border: 1px solid var(--th-line);
        border-radius: var(--ovamd-radius);
        background: var(--ovamd-card);
        padding: 18px;
      }
      .ovamd-card--accent {
        border-left: 3px solid var(--th-accent);
        background: var(--ovamd-tint);
      }
      .ovamd-card__title {
        margin: 0 0 8px;
        color: var(--th-ink);
        font-family: var(--th-font-display);
        font-size: 18px;
        font-weight: 700;
        line-height: 1.25;
      }
      .ovamd-card__body {
        margin: 0;
        color: var(--th-mute);
        font-size: 15px;
        line-height: 1.6;
      }
      .ovamd-steps {
        counter-reset: ovamd-step;
        display: grid;
        gap: 12px;
        margin: 20px 0 28px;
      }
      .ovamd-step {
        counter-increment: ovamd-step;
        display: grid;
        grid-template-columns: 42px minmax(0, 1fr);
        gap: 14px;
        border-top: 1px solid var(--th-line);
        padding-top: 14px;
      }
      .ovamd-step::before {
        content: counter(ovamd-step, decimal-leading-zero);
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 12px;
        line-height: 1.5;
      }
      .ovamd-step__title {
        color: var(--th-ink);
        font-weight: 700;
      }
      .ovamd-step__body {
        margin-top: 4px;
        color: var(--th-mute);
        font-size: 15px;
        line-height: 1.58;
      }
      .ovamd .b-figure--wide {
        margin-left: min(-7vw, -64px);
        margin-right: min(-7vw, -64px);
      }
      @media (max-width: 840px) {
        .ovamd-flow { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .ovamd-split { grid-template-columns: 1fr; }
        .ovamd .b-figure--wide {
          margin-left: 0;
          margin-right: 0;
        }
      }
      @media (max-width: 520px) {
        .ovamd-flow { grid-template-columns: 1fr; }
      }
    `}</style>
  );
}

function Flow({ t }) {
  const rows = [
    {
      label: t({ en: 'Capture', zh: '提取' }),
      title: t({ en: 'Distill the useful parts', zh: '提炼有效信息' }),
      body: t({ en: 'Design preferences, layout decisions, component rules, and project facts are extracted from ordinary agent dialogue.', zh: '审美偏好、布局决策、组件规则和项目事实，会从普通 Agent 对话中被提炼出来。' }),
    },
    {
      label: t({ en: 'Organize', zh: '组织' }),
      title: t({ en: 'Store by semantic path', zh: '按语义路径存放' }),
      body: t({ en: 'Memories are grouped by meaning instead of being dumped into one transcript-shaped pile.', zh: '记忆按语义类型组织，而不是堆成一份无法检索的聊天记录。' }),
    },
    {
      label: t({ en: 'Recall', zh: '召回' }),
      title: t({ en: 'Route the next request', zh: '路由下一次请求' }),
      body: t({ en: 'The agent first interprets intent, then searches the memory tree where the answer is likely to live.', zh: 'Agent 先判断意图，再进入最可能命中的记忆目录检索。' }),
    },
    {
      label: t({ en: 'Reuse', zh: '复用' }),
      title: t({ en: 'Carry context forward', zh: '把上下文带下去' }),
      body: t({ en: 'New sessions, sub-agents, and different tools can work from the same durable project background.', zh: '新会话、SubAgent 和不同工具，都可以从同一份长期项目背景继续工作。' }),
    },
  ];

  return (
    <div className="ovamd-flow">
      {rows.map(row => (
        <div className="ovamd-flow__item" key={row.label}>
          <div className="ovamd-flow__label">{row.label}</div>
          <div className="ovamd-flow__title">{row.title}</div>
          <div className="ovamd-flow__body">{row.body}</div>
        </div>
      ))}
    </div>
  );
}

function BeforeAfter({ t }) {
  return (
    <div className="ovamd-split">
      <div className="ovamd-card">
        <p className="ovamd-card__title">{t({ en: 'Without a memory layer', zh: '没有记忆层' })}</p>
        <p className="ovamd-card__body">{t({
          en: 'A new window starts from a blank context. Switching from Trae to Codex or Claude Code means repeating design rules, component conventions, and project history.',
          zh: '新窗口从空白上下文开始。Trae、Codex、Claude Code 之间切换时，设计规范、组件约定和项目背景都要重新讲。',
        })}</p>
      </div>
      <div className="ovamd-card ovamd-card--accent">
        <p className="ovamd-card__title">{t({ en: 'With OpenViking', zh: '接入 OpenViking' })}</p>
        <p className="ovamd-card__body">{t({
          en: 'The project keeps a shared context spine. The next agent can retrieve prior colors, layout choices, interaction patterns, and corrections before it writes code.',
          zh: '项目拥有一条共享上下文主干。下一个 Agent 写代码前，就能召回旧的配色、布局选择、交互模式和修正记录。',
        })}</p>
      </div>
    </div>
  );
}

function Stages({ t }) {
  const rows = [
    {
      title: t({ en: 'Collaborate normally', zh: '正常协作' }),
      body: t({ en: 'You keep refining the playground page with the agent. OpenViking extracts stable rules from that process rather than asking you to maintain a separate spec by hand.', zh: '你照常和 Agent 一起打磨 Playground 页面。OpenViking 从这个过程中提取稳定规则，而不是要求你手工维护另一份规范。' }),
    },
    {
      title: t({ en: 'Start a fresh task', zh: '开启新任务' }),
      body: t({ en: 'A prompt such as "recreate the previous playground style" can route to the old design decisions, component rules, and layout preferences.', zh: '一句“复现上个 Playground 的风格”，就能路由到旧的设计决策、组件规则和布局偏好。' }),
    },
    {
      title: t({ en: 'Add a new rule', zh: '追加新规则' }),
      body: t({ en: 'When you correct a detail, the correction becomes part of the durable memory and future pages can follow it without another reminder.', zh: '当你修正一个细节，这条修正会成为长期记忆的一部分，后续页面无需再次提醒也能遵守。' }),
    },
    {
      title: t({ en: 'Let multiple agents share it', zh: '让多个 Agent 共享' }),
      body: t({ en: 'A requirements agent, coding agent, and review agent can work from the same project memory instead of passing brittle summaries through chat.', zh: '需求 Agent、代码 Agent、审查 Agent 可以围绕同一份项目记忆协作，而不是靠脆弱的聊天摘要接力。' }),
    },
  ];

  return (
    <div className="ovamd-steps">
      {rows.map(row => (
        <div className="ovamd-step" key={row.title}>
          <div>
            <div className="ovamd-step__title">{row.title}</div>
            <div className="ovamd-step__body">{row.body}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

const OpenVikingAgentMemoryDesign = ({ t }) => {
  const T = t;

  return (
    <Article className="ovamd">
      <MemoryDesignStyles />

      <Lead>{T({
        en: 'AI coding work breaks down when every agent remembers only the current chat. OpenViking gives Trae, Codex, Claude Code, and sub-agents a shared memory layer so design decisions can survive the next window, the next tool, and the next task.',
        zh: 'AI 编程真正容易断的地方，是每个 Agent 只记得当前聊天。OpenViking 给 Trae、Codex、Claude Code 和 SubAgent 提供一层共享记忆，让设计决策跨过下一个窗口、下一个工具和下一个任务。',
      })}</Lead>

      <Figure
        src={COVER}
        size="wide"
        frame="soft"
        alt={T({ en: 'Abstract OpenViking memory hub connecting several AI agent workspaces', zh: 'OpenViking 记忆中枢连接多个 AI Agent 工作区的抽象图' })}
        caption={T({ en: 'Original generated cover for this OpenViking Blog rewrite.', zh: '本文 OpenViking Blog 改写版的原创生成封面。' })}
      />

      <Callout type="info" title={T({ en: 'Source boundary', zh: '来源边界' })}>
        <P>{T({
          en: <>This post is an OpenViking-style rewrite based on the public Juejin article <A href={SOURCE}>你的 Agent 每次都“失忆”？这个工具彻底治好了我的前端开发焦虑</A>. It preserves the product argument and examples, adds English coverage, and uses original generated imagery instead of source images.</>,
          zh: <>本文是基于掘金公开文章 <A href={SOURCE}>《你的 Agent 每次都“失忆”？这个工具彻底治好了我的前端开发焦虑》</A> 的 OpenViking Blog 风格改写。本文保留产品论点和案例信息，补充英文版本，并使用原创生成图片，不搬运原文图片。</>,
        })}</P>
      </Callout>

      <P dropCap>{T({
        en: 'The failure mode is familiar. You spend a late night teaching an agent the design direction for a frontend page: dark technical background, consistent typography, reusable button patterns, a clear component rhythm. The next morning you open a new session and ask it to continue the dashboard. The agent returns something that looks like a different product.',
        zh: '这个失败模式很常见。你前一晚花很久和 Agent 对齐前端页面方向：深色科技感背景、统一字体、可复用按钮样式、清晰组件节奏。第二天打开新会话，让它继续做数据看板，它却交出一个像另一个产品的页面。',
      })}</P>

      <P>{T({
        en: 'The problem is not that the model never understood you. The problem is that the useful parts of the collaboration stayed trapped inside one session. When you change windows, run out of quota, switch tools, or split work across sub-agents, the context does not automatically travel with the task.',
        zh: '问题不是模型从来没听懂你，而是协作里真正有价值的部分被困在一个会话里。换窗口、额度用完、切换工具、拆给多个 SubAgent 时，上下文并不会自动跟着任务走。',
      })}</P>

      <BeforeAfter t={T} />

      <H2>{T({ en: 'What OpenViking Adds', zh: 'OpenViking 增加了什么' })}</H2>

      <P>{T({
        en: 'OpenViking is the memory center between a user and the AI agents they use. It can be reached through MCP, plugins, CLI flows, and application integrations. Its job is to turn important working context into structured, retrievable memory, then make that memory available when the next agent needs it.',
        zh: 'OpenViking 是用户和各类 AI Agent 之间的记忆中枢。它可以通过 MCP、插件、CLI 流程和应用集成接入。它的任务是把重要工作上下文变成结构化、可召回的记忆，并在下一个 Agent 需要时提供出来。',
      })}</P>

      <Flow t={T} />

      <H2>{T({ en: 'From Design Preference To Memory Tree', zh: '从设计偏好到记忆树' })}</H2>

      <P>{T({
        en: 'Frontend design is a good example because the important information is rarely a single fact. It is a mixture of taste, project constraints, component decisions, and corrections made across several turns. A useful memory layer should capture that shape directly.',
        zh: '前端设计是一个很好的例子，因为重要信息很少只是单个事实。它混合了审美偏好、项目约束、组件决策，以及多轮对话中产生的修正。真正有用的记忆层应该直接承接这种形态。',
      })}</P>

      <Table
        headers={[
          T({ en: 'Memory type', zh: '记忆类型' }),
          T({ en: 'Example', zh: '示例' }),
          T({ en: 'Why it matters later', zh: '后续价值' }),
        ]}
        rows={[
          [
            T({ en: 'Taste and visual direction', zh: '审美与视觉方向' }),
            T({ en: 'Warm off-white background, fine grid texture, black and white primary actions.', zh: '米白底色、细网格纹理、黑白主按钮。' }),
            T({ en: 'Keeps a new page from drifting into a generic admin template.', zh: '避免新页面漂移成通用后台模板。' }),
          ],
          [
            T({ en: 'Component conventions', zh: '组件约定' }),
            T({ en: 'Capsule labels, demo window, floating assistant entry, heavy hero title.', zh: '胶囊标签、演示窗口、悬浮助手入口、超大标题。' }),
            T({ en: 'Lets another agent reuse the same interface vocabulary.', zh: '让另一个 Agent 复用同一套界面语言。' }),
          ],
          [
            T({ en: 'Project decisions', zh: '项目决策' }),
            T({ en: 'Which sections belong in the playground and how they should be ordered.', zh: 'Playground 应包含哪些模块，以及模块顺序。' }),
            T({ en: 'Keeps product structure stable across iterations.', zh: '让产品结构在多次迭代中保持稳定。' }),
          ],
          [
            T({ en: 'Corrections', zh: '修正记录' }),
            T({ en: 'A button color or spacing rule corrected during review.', zh: '评审中修正过的按钮颜色或间距规则。' }),
            T({ en: 'Turns feedback into a future default instead of a repeated reminder.', zh: '把反馈变成后续默认行为，而不是每次重复提醒。' }),
          ],
        ]}
      />

      <P>{T({
        en: 'Recall should also be structured. OpenViking first interprets what the user is asking for, then routes the search into the relevant memory space. A request about recreating a playground style should not search all memories equally; it should lean toward design preferences, component conventions, and prior project decisions.',
        zh: '召回也应该是结构化的。OpenViking 会先理解用户在问什么，再把检索路由到相关记忆空间。一个“复现 Playground 风格”的请求，不应该平等搜索所有记忆，而应该优先进入设计偏好、组件约定和历史项目决策。',
      })}</P>

      <Callout type="note" title={T({ en: 'The important design choice', zh: '关键设计选择' })}>
        <P>{T({
          en: 'Memory retrieval is not a transcript search. It is a scope-selection problem: identify the task, choose the likely directory in the memory tree, retrieve there first, and only expand when evidence is missing.',
          zh: '记忆召回不是聊天记录搜索，而是一个范围选择问题：先识别任务，再选择记忆树里最可能命中的目录，优先在那里检索，证据不足时再扩大范围。',
        })}</P>
      </Callout>

      <H2>{T({ en: 'The Four Practical Wins', zh: '四个实际收益' })}</H2>

      <H3>{T({ en: '1. Personal Design Rules Become Durable', zh: '1. 个人设计规范可长期延续' })}</H3>
      <P>{T({
        en: 'Repeated design decisions stop being disposable chat content. OpenViking can preserve them as reusable context so the next page starts with the right visual system rather than a blank slate.',
        zh: '反复确认过的设计决策不再是一次性聊天内容。OpenViking 可以把它们保存为可复用上下文，让下一个页面从正确视觉体系开始，而不是从白纸开始。',
      })}</P>

      <H3>{T({ en: '2. Sessions Can Continue Each Other', zh: '2. 会话之间可以接续' })}</H3>
      <P>{T({
        en: 'Long frontend tasks often span several sessions. If a bug appears after the design conversation is gone, the repair session still needs the old choices. OpenViking gives the new session a way to retrieve that background instead of asking the user to reconstruct it.',
        zh: '长链路前端任务往往横跨多个会话。设计会话结束后才出现 Bug 时，修复会话仍然需要旧的设计选择。OpenViking 让新会话可以取回这些背景，而不是让用户重新拼装上下文。',
      })}</P>

      <H3>{T({ en: '3. Sub-agents Can Share One Project Memory', zh: '3. SubAgent 可以共享同一份项目记忆' })}</H3>
      <P>{T({
        en: 'A requirements agent, coding agent, and review agent can be useful only if their handoff carries enough context. With OpenViking, the output of one agent can become evidence for the next agent, and feedback can be written back into the project memory.',
        zh: '需求 Agent、代码 Agent、审查 Agent 的价值，取决于交接时是否携带足够上下文。接入 OpenViking 后，前一个 Agent 的输出可以成为后一个 Agent 的依据，后一个 Agent 的反馈也能继续写回项目记忆。',
      })}</P>

      <H3>{T({ en: '4. Different Tools Stop Diverging', zh: '4. 不同工具不再各说各话' })}</H3>
      <P>{T({
        en: 'A team may use Codex for one part, Trae for another, and Claude Code for debugging. Without a shared memory layer, each tool produces its own component habits. With OpenViking, a rule learned in one tool can be recalled by another.',
        zh: '一个团队可能用 Codex 生成一部分页面，用 Trae 补功能，再用 Claude Code 修 Bug。没有共享记忆层时，每个工具都会产出自己的组件习惯。接入 OpenViking 后，一个工具里沉淀的规则，可以被另一个工具召回。',
      })}</P>

      <Hr ornament />

      <H2>{T({ en: 'Playground Case Pattern', zh: 'Playground 案例模式' })}</H2>

      <P>{T({
        en: 'The source article uses a product Playground page to show the workflow. The useful pattern is not tied to that one page. It is a repeatable loop for any long-running AI-assisted frontend project.',
        zh: '原文用一个产品 Playground 页面展示流程。真正有价值的不是单个页面，而是一套可复用的长链路 AI 前端协作循环。',
      })}</P>

      <Stages t={T} />

      <P>{T({
        en: 'The visual difference is exactly what a memory layer should influence. Background treatment, button system, title weight, component vocabulary, and assistant affordances are not magic. They are accumulated decisions. If they are remembered, the next generation has a basis. If they are lost, the model falls back to generic defaults.',
        zh: '视觉差异正是记忆层应该影响的部分。背景处理、按钮体系、标题字重、组件语言和助手入口都不是魔法，而是累积起来的决策。记住它们，下一次生成就有依据；丢掉它们，模型就会回到通用默认值。',
      })}</P>

      <H2>{T({ en: 'Getting Started', zh: '快速开始' })}</H2>

      <P>{T({
        en: <>For local agent tools, the practical path is to run OpenViking, connect the client through MCP or a plugin, and give the agent a rule that tells it when to read or write memory. The repository is on <A href={REPO}>GitHub</A>, the docs live at <A href={DOCS}>docs.openviking.ai</A>, and MCP setup details are in the <A href={MCP_GUIDE}>MCP integration guide</A>.</>,
        zh: <>对本地 Agent 工具来说，实践路径是先运行 OpenViking，再通过 MCP 或插件接入客户端，并给 Agent 一条规则，说明什么时候读取或写入记忆。项目在 <A href={REPO}>GitHub</A>，文档在 <A href={DOCS}>docs.openviking.ai</A>，MCP 细节见 <A href={MCP_GUIDE}>MCP 集成指南</A>。</>,
      })}</P>

      <Table
        headers={[
          T({ en: 'Step', zh: '步骤' }),
          T({ en: 'Action', zh: '动作' }),
        ]}
        rows={[
          [T({ en: 'Run OpenViking', zh: '运行 OpenViking' }), T({ en: 'Start the server and make sure durable storage is configured for memory and resources.', zh: '启动服务，并确保记忆和资源有持久化存储。' })],
          [T({ en: 'Connect the agent', zh: '连接 Agent' }), T({ en: 'Use MCP, a plugin, or CLI integration depending on the client.', zh: '根据客户端选择 MCP、插件或 CLI 集成方式。' })],
          [T({ en: 'Add usage rules', zh: '添加调用规则' }), T({ en: 'Tell the agent to retrieve relevant memory before work and write back durable corrections after meaningful changes.', zh: '要求 Agent 工作前召回相关记忆，并在重要变更后写回可长期复用的修正。' })],
          [T({ en: 'Inspect results', zh: '检查结果' }), T({ en: 'Use OpenViking surfaces to inspect what was stored and whether future retrieval sees the right evidence.', zh: '通过 OpenViking 界面检查写入内容，以及后续召回是否命中正确证据。' })],
        ]}
      />

      <Callout type="tip" title={T({ en: 'Rule of thumb', zh: '经验法则' })}>
        <P>{T({
          en: <>If you expect to tell the same thing to another agent later, it should probably become OpenViking memory. Local files such as <InlineCode>AGENTS.md</InlineCode>, <InlineCode>CLAUDE.md</InlineCode>, or <InlineCode>MEMORY.md</InlineCode> still matter, but they should not be the only place where cross-tool context survives.</>,
          zh: <>如果你预期以后还要把同一件事告诉另一个 Agent，它很可能就应该进入 OpenViking 记忆。本地的 <InlineCode>AGENTS.md</InlineCode>、<InlineCode>CLAUDE.md</InlineCode> 或 <InlineCode>MEMORY.md</InlineCode> 仍然重要，但不应该是跨工具上下文唯一能活下来的地方。</>,
        })}</P>
      </Callout>
    </Article>
  );
};

export default {
  id: 'openviking-agent-memory-design',
  Component: OpenVikingAgentMemoryDesign,
  meta: {
    title: {
      en: 'Stop Teaching Every Agent From Scratch',
      zh: '别再让每个 Agent 从零认识你',
    },
    description: {
      en: 'How OpenViking turns frontend design preferences, project decisions, and agent handoffs into shared long-term memory.',
      zh: 'OpenViking 如何把前端设计偏好、项目决策和 Agent 交接沉淀成共享长期记忆。',
    },
    cover: COVER,
    cardCover: COVER,
    publishedAt: '2026-06-17',
    readingTime: { en: 8, zh: 9 },
    category: { en: 'Agent Memory', zh: 'Agent 记忆' },
    tags: ['openviking', 'memory', 'mcp', 'codex', 'frontend'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    source: SOURCE,
    authors: [{ name: 'OpenViking Team' }],
  },
};
