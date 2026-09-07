import React from 'react';
import {
  Article, Lead, P, H2, A, InlineCode, Quote, Table, Callout, Strong, Figure,
} from '../../blog-components';

const GUIDE = 'https://bytedance.sg.larkoffice.com/docx/ICr3d48hGosGSIxwHUNlWUCRgVf';
const RECORDING = 'https://bytedance.sg.larkoffice.com/minutes/obsgoz7yxk44c5e63gcf85l1';
const COVER = '/assets/covers/openviking-agent-harness.webp';
const IMAGE_BASE = '/post/openviking-agent-harness/images';

const img = name => `${IMAGE_BASE}/${name}`;

const chapters = [
  ['06:30', {
    zh: '安装 OpenViking，配置模型，启动服务并打开 Studio',
    en: 'Install OpenViking, configure models, start the server, and open Studio',
  }],
  ['13:30', {
    zh: '通过统一脚本接入 Claude Code / Codex，检查插件状态',
    en: 'Connect Claude Code and Codex with the shared installer, then check plugin status',
  }],
  ['15:55', {
    zh: '两个 Python 项目的跨工具记忆实验，查看提取与召回过程',
    en: 'Try memory across two Python projects and inspect extraction and recall',
  }],
  ['30:05', {
    zh: '在 MCP 客户端中查找同一份开发偏好',
    en: 'Retrieve the same development preferences through an MCP client',
  }],
  ['37:10', {
    zh: '远程部署、创建用户，以及个人记忆和共享资源的使用',
    en: 'Set up remote access, create users, and work with personal memory and shared resources',
  }],
];

const loopSteps = [
  {
    title: { zh: '你开始说话', en: 'You start talking' },
    body: { zh: '先查有没有相关记忆，有就附在提示词里一起交给模型。', en: 'It checks for related memories and attaches any it finds to the prompt.' },
  },
  {
    title: { zh: '你说完一轮', en: 'You finish a turn' },
    body: { zh: '这一轮的内容自动记下来。', en: 'The exchange is recorded automatically.' },
  },
  {
    title: { zh: '对话结束', en: 'The conversation ends' },
    body: { zh: '归档，后台把有价值的内容提炼成记忆文件。', en: 'It is archived, and useful content is distilled into memory files in the background.' },
  },
  {
    title: { zh: '下次任何工具开口', en: 'Next time, in any tool' },
    body: { zh: '回到第一步，召回的是同一份记忆。', en: 'Back to step one, recalling the same memory.' },
  },
];

function HarnessStyles() {
  return (
    <style>{`
      .ovah *,
      .ovah *::before,
      .ovah *::after {
        box-sizing: border-box;
        min-width: 0;
      }
      /* Let the comparison screenshot grow into the gutter on the right; never past the TOC column. */
      @media (min-width: 841px) {
        .ovah .b-figure--wide { width: min(100% + 240px, 100vw - 64px); }
      }
      @media (min-width: 1100px) {
        .ovah .b-figure--wide { width: min(100% + 240px, min(100vw, 1280px) - 340px); }
      }
      .ovah .b-figure--portrait {
        max-width: 50%;
        margin-left: auto;
        margin-right: auto;
      }
      .ovah-loop {
        margin: 32px 0;
      }
      .ovah-loop__label {
        margin-bottom: 10px;
        color: var(--th-accent);
        font-family: var(--th-font-mono);
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0.18em;
        text-transform: uppercase;
      }
      .ovah-loop__grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 18px;
      }
      .ovah-loop__step {
        position: relative;
        border: 1px solid var(--th-line);
        border-radius: 8px;
        background: color-mix(in oklab, var(--th-bg-2) 88%, transparent);
        padding: 14px;
      }
      .ovah-loop__num {
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 11px;
        letter-spacing: 0.08em;
      }
      .ovah-loop__title {
        margin-top: 6px;
        color: var(--th-ink);
        font-family: var(--th-font-display);
        font-size: 15px;
        font-weight: 700;
        line-height: 1.3;
      }
      .ovah-loop__body {
        margin-top: 6px;
        color: var(--th-mute);
        font-size: 13.5px;
        line-height: 1.55;
      }
      .ovah-loop__cap {
        margin-top: 10px;
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 13px;
      }
      @media (min-width: 841px) {
        .ovah-loop__step:not(:last-child)::after {
          content: '→';
          position: absolute;
          top: 50%;
          right: -16px;
          width: 14px;
          transform: translateY(-50%);
          color: var(--th-mute);
          font-size: 13px;
          line-height: 1;
          text-align: center;
        }
      }
      @media (max-width: 840px) {
        .ovah .b-figure--portrait { max-width: 72%; }
        .ovah-loop__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      }
      @media (max-width: 520px) {
        .ovah .b-figure--portrait { max-width: 100%; }
        .ovah-loop__grid { grid-template-columns: 1fr; }
      }
    `}</style>
  );
}

