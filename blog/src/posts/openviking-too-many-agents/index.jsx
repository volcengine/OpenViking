import React from 'react';
import {
  Article, Lead, P, H2, Pre, Callout, Hr,
  Li, Ul, Table, A, Strong, Figure,
} from '../../blog-components';

const LLM_PATH = '/post/openviking-too-many-agents/llm.txt';
const IMAGE_BASE = '/post/openviking-too-many-agents/images';
const COVER = '/assets/covers/openviking-too-many-agents.png';
const OPENVIKING_REPO = 'https://github.com/volcengine/OpenViking';
const OPENVIKING_IMAGE = 'https://github.com/volcengine/OpenViking/pkgs/container/openviking';
const AGENT_INTEGRATIONS = 'https://docs.openviking.ai/en/agent-integrations/01-overview';
const MCP_INTEGRATION = 'https://docs.openviking.ai/en/guides/06-mcp-integration';

function TooManyAgentsStyles() {
  return (
    <style>{`
      .ovta {
        --ovta-radius: 8px;
        --ovta-soft: color-mix(in oklab, var(--th-bg-2) 82%, transparent);
        --ovta-tint: color-mix(in oklab, var(--th-accent) 9%, transparent);
      }
      .ovta *,
      .ovta *::before,
      .ovta *::after {
        box-sizing: border-box;
        min-width: 0;
      }
      .ovta {
        overflow-wrap: anywhere;
      }
      .ovta .b-callout,
      .ovta .b-table-wrap,
      .ovta .b-pre,
      .ovta .b-figure__media {
        max-width: 100%;
      }
      .ovta-kicker {
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 11px;
        letter-spacing: 0.14em;
        line-height: 1.4;
        text-transform: uppercase;
      }
      .ovta-agents {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
        margin: 20px 0 26px;
      }
      .ovta-agent {
        border: 1px solid var(--th-line);
        border-radius: var(--ovta-radius);
        background: var(--ovta-soft);
        padding: 14px;
      }
      .ovta-agent__name {
        color: var(--th-ink);
        font-family: var(--th-font-display);
        font-size: 17px;
        font-weight: 700;
        line-height: 1.2;
      }
      .ovta-agent__state {
        margin-top: 8px;
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 12px;
        line-height: 1.45;
      }
      .ovta-layer-table {
        width: 100%;
      }
      .ovta-layer-cards {
        display: none;
      }
      .ovta-spine {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr);
        gap: 18px;
        margin: 24px 0 30px;
      }
      .ovta-spine__card {
        border: 1px solid var(--th-line);
        border-radius: var(--ovta-radius);
        background: var(--ovta-soft);
        padding: 18px;
      }
      .ovta-spine__card--accent {
        border-left: 3px solid var(--th-accent);
        background: var(--ovta-tint);
      }
      .ovta-spine__title {
        margin: 0 0 8px;
        color: var(--th-ink);
        font-family: var(--th-font-display);
        font-size: 18px;
        font-weight: 700;
        line-height: 1.25;
      }
      .ovta-spine__body {
        margin: 0;
        color: var(--th-mute);
        font-size: 15px;
        line-height: 1.58;
      }
      .ovta-steps {
        counter-reset: ovta-step;
        display: grid;
        gap: 12px;
        margin: 20px 0 26px;
      }
      .ovta-step {
        counter-increment: ovta-step;
        display: grid;
        grid-template-columns: 42px minmax(0, 1fr);
        gap: 14px;
        align-items: start;
        border-top: 1px solid var(--th-line);
        padding-top: 14px;
      }
      .ovta-step::before {
        content: counter(ovta-step, decimal-leading-zero);
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 12px;
        line-height: 1.5;
      }
      .ovta-step__title {
        color: var(--th-ink);
        font-weight: 700;
      }
      .ovta-step__body {
        margin-top: 4px;
        color: var(--th-mute);
        font-size: 15px;
        line-height: 1.55;
      }
      .ovta-proof {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin: 20px 0 28px;
      }
      .ovta-proof__item {
        border: 1px solid var(--th-line);
        border-radius: var(--ovta-radius);
        background: var(--ovta-soft);
        padding: 15px;
      }
      .ovta-proof__label {
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 11px;
        letter-spacing: 0.1em;
        text-transform: uppercase;
      }
      .ovta-proof__text {
        margin-top: 7px;
        font-size: 15px;
        line-height: 1.5;
      }
      .ovta .b-figure--wide {
        margin-left: min(-7vw, -64px);
        margin-right: min(-7vw, -64px);
      }
      @media (max-width: 820px) {
        .ovta-agents { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .ovta-layer-table {
          display: none;
        }
        .ovta-layer-cards {
          display: grid;
          gap: 12px;
          margin: 20px 0 28px;
        }
        .ovta-layer-card {
          border: 1px solid var(--th-line);
          border-radius: var(--ovta-radius);
          background: var(--ovta-soft);
          padding: 15px;
        }
        .ovta-layer-card__title {
          color: var(--th-ink);
          font-family: var(--th-font-display);
          font-size: 18px;
          font-weight: 700;
          line-height: 1.25;
        }
        .ovta-layer-card__label {
          margin-top: 14px;
          color: var(--th-mute);
          font-family: var(--th-font-mono);
          font-size: 11px;
          letter-spacing: 0.1em;
          line-height: 1.35;
          text-transform: uppercase;
        }
        .ovta-layer-card__body {
          margin-top: 5px;
          font-size: 15px;
          line-height: 1.55;
        }
        .ovta-spine,
        .ovta-proof { grid-template-columns: 1fr; }
        .ovta .b-figure--wide {
          margin-left: 0;
          margin-right: 0;
        }
      }
      @media (max-width: 520px) {
        .ovta-agents { grid-template-columns: 1fr; }
      }
    `}</style>
  );
}

