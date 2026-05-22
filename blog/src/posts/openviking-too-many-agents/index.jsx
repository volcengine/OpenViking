import React from 'react';
import {
  Article, Lead, P, H2, Pre, Callout, Hr,
  Li, Ul, Table, A, Strong, Figure,
} from '../../blog-components';

const LLM_PATH = '/post/openviking-too-many-agents/llm.txt';
const IMAGE_BASE = '/post/openviking-too-many-agents/images';
const OPENVIKING_REPO = 'https://github.com/volcengine/OpenViking';
const OPENVIKING_IMAGE = 'https://github.com/volcengine/OpenViking/pkgs/container/openviking';
const AGENT_INTEGRATIONS = 'https://docs.openviking.ai/en/agent-integrations/01-overview';
const MCP_INTEGRATION = 'https://docs.openviking.ai/en/guides/06-mcp-integration';
const SOURCE_POST = 'https://x.com/ZaynJarvis/status/2057680967075324365';

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
        grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
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
        .ovta-spine,
        .ovta-proof { grid-template-columns: 1fr; }
        .ovta .b-figure--wide {
          margin-left: 0;
          margin-right: 0;
        }
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
          <div className="ovta-agent__state">{t({ en: 'fresh context unless connected', zh: '不连接就重新建上下文' })}</div>
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
          zh: '每个 agent 都有自己的 session、工具状态、记忆习惯和本地 prompt 层。切换工具时，要么从零重建上下文，要么靠人手动粘摘要。',
        })}</p>
      </div>
      <div className="ovta-spine__card ovta-spine__card--accent">
        <p className="ovta-spine__title">{t({ en: 'After OpenViking', zh: '接入 OpenViking 后' })}</p>
        <p className="ovta-spine__body">{t({
          en: 'Dialogues, docs, code, files, and distilled preferences enter one governed context layer. Agents retrieve the same durable background through plugins, hooks, or MCP.',
          zh: '对话、文档、代码、文件和提炼后的偏好进入同一层可治理上下文。不同 agent 通过插件、hooks 或 MCP 读取同一份长期背景。',
        })}</p>
      </div>
    </div>
  );
}