function MemoryLoop({ t }) {
  return (
    <div className="ovah-loop" role="figure" aria-label={t({ zh: '插件接入后的记忆循环', en: 'The memory loop after the plugin is installed' })}>
      <div className="ovah-loop__label">{t({ zh: '插件接入后的记忆循环', en: 'The memory loop after the plugin is installed' })}</div>
      <div className="ovah-loop__grid">
        {loopSteps.map((step, index) => (
          <div className="ovah-loop__step" key={step.title.en}>
            <div className="ovah-loop__num">{String(index + 1).padStart(2, '0')}</div>
            <div className="ovah-loop__title">{t(step.title)}</div>
            <div className="ovah-loop__body">{t(step.body)}</div>
          </div>
        ))}
      </div>
      <div className="ovah-loop__cap">{t({
        zh: '每一轮对话都在后台自动走一遍这个循环，不依赖模型主动调用工具。',
        en: 'Every turn runs this loop in the background; it does not depend on the model calling a tool.',
      })}</div>
    </div>
  );
}

function SessionLinks({ t }) {
  return (
    <Callout type="tip" title={t({ zh: '完整教程与回放', en: 'Guide and recording' })}>
      <P><A href={GUIDE}><Strong>{t({ zh: '阅读会议文档与常见问题', en: 'Read the guide and FAQ (Chinese)' })}</Strong></A></P>
      <P><A href={RECORDING}><Strong>{t({ zh: '观看会议录屏 · 约一小时', en: 'Watch the recording · about one hour (Chinese)' })}</Strong></A></P>
    </Callout>
  );
}