function AgentSpread({ t }) {
  const agents = ['Claude Code', 'Codex', 'Hermes Agent', 'Manus', 'Lovable', 'Cursor'];
  return (
    <div className="ovta-agents" aria-label={t({ en: 'Agent ecosystem examples', zh: 'Agent 生态示例' })}>
      {agents.map(name => (
        <div className="ovta-agent" key={name}>
          <div className="ovta-agent__name">{name}</div>
          <div className="ovta-agent__state">{t({ en: 'fresh context unless connected', zh: '接入前各用各的上下文' })}</div>
        </div>
      ))}
    </div>
  );
}

function SharedSpine({ t }) {
  return (
    <div className="ovta-spine">
      <div className="ovta-spine__card">
        <p className="ovta-spine__title">{t({ en: 'Before', zh: '接入前' })}</p>
        <p className="ovta-spine__body">{t({
          en: 'Each agent keeps its own session, tool state, memory habit, and local prompt layer. Switching tools means rebuilding context from zero or pasting summaries by hand.',
          zh: '每个 agent 都有自己的会话、工具状态、记忆习惯和本地提示词规则。切换工具时，常常要重新交代背景，或者手动整理摘要再贴过去。',
        })}</p>
      </div>
      <div className="ovta-spine__card ovta-spine__card--accent">
        <p className="ovta-spine__title">{t({ en: 'After OpenViking', zh: '接入 OpenViking 后' })}</p>
        <p className="ovta-spine__body">{t({
          en: 'Dialogues, docs, code, files, and distilled preferences enter one governed context layer. Agents retrieve the same durable background through plugins, hooks, or MCP.',
          zh: '对话、文档、代码、文件和提炼后的偏好会沉淀到同一层可治理的上下文里。不同 agent 可以通过插件、hooks 或 MCP 读取同一份长期背景。',
        })}</p>
      </div>
    </div>
  );
}

