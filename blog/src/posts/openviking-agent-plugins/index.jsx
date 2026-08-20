import React from 'react';
import {
  Article, Lead, P, H2, Pre, A, InlineCode, Strong,
} from '../../blog-components';

const COVER = '/assets/covers/openviking-agent-plugins.png';
const LLM_PATH = '/post/openviking-agent-plugins/llm.txt';

const SPEC_URL = 'https://agent-plugins.org/specification';
const AAIF_POST = 'https://aaif.io/blog/from-skills-and-tools-to-portable-agent-plugins';
const PLUGIN_DIR = 'https://github.com/volcengine/OpenViking?utm_source=blog&utm_medium=article&utm_campaign=openviking-agent-plugins/tree/main/agent-plugins';
const INTEGRATIONS_EN = 'https://docs.openviking.ai/en/agent-integrations/01-overview';
const INTEGRATIONS_ZH = 'https://docs.openviking.ai/zh/agent-integrations/01-overview';

function CapabilityCard({ label, title, children }) {
  return (
    <div style={{
      padding: '16px 18px',
      borderRadius: 10,
      border: '1px solid var(--th-line)',
      background: 'var(--th-surface, var(--th-bg))',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <span style={{
          display: 'inline-block', padding: '2px 8px', borderRadius: 4,
          background: 'var(--th-accent)', color: 'var(--th-bg)',
          fontFamily: 'var(--th-font-mono)', fontSize: 11, fontWeight: 600,
        }}>{label}</span>
        <span style={{ fontFamily: 'var(--th-font-display)', fontWeight: 600, fontSize: 15 }}>{title}</span>
      </div>
      <div style={{ color: 'var(--th-mute)', fontSize: 14, lineHeight: 1.65 }}>{children}</div>
    </div>
  );
}

const OpenVikingAgentPlugins = ({ t }) => (
  <Article>
    <Lead>
      {t({
        en: 'OpenViking now ships as an Agent Plugins 1.0 package: one directory, installed the same way in ChatGPT, Codex, Cursor, GitHub Copilot, Kiro, and VS Code, giving each of them long-term memory.',
        zh: 'OpenViking 现在提供 Agent Plugins 1.0 安装包：一个目录，在 ChatGPT、Codex、Cursor、GitHub Copilot、Kiro、VS Code 里用同一种方式安装，让它们都拥有长期记忆。',
      })}
    </Lead>

    <P>
      {t({
        en: <>Until recently, equipping an AI coding agent with long-term memory via OpenViking meant installing a client-specific plugin — one for Claude Code, another for Codex, yet another for Cursor. They all did the same fundamental job, but each had to be packaged, installed, and maintained separately.</>,
        zh: <>此前，想让一个 AI 编程 Agent 通过 OpenViking 获得长期记忆，需要装对应客户端的专属插件：Claude Code 一个、Codex 一个、Cursor 又一个。它们做的是同一件事，却要各自打包、各自安装、各自维护。</>,
      })}
    </P>

    <P>
      {t({
        en: <><A href={SPEC_URL}>Agent Plugins 1.0</A> changes the distribution side of this. It is an open packaging standard from the Agentic AI Foundation (backed by Amazon, Cursor, Google, Microsoft, OpenAI, Vercel, and others). A plugin is just a plain directory: a <InlineCode>plugin.json</InlineCode> manifest, Agent Skills under <InlineCode>skills/</InlineCode>, and MCP server declarations in <InlineCode>mcp.json</InlineCode>. Any compatible client discovers and loads it the same way. We won't rehash the format here — the <A href={SPEC_URL}>specification</A> and the <A href={AAIF_POST}>AAIF announcement</A> cover it well.</>,
        zh: <><A href={SPEC_URL}>Agent Plugins 1.0</A> 改变的是分发这一侧。它是 Agentic AI Foundation 推出的开放打包标准（Amazon、Cursor、Google、Microsoft、OpenAI、Vercel 等都在背后支持）。一个插件就是一个普通目录：<InlineCode>plugin.json</InlineCode> 清单、放在 <InlineCode>skills/</InlineCode> 下的 Agent Skills、写在 <InlineCode>mcp.json</InlineCode> 里的 MCP server 声明。所有兼容客户端用同一种方式发现并加载它。格式细节这里不展开，<A href={SPEC_URL}>官方规范</A>和 <A href={AAIF_POST}>AAIF 的发布文章</A>已经讲得很清楚。</>,
      })}
    </P>

    <P>
      {t({
        en: <><Strong>OpenViking now ships an Agent Plugins 1.0 package.</Strong> You can find it under <A href={PLUGIN_DIR}><InlineCode>agent-plugins/</InlineCode></A> in our repository:</>,
        zh: <><Strong>OpenViking 现已提供 Agent Plugins 1.0 安装包。</Strong>它位于仓库的 <A href={PLUGIN_DIR}><InlineCode>agent-plugins/</InlineCode></A> 目录：</>,
      })}
    </P>

    <Pre lang="text">{`agent-plugins/
  plugin.json          # manifest
  skills/
    openviking-memory/ # when to recall, when to remember
  mcp.json             # OpenViking MCP toolset (stdio proxy)`}</Pre>

    <P>
      {t({
        en: 'A single installation equips any supported environment with two capabilities:',
        zh: '装一次，任何受支持的环境都会获得两项能力：',
      })}
    </P>

    <div style={{ display: 'grid', gap: 12 }}>
      <CapabilityCard
        label="skill"
        title={t({ en: 'The openviking-memory skill', zh: 'openviking-memory skill' })}
      >
        {t({
          en: <>Process knowledge that stays resident in the model's context and teaches the agent <Strong>when</Strong> to act: recall relevant memories with <InlineCode>find</InlineCode>/<InlineCode>search</InlineCode> at the start of a substantial task, and persist durable facts with <InlineCode>remember</InlineCode>/<InlineCode>write</InlineCode> when key decisions, user preferences, or hard-won root causes emerge.</>,
          zh: <>常驻在模型上下文里的过程性知识，教会 Agent <Strong>什么时候</Strong>该行动：在开始一项重要任务前用 <InlineCode>find</InlineCode>/<InlineCode>search</InlineCode> 召回相关记忆；出现关键决策、用户偏好或来之不易的问题根因时，用 <InlineCode>remember</InlineCode>/<InlineCode>write</InlineCode> 把它们沉淀下来。</>,
        })}
      </CapabilityCard>
      <CapabilityCard
        label="mcp"
        title={t({ en: 'The complete OpenViking MCP toolset', zh: '完整的 OpenViking MCP 工具集' })}
      >
        {t({
          en: <><InlineCode>find</InlineCode>, <InlineCode>search</InlineCode>, <InlineCode>read</InlineCode>, <InlineCode>tree</InlineCode>, <InlineCode>grep</InlineCode>, <InlineCode>remember</InlineCode>, <InlineCode>write</InlineCode>, <InlineCode>add_resource</InlineCode>, and more, exposed through a stdio proxy. The specification sensibly forbids hardcoding credentials in static manifests, and every OpenViking deployment has its own endpoint — so the proxy resolves your server URL and API key locally at runtime, from the same sources as the <InlineCode>ov</InlineCode> CLI. Install the package and it works with your existing setup.</>,
          zh: <><InlineCode>find</InlineCode>、<InlineCode>search</InlineCode>、<InlineCode>read</InlineCode>、<InlineCode>tree</InlineCode>、<InlineCode>grep</InlineCode>、<InlineCode>remember</InlineCode>、<InlineCode>write</InlineCode>、<InlineCode>add_resource</InlineCode> 等工具，全部通过一个 stdio 代理暴露。新规范明确禁止在静态清单里硬编码凭证，而每个 OpenViking 部署的 endpoint 又各不相同——所以这个代理在运行时于本地解析你的服务器地址和 API key，来源与 <InlineCode>ov</InlineCode> CLI 完全一致。装上即可直接使用你现有的 OpenViking 配置。</>,
        })}
      </CapabilityCard>
    </div>

    <H2>{t({ en: "What the Portable Package Doesn't Do — Yet", zh: '这个包暂时做不到的事' })}</H2>

    <P>
      {t({
        en: 'Agent Plugins 1.0 deliberately limits its scope to Skills and MCP. Hooks — the mechanism our per-client plugins use for automatic conversation capture and pre-turn memory recall — are excluded from this first version of the specification, because their semantics still vary too widely across clients.',
        zh: 'Agent Plugins 1.0 有意把标准化范围限定在 Skills 和 MCP。Hooks 被排除在这个初版规范之外——各客户端的 hook 语义目前差异太大。而 hooks 正是我们各客户端专属插件用来做自动对话采集和每轮对话前记忆召回的机制。',
      })}
    </P>

    <P>
      {t({
        en: <>As a result, the portable package operates within a strictly defined scope: memory recall and writes are entirely <Strong>model-driven</Strong>. They rely on the agent following the skill's instructions, without the safety net of background hooks. Simply put: if the model doesn't proactively decide to save something, it won't be saved.</>,
        zh: <>因此，这个可移植包的工作边界很明确：记忆的召回和写入完全由<Strong>模型驱动</Strong>，依赖 Agent 遵循 skill 中的指引，没有后台 hooks 兜底。直白地说：模型没有主动决定保存的信息，就不会被保存。</>,
      })}
    </P>

    <H2>{t({ en: 'So Which One Should You Install?', zh: '那到底该装哪个？' })}</H2>

    <P>
      {t({
        en: <>The short answer: if your client has a <A href={INTEGRATIONS_EN}>dedicated plugin</A> — Claude Code, Codex, Cursor, ZCode, or DeepSeek harness — install that one. Hooks give it automatic conversation capture and pre-turn memory recall, so the loop closes even when the model never thinks about saving. That is the experience we recommend and the one we optimize first, and we will keep maintaining these plugins.</>,
        zh: <>简单说：如果你的客户端有<A href={INTEGRATIONS_ZH}>专属插件</A>——Claude Code、Codex、Cursor、ZCode、DeepSeek harness——请装专属插件。hooks 让它自动采集对话、在每轮开始前召回记忆，模型没想起来也不会漏。这是我们推荐、也是我们优先打磨的体验，这些插件会持续维护下去。</>,
      })}
    </P>

    <P>
      {t({
        en: <>The portable package covers everywhere else. If you work in ChatGPT, GitHub Copilot, Kiro, or VS Code, there was previously no way to bring OpenViking along at all — now there is. And it will grow: the specification's roadmap explicitly reserves hooks and other extension types for future versions. The day Agent Plugins standardizes hooks, the package you already have picks up automatic capture and recall through a simple update, and the maintenance matrix of <Strong>N clients × M plugins</Strong> finally collapses into a single package.</>,
        zh: <>可移植包负责覆盖其余场景。如果你平时用的是 ChatGPT、GitHub Copilot、Kiro 或 VS Code，此前没有办法把 OpenViking 带进去，现在可以了。而且它会继续生长：规范的路线图已经明确为 hooks 等扩展类型预留了位置。等到 Agent Plugins 标准化 hooks 的那一天，你已经装好的这个包只需一次简单更新，就能补上自动采集和自动召回。到那时，<Strong>N 个客户端 × M 个插件</Strong>的维护矩阵将彻底收敛为一个包。</>,
      })}
    </P>

    <P>
      {t({
        en: <>Until then: dedicated plugins where we have them, one portable directory everywhere else — and an agent that remembers either way. Try it out from <A href={PLUGIN_DIR}><InlineCode>agent-plugins/</InlineCode></A>, and let us know what breaks.</>,
        zh: <>在那之前：有专属插件的客户端用专属插件，其余环境用这一个目录——两条路，Agent 都记得住事。欢迎从 <A href={PLUGIN_DIR}><InlineCode>agent-plugins/</InlineCode></A> 装来试试，有问题随时告诉我们。</>,
      })}
    </P>
  </Article>
);

export default {
  id: 'openviking-agent-plugins',
  Component: OpenVikingAgentPlugins,
  meta: {
    title: {
      en: 'OpenViking Now Supports Agent Plugins 1.0',
      zh: 'OpenViking 支持 Agent Plugins 1.0',
    },
    description: {
      en: 'One portable package brings OpenViking memory to ChatGPT, Codex, Cursor, GitHub Copilot, Kiro, and VS Code — no more per-client plugins.',
      zh: '一个可移植安装包，把 OpenViking 记忆带进 ChatGPT、Codex、Cursor、GitHub Copilot、Kiro 和 VS Code，不再需要为每个客户端单独装插件。',
    },
    cover: COVER,
    cardCover: COVER,
    publishedAt: '2026-08-14',
    readingTime: { en: 5, zh: 4 },
    category: { en: 'Engineering', zh: '工程' },
    tags: ['openviking', 'agent-plugins', 'mcp', 'skills', 'memory'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [{ name: 'OpenViking Team', github: 'volcengine' }],
  },
};