function OpenVikingAgentHarness({ t }) {
  return (
    <Article className="ovah">
      <HarnessStyles />

      <Lead>{t({
        zh: '在 Claude Code 里交代过的开发习惯，换到 Codex、换一个项目，还要再讲一遍吗？',
        en: 'You have already told Claude Code how you like to work. Do you have to explain it all again when you switch to Codex or start another project?',
      })}</Lead>

      <P>{t({
        zh: '8 月 28 日的 OpenViking × Agent Harness 分享会，我们从本地安装开始，演示了如何把 OpenViking 接入常用的 AI 编程工具，让对话中积累的偏好和经验在不同工具之间复用。完整图文教程和约一小时的演示回放已经整理好，适合还没接入、或想确认记忆是否真正生效的开发者。',
        en: 'In our OpenViking × Agent Harness session on August 28, we started with a local installation and connected OpenViking to everyday AI coding tools, showing how preferences and experience from conversations can carry over between them. The written guide and recording are ready for developers who want to get started or check whether memory is actually working.',
      })}</P>

      <SessionLinks t={t} />

      <H2 id="across-tools">{t({
        zh: '在 Claude Code 里说一次，换到 Codex 接着用',
        en: 'Tell Claude Code once, then pick up in Codex',
      })}</H2>

      <P>{t({
        zh: <>演示中，我们准备了两个用途不同的 Python 项目。在项目 A 中，先告诉 Claude Code 几条个人习惯：行宽 120，测试使用 pytest，测试文件以 <InlineCode>*_spec.py</InlineCode> 命名并放在 <InlineCode>tests/</InlineCode> 下，提交信息使用中文。</>,
        en: <>We used two Python projects with different purposes. In project A, we told Claude Code a few development preferences: a line length of 120, pytest for tests, test files named <InlineCode>*_spec.py</InlineCode> under <InlineCode>tests/</InlineCode>, and commit messages in Chinese.</>,
      })}</P>

      <P>{t({
        zh: '完成任务、归档对话并提取记忆后，切到项目 B，换用 Codex，只说：',
        en: 'After completing the task, archiving the conversation, and extracting its memories, we switched to Codex in project B and asked:',
      })}</P>

      <Quote>{t({
        zh: '给这个 Python 项目加上代码检查和测试的配置，按我的习惯来。',
        en: 'Add linting and test configuration to this Python project, following my usual preferences.',
      })}</Quote>

      <Figure
        src={img('figure-01-codex-with-and-without-openviking.webp')}
        size="wide"
        alt={t({
          zh: 'Codex 在关闭与启用 OpenViking 时生成的 pyproject.toml 对比',
          en: 'Codex output side by side with OpenViking disabled and enabled',
        })}
        caption={t({
          zh: '同一句话交给 Codex。左：临时关闭 OpenViking，行宽 88、测试文件按 test_*.py；右：启用后行宽 120、测试文件 *_spec.py，和之前在 Claude Code 里说的一致。',
          en: 'The same request in Codex. Left: OpenViking temporarily disabled, so line length 88 and test_*.py. Right: OpenViking enabled, so line length 120 and *_spec.py, matching what Claude Code was told earlier.',
        })}
      />

      <P>{t({
        zh: 'Codex 的自动召回记录中出现了刚才的开发偏好，生成的 Ruff 和 pytest 配置也遵循了这些约定。借助 OpenViking Helper，还能追溯本次召回命中了哪个记忆文件，以及具体注入了哪些内容。',
        en: 'The development preferences appeared in Codex’s automatic recall record, and its Ruff and pytest configuration followed them. OpenViking Helper also let us trace which memory file was retrieved and inspect the content injected into the conversation.',
      })}</P>

      <Figure
        src={img('figure-02-helper-preference-memory.webp')}
        alt={t({
          zh: 'OpenViking Helper 中 preferences 目录下的 Python project tooling 记忆文件',
          en: 'The Python project tooling memory file under preferences in OpenViking Helper',
        })}
        caption={t({
          zh: 'OpenViking Helper 里的记忆目录。preferences 下这份文件就是 Codex 召回命中的内容：行宽 120、*_spec.py、中文提交信息。',
          en: 'The memory tree in OpenViking Helper. This file under preferences is what Codex recalled: line length 120, *_spec.py, and Chinese commit messages.',
        })}
      />

      <P>{t({
        zh: '现场也遇到了两个值得留意的细节：对话上传后，还需要归档和后台提取，记忆才会生效；未启用 OpenViking 的 Codex 则曾通过读取相邻项目找到偏好。回放保留了这些过程，展示如何检查记忆文件和召回记录，确认信息的来源。',
        en: 'Two details surfaced during the demo. Uploaded conversations still needed archival and background extraction before their memories became available. Codex without OpenViking also found the preferences by reading a neighboring project. The recording includes both moments and shows how to inspect memory files and recall records to establish where the information came from.',
      })}</P>

      <MemoryLoop t={t} />

      <H2 id="recording-guide">{t({
        zh: '除了跨工具演示，还讲了什么',
        en: 'What else is in the session?',
      })}</H2>

      <P>{t({
        zh: '专用插件通过 Hook 在对话的关键节点自动召回和捕获内容；只接入 MCP（Model Context Protocol）的客户端也能访问同一份记忆，但需要模型或调用方主动使用工具。分享中分别展示了这两种方式，也演示了自建服务的远程连接和多用户使用。',
        en: 'Dedicated integrations use hooks to recall and capture content at key points in a conversation. Clients connected only through MCP (Model Context Protocol) can access the same memory, but the model or calling application must invoke the tools. We demonstrated both approaches, along with remote access and multiple users on a self-hosted server.',
      })}</P>

      <Figure
        src={img('figure-03-mcp-client-recall.webp')}
        size="portrait"
        alt={t({
          zh: 'MCP 客户端中模型逐步查找 OpenViking 工具并读取 Python 开发偏好',
          en: 'An MCP client where the model looks up OpenViking tools step by step and reads the Python development preferences',
        })}
        caption={t({
          zh: '只接入 MCP 的客户端：模型要自己找到 OpenViking 的检索工具，再读出同一份偏好文件。',
          en: 'A client connected only through MCP. The model has to find the OpenViking retrieval tools itself before it reads the same preference file.',
        })}
      />

      <P>{t({
        zh: '可以按下面的时间找到感兴趣的部分：',
        en: 'Use these timestamps to find the parts you want to watch:',
      })}</P>

      <Table
        headers={[t({ zh: '回放时间', en: 'Starts at' }), t({ zh: '内容', en: 'Topic' })]}
        rows={chapters.map(([time, topic]) => [<InlineCode key={time}>{time}</InlineCode>, t(topic)])}
      />

      <Figure
        src={img('figure-04-shared-installer.webp')}
        alt={t({
          zh: '统一安装脚本列出本机检测到的 Claude Code、Codex、Cursor 等工具',
          en: 'The shared installer listing detected tools such as Claude Code, Codex, and Cursor',
        })}
        caption={t({
          zh: '13:30 起的接入环节：统一安装脚本会检测本机已装的编程工具，勾选后一次接入。',
          en: 'From 13:30: the shared installer detects the coding tools on the machine, and you pick which ones to connect.',
        })}
      />

      <P>{t({
        zh: '问答穿插在演示中，涉及长会话何时归档、记忆能否团队共享、如何修正错误记忆，以及怎样临时关闭自动记忆等。安装命令、配置说明和常见问题都收录在配套文档中。',
        en: 'Questions throughout the demo covered when long conversations are archived, how memory can be shared within a team, how to correct a mistaken memory, and how to temporarily turn off automatic memory. The accompanying guide includes installation commands, configuration details, and frequently asked questions.',
      })}</P>

      <H2 id="try-one-preference">{t({
        zh: '从一条开发习惯开始试',
        en: 'Start with one development preference',
      })}</H2>

      <P>{t({
        zh: '第一次体验，可以就用行宽和测试命名：在一个工具里交代，确认记忆提取完成，再换工具、换项目，让它按习惯完成任务。几行配置，就足够观察记忆怎样被保存和再次使用。',
        en: 'For a first try, use line length and test naming. Explain your preferences in one tool, check that memory extraction has finished, then switch tools and projects and ask it to follow your usual conventions. A few lines of configuration are enough to see how memory is saved and used again.',
      })}</P>

      <Figure
        src={img('figure-05-memory-doctor.webp')}
        alt={t({
          zh: '在 Claude Code 里让 OpenViking 插件自检后的汇报',
          en: 'The report after asking the OpenViking plugin to check itself in Claude Code',
        })}
        caption={t({
          zh: '不确定记忆有没有生效，就在 Claude Code 或 Codex 里说一句「帮我检查一下 OpenViking 记忆插件」，它会汇报插件、服务连接和最近的召回、记录情况。',
          en: 'If you are not sure memory is working, ask Claude Code or Codex to check the OpenViking memory plugin. It reports on the plugin, the server connection, and recent recalls and captures.',
        })}
      />

      <P>{t({
        zh: <><A href={GUIDE}><Strong>打开飞书教程，跟着接入 OpenViking</Strong></A>，或先看<A href={RECORDING}><Strong>完整会议录屏</Strong></A>，从 15:55 的跨工具演示开始。</>,
        en: <><A href={GUIDE}><Strong>Follow the guide to connect OpenViking</Strong></A>, or <A href={RECORDING}><Strong>watch the full recording</Strong></A>, starting with the demo across tools at 15:55. Both resources are in Chinese.</>,
      })}</P>
    </Article>
  );
}

export default {
  id: 'openviking-agent-harness',
  Component: OpenVikingAgentHarness,
  meta: {
    title: {
      zh: '让记忆跟着你走：OpenViking × Agent Harness 分享回顾',
      en: 'Memory That Follows You: OpenViking × Agent Harness',
    },
    description: {
      zh: '从 Claude Code 到 Codex，用两个 Python 项目演示跨工具记忆。完整教程、会议回放与时间索引。',
      en: 'A demo of memory across Claude Code, Codex, and two Python projects, with a written guide, session recording, and timestamps.',
    },
    cover: COVER,
    cardCover: '/assets/covers/openviking-agent-harness-card.webp',
    publishedAt: '2026-09-07',
    readingTime: { en: 4, zh: 3 },
    category: { zh: '实践', en: 'Field Notes' },
    tags: ['openviking', 'memory', 'claude-code', 'codex', 'mcp'],
    languages: ['en', 'zh'],
    llmPath: '/post/openviking-agent-harness/llm.txt',
    authors: [{ name: 'OpenViking Team', github: 'volcengine' }],
  },
};