function LayerComparison({ t }) {
  const rows = [
    {
      layer: t({ en: 'Agent harnesses', zh: 'Agent 运行约束' }),
      belongs: t({ en: 'Runtime guidance such as project conventions, command preferences, local tools, and agent-specific rules.', zh: '项目约定、命令偏好、本地工具、agent 专属规则等运行时约束。' }),
      matters: t({ en: 'They steer behavior in one workspace. OpenViking handles durable recall across agents.', zh: '这层约束单个工作区里的行为；跨 agent 的长期记忆交给 OpenViking。' }),
    },
    {
      layer: t({ en: 'OpenViking memory', zh: 'OpenViking 记忆' }),
      belongs: t({ en: 'User preferences, decisions, handoffs, resources, summaries, and reusable lessons.', zh: '用户偏好、决策、交接、资源、摘要和可复用经验。' }),
      matters: t({ en: 'They survive sessions and can be retrieved by multiple agents under governed identity.', zh: '它们跨 session 保留下来，并能在受治理的身份边界内给多个 agent 取用。' }),
    },
    {
      layer: t({ en: 'MCP and plugins', zh: 'MCP 与插件' }),
      belongs: t({ en: 'The connection surface for Claude Code, Codex, Manus, Lovable, Bolt, and other clients.', zh: 'Claude Code、Codex、Manus、Lovable、Bolt 等客户端接入 OpenViking 的接口层。' }),
      matters: t({ en: 'They let agents read and write context without hand-built copy-paste workflows.', zh: '它们让 agent 能直接读写上下文，不再靠人来回复制粘贴。' }),
    },
  ];

  return (
    <>
      <div className="ovta-layer-table">
        <Table
          headers={[
            t({ en: 'Layer', zh: '层' }),
            t({ en: 'What belongs there', zh: '放什么' }),
            t({ en: 'Why it matters', zh: '为什么重要' }),
          ]}
          rows={rows.map(row => [row.layer, row.belongs, row.matters])}
        />
      </div>
      <div className="ovta-layer-cards">
        {rows.map(row => (
          <div className="ovta-layer-card" key={row.layer}>
            <div className="ovta-layer-card__title">{row.layer}</div>
            <div className="ovta-layer-card__label">{t({ en: 'What belongs there', zh: '放什么' })}</div>
            <div className="ovta-layer-card__body">{row.belongs}</div>
            <div className="ovta-layer-card__label">{t({ en: 'Why it matters', zh: '为什么重要' })}</div>
            <div className="ovta-layer-card__body">{row.matters}</div>
          </div>
        ))}
      </div>
    </>
  );
}