function SetupSteps({ t }) {
  const rows = [
    {
      title: t({ en: 'Run the official container image', zh: '运行官方容器镜像' }),
      body: t({ en: 'Start from the published OpenViking image instead of building a custom service wrapper first.', zh: '先从已发布的 OpenViking 镜像开始，不必先写一层自定义服务包装。' }),
    },
    {
      title: t({ en: 'Attach a persistent volume', zh: '挂载持久化 Volume' }),
      body: t({ en: 'Memory and resources are only useful when they survive restarts, deploys, and infrastructure churn.', zh: '记忆和资源必须跨重启、部署和基础设施变动继续存在，才真正有价值。' }),
    },
    {
      title: t({ en: 'Initialize the server once', zh: '初始化服务' }),
      body: t({ en: 'Run the init command in the deployment shell, then wire clients through the documented integrations.', zh: '在部署 shell 中执行初始化命令，然后按官方集成文档把客户端接进来。' }),
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
      text: t({ en: 'Docker image plus a volume gives the memory plane a stable home.', zh: 'Docker 镜像加 Volume，让 memory plane 有稳定落点。' }),
    },
    {
      label: t({ en: 'Connect', zh: '连接' }),
      text: t({ en: 'Plugins, hooks, and MCP let different agent loops use the same context surface.', zh: '插件、hooks 和 MCP 让不同 agent loop 接入同一个上下文界面。' }),
    },
    {
      label: t({ en: 'Inspect', zh: '检查' }),
      text: t({ en: 'Web Studio makes the context filesystem visible on desktop and phone.', zh: 'Web Studio 让上下文文件系统在桌面和手机上都可检查。' }),
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
        en: 'The agent problem has shifted. It is no longer hard to find an agent. It is hard to keep context, memory, and project knowledge consistent when work moves between Claude Code, Codex, Hermes Agent, Manus, Lovable, Cursor, and whatever comes next.',
        zh: 'Agent 问题已经变了。难点不再是找到一个 agent，而是在工作跨 Claude Code、Codex、Hermes Agent、Manus、Lovable、Cursor 以及下一个工具流转时，保持上下文、记忆和项目知识一致。',
      })}</Lead>

      <Callout type="info" title={T({ en: 'Source boundary', zh: '来源边界' })}>
        <P>{T({
          en: 'This article is based on a public post by Zayn Jarvis, public OpenViking documentation, and the public repository. Local authoring notes shaped the structure only; private URLs, localhost links, keys, and internal review traces are not part of the published artifact.',
          zh: '本文基于 Zayn Jarvis 的公开动态、OpenViking 公开文档和公开仓库。本地写作笔记只影响结构；私有 URL、localhost 链接、密钥和内部 review 痕迹都不进入发布内容。',
        })}</P>
      </Callout>

      <H2>{T({ en: 'The real cost of too many agents', zh: 'Agent 太多的真实成本' })}</H2>

      <P dropCap>{T({
        en: 'Every new agent gives you a different interface and a different strength. The hidden cost is that each one starts with a partial view of the world. A coding agent may know the current repository. A browser agent may know the page. A workflow agent may know its tool call. None of that automatically becomes shared memory.',
        zh: '每出现一个新 agent，就多一种界面和一类能力。隐藏成本是：每个 agent 都只带着局部世界观开始工作。代码 agent 可能知道当前仓库，浏览器 agent 可能知道当前页面，工作流 agent 可能知道自己的工具调用。但这些不会自动变成共享记忆。',
      })}</P>

      <AgentSpread t={T} />

      <P>{T({
        en: 'That creates a coordination tax. Humans become the copy-paste bus between tools, and agents repeat discovery work that another agent already paid for.',
        zh: '于是出现了协作税。人变成工具之间的复制粘贴总线，agent 也会重复另一位 agent 已经做过的探索。',
      })}</P>

      <H2>{T({ en: 'OpenViking as the shared context layer', zh: 'OpenViking 作为共享上下文层' })}</H2>

      <SharedSpine t={T} />

      <P>{T({
        en: 'OpenViking is useful here because it does not demand that every agent loop become the same product. It can listen quietly, store distilled memory, index resources, and expose the result through interfaces agents already understand.',
        zh: 'OpenViking 的价值在于，它不要求每个 agent loop 变成同一个产品。它可以安静地监听、写入提炼后的记忆、索引资源，再通过 agent 已经能理解的接口把结果暴露出来。',
      })}</P>

      <Table
        headers={[
          T({ en: 'Layer', zh: '层' }),
          T({ en: 'What belongs there', zh: '放什么' }),
          T({ en: 'Why it matters', zh: '为什么重要' }),
        ]}
        rows={[
          [
            T({ en: 'Workspace-local instructions', zh: '工作区本地指令' }),
            T({ en: 'Rules such as project conventions, command preferences, or local agent guidance.', zh: '项目约定、命令偏好、本地 agent 指令等规则。' }),
            T({ en: 'They shape behavior in one workspace, but do not automatically solve cross-agent recall.', zh: '它们塑造单个工作区里的行为，但不会自动解决跨 agent 召回。' }),
          ],
          [
            T({ en: 'OpenViking memory', zh: 'OpenViking 记忆' }),
            T({ en: 'User preferences, decisions, handoffs, resources, summaries, and reusable lessons.', zh: '用户偏好、决策、交接、资源、摘要和可复用经验。' }),
            T({ en: 'They survive sessions and can be retrieved by multiple agents under governed identity.', zh: '它们跨 session 存活，并能在受治理身份下被多个 agent 检索。' }),
          ],
          [
            T({ en: 'MCP and plugins', zh: 'MCP 与插件' }),
            T({ en: 'The connection surface for Claude Code, Codex, Manus, Lovable, Bolt, and other clients.', zh: 'Claude Code、Codex、Manus、Lovable、Bolt 等客户端的连接面。' }),
            T({ en: 'They let agents read and write context without hand-built copy-paste workflows.', zh: '它们让 agent 不靠手写复制粘贴流程也能读写上下文。' }),
          ],
        ]}
      />

      <H2>{T({ en: 'A simple deployment path', zh: '一个简单部署路径' })}</H2>

      <P>{T({
        en: 'The reference deployment is intentionally simple: run the official container image, attach durable storage, initialize once, then point agents at the service.',
        zh: '参考部署故意保持简单：运行官方容器镜像，挂持久化存储，初始化一次，然后把 agent 指向这个服务。',
      })}</P>

      <Figure
        src={`${IMAGE_BASE}/figure-01-railway-deployment.jpg`}
        size="wide"
        frame="plain"
        alt={T({ en: 'Railway deployment screen showing OpenViking running from the official container image with a connected volume.', zh: 'Railway 部署界面，展示 OpenViking 通过官方容器镜像运行并挂载 Volume。' })}
        caption={T({ en: 'A minimal hosted setup: official image, persistent volume, and a public endpoint for clients.', zh: '最小托管形态：官方镜像、持久化 Volume，以及给客户端使用的公开 endpoint。' })}
        credit={T({ en: 'Source image adapted from Zayn Jarvis public post.', zh: '源图改编自 Zayn Jarvis 公开动态。' })}
      />

      <SetupSteps t={T} />

      <Pre lang="bash" filename="deployment-shell" lineNumbers={false}>{`# Start from the official image:
ghcr.io/volcengine/openviking:main

# Initialize the service once after storage is attached:
openviking-server init`}</Pre>

      <P>{T({
        en: 'After that, agents can follow the integration docs instead of reverse-engineering the service. Use the plugin or hook path for coding agents, and use MCP when the client speaks MCP directly.',
        zh: '之后，agent 按集成文档接入即可，不需要反向摸索服务。代码 agent 走插件或 hook 路径；原生支持 MCP 的客户端直接走 MCP。',
      })}</P>

      <Ul>
        <Li><A href={AGENT_INTEGRATIONS}>{T({ en: 'Agent integration guide', zh: 'Agent 集成指南' })}</A></Li>
        <Li><A href={MCP_INTEGRATION}>{T({ en: 'MCP integration guide', zh: 'MCP 集成指南' })}</A></Li>
        <Li><A href={OPENVIKING_IMAGE}>{T({ en: 'Official OpenViking container image', zh: 'OpenViking 官方容器镜像' })}</A></Li>
      </Ul>

      <H2>{T({ en: 'Make the memory visible', zh: '让记忆可见' })}</H2>

      <P>{T({
        en: 'A shared context layer should not be a black box. Web Studio is bundled with the Docker image so teams can inspect the OpenViking filesystem, upload resources, review processing tasks, and check what memory has actually been captured.',
        zh: '共享上下文层不应该是黑盒。Web Studio 已打包在 Docker 镜像里，团队可以检查 OpenViking 文件系统、上传资源、查看处理任务，并确认到底捕获了哪些记忆。',
      })}</P>

      <Figure
        src={`${IMAGE_BASE}/figure-02-web-studio-desktop.jpg`}
        size="wide"
        frame="plain"
        alt={T({ en: 'OpenViking Studio desktop view showing the viking resource tree and an L0/L1 overview panel.', zh: 'OpenViking Studio 桌面视图，展示 viking 资源树和 L0/L1 概览面板。' })}
        caption={T({ en: 'Desktop Web Studio turns the context database into a browsable filesystem: resource trees, abstracts, overviews, and evidence stay inspectable.', zh: '桌面 Web Studio 把上下文数据库变成可浏览文件系统：资源树、abstract、overview 和证据都能被检查。' })}
        credit={T({ en: 'Source image adapted from Zayn Jarvis public post.', zh: '源图改编自 Zayn Jarvis 公开动态。' })}
      />

      <Figure
        src={`${IMAGE_BASE}/figure-03-web-studio-pwa.jpg`}
        size="sm"
        frame="plain"
        alt={T({ en: 'OpenViking Studio PWA on a phone showing a session history tree and a messages.jsonl reader.', zh: '手机上的 OpenViking Studio PWA，展示 session history 树和 messages.jsonl 阅读器。' })}
        caption={T({ en: 'The same inspection path works as a PWA, which matters when memory debugging happens away from the desktop.', zh: '同一套检查路径也能以 PWA 运行；当记忆排查不在桌面前发生时，这一点很重要。' })}
        credit={T({ en: 'Source image adapted from Zayn Jarvis public post.', zh: '源图改编自 Zayn Jarvis 公开动态。' })}
      />

      <ProofStrip t={T} />

      <H2>{T({ en: 'The production lesson', zh: '生产环境里的经验' })}</H2>

      <P>{T({
        en: 'The useful mental model is simple: local instruction files steer behavior; OpenViking stores durable context; MCP and plugins make that context reachable. Keep those responsibilities separate and the system stays explainable.',
        zh: '有用的心智模型很简单：本地指令文件负责约束行为；OpenViking 负责保存长期上下文；MCP 和插件负责让上下文可达。把这些职责分清，系统才容易解释。',
      })}</P>

      <Table
        headers={[
          T({ en: 'Practice', zh: '实践' }),
          T({ en: 'Operational rule', zh: '操作规则' }),
        ]}
        rows={[
          [
            T({ en: 'Persist the context plane', zh: '持久化上下文层' }),
            T({ en: 'Attach storage before trusting memory. A stateless context service is a demo, not a memory system.', zh: '先挂存储，再信任记忆。无状态上下文服务只是 demo，不是记忆系统。' }),
          ],
          [
            T({ en: 'Give agents scoped identities', zh: '给 agent 有边界的身份' }),
            T({ en: 'Shared users are convenient; distinct users are better when recall, ownership, and audit boundaries matter.', zh: '共享用户很方便；当召回、归属和审计边界重要时，独立用户更好。' }),
          ],
          [
            T({ en: 'Expose evidence, not only summaries', zh: '暴露证据，不只暴露摘要' }),
            T({ en: 'L0/L1 summaries help routing, but the original resource and session files must remain readable when precision matters.', zh: 'L0/L1 摘要适合路由，但需要精确判断时，原始资源和 session 文件必须仍可读取。' }),
          ],
          [
            T({ en: 'Keep agent-readable pages clean', zh: '保持 agent 可读页面干净' }),
            T({ en: 'The human article can be visual; the agent version should be concise, public, and free of private authoring traces.', zh: '人读文章可以有视觉表达；agent 版本要简洁、公开，并且没有私有写作痕迹。' }),
          ],
        ]}
      />

      <Hr ornament />

      <H2>{T({ en: 'Try it', zh: '试一下' })}</H2>

      <P>{T({
        en: 'If your work already crosses multiple agents, OpenViking is most valuable when you install it before the next context handoff. Put the repository, docs, and prior session memory into the same layer, then let each agent retrieve only what it needs.',
        zh: '如果你的工作已经跨多个 agent，OpenViking 最好在下一次上下文交接前就装好。把仓库、文档和历史 session 记忆放进同一层，再让每个 agent 只检索自己需要的部分。',
      })}</P>

      <Ul>
        <Li><Strong>{T({ en: 'Repository:', zh: '仓库：' })}</Strong> <A href={OPENVIKING_REPO}>github.com/volcengine/OpenViking</A></Li>
        <Li><Strong>{T({ en: 'Agent docs:', zh: 'Agent 文档：' })}</Strong> <A href={AGENT_INTEGRATIONS}>docs.openviking.ai/en/agent-integrations/01-overview</A></Li>
        <Li><Strong>{T({ en: 'MCP docs:', zh: 'MCP 文档：' })}</Strong> <A href={MCP_INTEGRATION}>docs.openviking.ai/en/guides/06-mcp-integration</A></Li>
        <Li><Strong>{T({ en: 'Source post:', zh: '来源动态：' })}</Strong> <A href={SOURCE_POST}>x.com/ZaynJarvis/status/2057680967075324365</A></Li>
      </Ul>

      <Callout type="tip" title={T({ en: 'Rule of thumb', zh: '经验法则' })}>
        <P>{T({
          en: 'When an agent produces context that another agent will need later, write it to OpenViking instead of leaving it trapped in a chat transcript.',
          zh: '当一个 agent 产出的上下文之后会被另一个 agent 用到，就把它写进 OpenViking，不要让它困在聊天记录里。',
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
      zh: '一次 OpenViking 部署与 Web Studio 实践：把上下文共享给 Claude Code、Codex、Hermes Agent、Manus、Lovable、Cursor 和 MCP 客户端。',
    },
    cover: `${IMAGE_BASE}/figure-02-web-studio-desktop.jpg`,
    cardCover: `${IMAGE_BASE}/figure-01-railway-deployment.jpg`,
    publishedAt: '2026-05-22',
    readingTime: { en: 7, zh: 8 },
    category: { en: 'Use Case', zh: '实践' },
    tags: ['openviking', 'memory', 'mcp', 'web-studio'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [{ name: 'zayn', github: 'ZaynJarvis', role: { en: 'Engineer', zh: '工程师' } }],
  },
};