function SetupSteps({ t }) {
  const rows = [
    {
      title: t({ en: 'Use the official OpenViking image', zh: '使用 OpenViking 官方镜像' }),
      body: t({
        en: <>Use the <A href={OPENVIKING_IMAGE}>official OpenViking image</A> first; add a custom service layer only if needed.</>,
        zh: <>先用 <A href={OPENVIKING_IMAGE}>OpenViking 官方镜像</A>把服务跑起来，需要时再接自己的服务层。</>,
      }),
    },
    {
      title: t({ en: 'Attach a persistent volume', zh: '挂载持久化 Volume' }),
      body: t({ en: 'Memory and resources are only useful when they survive restarts, deploys, and infrastructure churn.', zh: '记忆和资源只有跨过重启、部署和基础设施变动还在，才算真的可用。' }),
    },
    {
      title: t({ en: 'Initialize the server once', zh: '初始化服务' }),
      body: t({ en: 'Run the init command in the deployment shell, then wire clients through the documented integrations.', zh: '在部署 shell 里初始化一次，然后按官方集成文档接入客户端。' }),
    },
  ];
  return (
    <div className="ovta-steps">
      {rows.map(row => (
        <div className="ovta-step" key={row.title}>
          <div>
            <div className="ovta-step__title">{row.title}</div>
            <div className="ovta-step__body">{row.body}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function ProofStrip({ t }) {
  const rows = [
    {
      label: t({ en: 'Deploy', zh: '部署' }),
      text: t({ en: 'Docker image plus a volume gives the memory plane a stable home.', zh: 'Docker 镜像加 Volume，让记忆层有稳定落点。' }),
    },
    {
      label: t({ en: 'Connect', zh: '连接' }),
      text: t({ en: 'Plugins, hooks, and MCP let different agent loops use the same context surface.', zh: '插件、hooks 和 MCP 让不同 agent 运行链路连接到同一层上下文。' }),
    },
    {
      label: t({ en: 'Inspect', zh: '检查' }),
      text: t({ en: 'Web Studio makes the context filesystem visible on desktop and phone.', zh: 'Web Studio 让团队在桌面和手机上都能查看上下文文件系统。' }),
    },
  ];
  return (
    <div className="ovta-proof">
      {rows.map(row => (
        <div className="ovta-proof__item" key={row.label}>
          <div className="ovta-proof__label">{row.label}</div>
          <div className="ovta-proof__text">{row.text}</div>
        </div>
      ))}
    </div>
  );
}

const OpenVikingTooManyAgents = ({ t }) => {
  const T = t;

  return (
    <Article className="ovta">
      <TooManyAgentsStyles />

      <Lead>{T({
        en: 'The hard part now is keeping context, memory, and project knowledge consistent while work moves between Claude Code, Codex, Hermes Agent, Manus, Lovable, Cursor, and whatever comes next.',
        zh: '现在真正麻烦的是：工作在 Claude Code、Codex、Hermes Agent、Manus、Lovable、Cursor 和下一个工具之间来回切换时，怎么让上下文、记忆和项目知识不散掉。',
      })}</Lead>

      <H2>{T({ en: 'The real cost of too many agents', zh: 'Agent 多起来后的真实成本' })}</H2>

      <P dropCap>{T({
        en: 'Every new agent gives you a different interface and a different strength. The hidden cost is that each one starts with a partial view of the world. A coding agent may know the current repository. A browser agent may know the page. A workflow agent may know its tool call. None of that automatically becomes shared memory.',
        zh: '每多用一个 agent，就多一种入口和一类能力。麻烦在于，它们一开始看到的世界都不完整：代码 agent 知道当前仓库，浏览器 agent 知道当前页面，工作流 agent 知道自己的工具调用，但这些信息不会自动沉淀成共享记忆。',
      })}</P>

      <AgentSpread t={T} />

      <P>{T({
        en: 'That creates a coordination tax. Humans become the copy-paste bus between tools, and agents repeat discovery work that another agent already paid for.',
        zh: '结果是协作成本被转嫁给人：人要在工具之间搬运背景和摘要，agent 也会重复做别的 agent 已经做过的探索。',
      })}</P>

      <H2>{T({ en: 'OpenViking as the shared context layer', zh: '用 OpenViking 做共享上下文层' })}</H2>

      <SharedSpine t={T} />

      <P>{T({
        en: 'OpenViking is useful here because it does not demand that every agent loop become the same product. It can listen quietly, store distilled memory, index resources, and expose the result through interfaces agents already understand.',
        zh: 'OpenViking 的价值在于，不需要把所有 agent 运行链路都改造成同一个产品。它可以在旁边持续沉淀对话里的关键信息、索引资源，再通过 agent 熟悉的接口把上下文拿出来用。',
      })}</P>

      <LayerComparison t={T} />

      <H2>{T({ en: 'A simple deployment path', zh: '部署很简单' })}</H2>

      <P>{T({
        en: 'The reference deployment is intentionally simple: run the official container image, attach durable storage, initialize once, then point agents at the service.',
        zh: '部署非常简单：用官方容器镜像启动，挂上持久化存储，初始化一次，然后让 agent 连接这个服务。',
      })}</P>

      <Figure
        src={`${IMAGE_BASE}/figure-01-railway-deployment.jpg`}
        size="wide"
        frame="plain"
        alt={T({ en: 'Railway deployment screen showing OpenViking running from the official container image with a connected volume.', zh: 'Railway 部署界面，展示 OpenViking 通过官方容器镜像运行并挂载 Volume。' })}
        caption={T({ en: 'A minimal hosted setup: official image, persistent volume, and a public endpoint for clients.', zh: '最小托管形态：官方镜像、持久化 Volume，加一个给客户端访问的公开 endpoint。' })}
      />

      <SetupSteps t={T} />

      <Pre lang="bash" filename="deployment-shell" lineNumbers={false}>{`# Use the official OpenViking image:
ghcr.io/volcengine/openviking:latest

# Initialize the service once after storage is attached:
openviking-server init`}</Pre>

      <P>{T({
        en: 'After that, use the plugin or hook path for coding agents, and use MCP when the client speaks MCP directly.',
        zh: '之后，代码 agent 走插件或 hook；原生支持 MCP 的客户端直接走 MCP。',
      })}</P>

      <Ul>
        <Li><A href={AGENT_INTEGRATIONS}>{T({ en: 'Agent integration guide', zh: 'Agent 集成指南' })}</A></Li>
        <Li><A href={MCP_INTEGRATION}>{T({ en: 'MCP integration guide', zh: 'MCP 集成指南' })}</A></Li>
        <Li><A href={OPENVIKING_IMAGE}>{T({ en: 'Official OpenViking container image', zh: 'OpenViking 官方容器镜像' })}</A></Li>
      </Ul>

      <H2>{T({ en: 'Make the memory visible', zh: '让记忆可见' })}</H2>

      <P>{T({
        en: 'A shared context layer should not be a black box. Web Studio is bundled with the Docker image so teams can inspect the OpenViking filesystem, upload resources, review processing tasks, and check what memory has actually been captured.',
        zh: '共享上下文层不能是黑盒。Web Studio 已经打进 Docker 镜像，团队可以查看 OpenViking 文件系统、上传资源、检查处理任务，也能确认系统到底记住了什么。',
      })}</P>

      <Figure
        src={`${IMAGE_BASE}/figure-02-web-studio-desktop.jpg`}
        size="wide"
        frame="plain"
        alt={T({ en: 'OpenViking Studio desktop view showing the viking resource tree and an L0/L1 overview panel.', zh: 'OpenViking Studio 桌面视图，展示 viking 资源树和 L0/L1 概览面板。' })}
        caption={T({ en: 'Desktop Web Studio turns the context database into a browsable filesystem: resource trees, abstracts, overviews, and evidence stay inspectable.', zh: '桌面 Web Studio 把上下文数据库变成可浏览的文件系统：资源树、abstract、overview 和证据都能查。' })}
      />

      <Figure
        src={`${IMAGE_BASE}/figure-03-web-studio-pwa.jpg`}
        size="sm"
        frame="plain"
        alt={T({ en: 'OpenViking Studio PWA on a phone showing a session history tree and a messages.jsonl reader.', zh: '手机上的 OpenViking Studio PWA，展示 session history 树和 messages.jsonl 阅读器。' })}
        caption={T({ en: 'The same inspection path works as a PWA, which matters when memory debugging happens away from the desktop.', zh: '同一套查看路径也可以作为 PWA 在手机上用；排查记忆问题不一定总在电脑前。' })}
      />

      <ProofStrip t={T} />

      <H2>{T({ en: 'The production lesson', zh: '生产环境里的经验' })}</H2>

      <P>{T({
        en: 'The useful mental model is simple: agent harnesses steer behavior; OpenViking stores durable context; MCP and plugins make that context reachable. Keep those responsibilities separate and the system stays explainable.',
        zh: '心智模型可以很简单：Agent 运行约束管行为，OpenViking 保存长期上下文，MCP 和插件负责把上下文接到 agent 手里。职责分清，系统才好解释、好排查。',
      })}</P>

      <Table
        headers={[
          T({ en: 'Practice', zh: '实践' }),
          T({ en: 'Operational rule', zh: '操作规则' }),
        ]}
        rows={[
          [
            T({ en: 'Persist the context plane', zh: '先让上下文持久化' }),
            T({ en: 'Attach storage before trusting memory. A stateless context service is a demo, not a memory system.', zh: '先挂存储，再谈记忆。无状态上下文服务只能演示，不能当记忆系统。' }),
          ],
          [
            T({ en: 'Give agents scoped identities', zh: '给 agent 清晰的身份边界' }),
            T({ en: 'Shared users are convenient; distinct users are better when recall, ownership, and audit boundaries matter.', zh: '早期共用一个用户最省事；一旦涉及召回、归属和审计，就应该拆成独立用户。' }),
          ],
          [
            T({ en: 'Expose evidence, not only summaries', zh: '保留证据，不只给摘要' }),
            T({ en: 'L0/L1 summaries help routing, and original resources or session files can still be used when precision matters.', zh: 'L0/L1 摘要适合做路由；需要精确判断时，还要能回到原始资源和 session 文件。' }),
          ],
        ]}
      />

      <Hr ornament />

      <H2>{T({ en: 'Try it', zh: '试一下' })}</H2>

      <P>{T({
        en: 'If your work already crosses multiple agents, OpenViking is most valuable when you install it before the next context handoff. Put the repository, docs, and prior session memory into the same layer, then let each agent retrieve only what it needs.',
        zh: '如果你的工作已经在多个 agent 之间流转，最好在下一次交接前就把 OpenViking 接上。把仓库、文档和历史 session 记忆放到同一层，让每个 agent 只取自己需要的部分。',
      })}</P>

      <Ul>
        <Li><Strong>{T({ en: 'Repository:', zh: '仓库：' })}</Strong> <A href={OPENVIKING_REPO}>github.com/volcengine/OpenViking</A></Li>
        <Li><Strong>{T({ en: 'Agent docs:', zh: 'Agent 文档：' })}</Strong> <A href={AGENT_INTEGRATIONS}>docs.openviking.ai/en/agent-integrations/01-overview</A></Li>
        <Li><Strong>{T({ en: 'MCP docs:', zh: 'MCP 文档：' })}</Strong> <A href={MCP_INTEGRATION}>docs.openviking.ai/en/guides/06-mcp-integration</A></Li>
      </Ul>

      <Callout type="tip" title={T({ en: 'Rule of thumb', zh: '经验法则' })}>
        <P>{T({
          en: 'When an agent produces context that another agent will need later, write it to OpenViking instead of leaving it trapped in a chat transcript.',
          zh: '当一个 agent 产出的上下文以后还会被其他 agent 用到，就写进 OpenViking，不要只留在聊天记录里。',
        })}</P>
      </Callout>
    </Article>
  );
};

export default {
  id: 'openviking-too-many-agents',
  Component: OpenVikingTooManyAgents,
  meta: {
    title: {
      en: 'OpenViking for the Too Many Agents Problem',
      zh: 'OpenViking 如何解决 Agent 太多的问题',
    },
    description: {
      en: 'A practical OpenViking deployment and Web Studio walkthrough for sharing context across Claude Code, Codex, Hermes Agent, Manus, Lovable, Cursor, and MCP clients.',
      zh: '一次 OpenViking 部署和 Web Studio 实践：把上下文共享给 Claude Code、Codex、Hermes Agent、Manus、Lovable、Cursor 和 MCP 客户端。',
    },
    cover: COVER,
    cardCover: COVER,
    publishedAt: '2026-05-22',
    readingTime: { en: 7, zh: 8 },
    category: { en: 'Use Case', zh: '实践' },
    tags: ['openviking', 'memory', 'mcp', 'web-studio'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [{ name: 'zayn', github: 'ZaynJarvis' }],
  },
};
