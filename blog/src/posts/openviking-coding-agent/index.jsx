import React from 'react';
import {
  Article, Lead, P, H2, H3, Pre, Quote, Pull, Callout, Hr, Figure,
  Cols, Col, Ol, Li, Ul, Table, A, InlineCode, Strong, Tag, Mark,
} from '../../blog-components';

const OPENVIKING_GITHUB = 'https://github.com/volcengine/OpenViking';
const OPENVIKING_DOCS = 'https://docs.openviking.ai';
const CLAUDE_PLUGIN_SRC = 'https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin';
const CODEX_PLUGIN_SRC = 'https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin';
const ARCH_POST = '/post/openviking-context-database-architecture';
const LOCAL_DEPLOY_DOC = 'https://docs.openviking.ai/zh/getting-started/02-quickstart';
const CLOUD_CONSOLE = 'https://console.volcengine.com/vikingdb/openviking';

const IMG = '/assets/posts/openviking-coding-agent';

function PainPoint({ icon, title, detail }) {
  return (
    <div style={{
      display: 'flex', gap: 14, alignItems: 'flex-start',
      padding: '14px 0',
      borderBottom: '1px solid var(--th-line)',
    }}>
      <div style={{
        width: 36, height: 36, borderRadius: 10,
        background: 'var(--th-accent)', color: 'var(--th-bg)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 18, flexShrink: 0, fontWeight: 700,
      }}>{icon}</div>
      <div>
        <div style={{
          fontFamily: 'var(--th-font-display)', fontWeight: 600,
          fontSize: 15, marginBottom: 4,
        }}>{title}</div>
        <div style={{ color: 'var(--th-mute)', fontSize: 14, lineHeight: 1.6 }}>{detail}</div>
      </div>
    </div>
  );
}

function MemoryTypeCard({ dimension, type, desc, color }) {
  return (
    <div style={{
      padding: '12px 14px',
      borderRadius: 8,
      border: `1px solid ${color}33`,
      background: `${color}08`,
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6,
      }}>
        <span style={{
          display: 'inline-block', padding: '2px 8px', borderRadius: 4,
          background: `${color}20`, color,
          fontFamily: 'var(--th-font-mono)', fontSize: 11, fontWeight: 600,
        }}>{dimension}</span>
        <span style={{
          fontFamily: 'var(--th-font-display)', fontWeight: 600, fontSize: 14,
        }}>{type}</span>
      </div>
      <div style={{ color: 'var(--th-mute)', fontSize: 13, lineHeight: 1.5 }}>{desc}</div>
    </div>
  );
}

function FormulaBlock({ T }) {
  return (
    <div style={{
      padding: '20px 24px',
      borderRadius: 10,
      border: '1px solid var(--th-line)',
      background: 'var(--th-surface, var(--th-bg))',
      fontFamily: 'var(--th-font-mono)',
      fontSize: 15,
      textAlign: 'center',
      lineHeight: 2,
    }}>
      <div style={{ fontSize: 13, color: 'var(--th-mute)', fontFamily: 'var(--th-font-body)', marginBottom: 8 }}>
        {T({ en: 'Hotness Decay Formula', zh: '热度衰减公式' })}
      </div>
      <code>hotness = sigmoid(log1p(access_count)) × exp(-decay_rate × age_days)</code>
      <div style={{ fontSize: 13, color: 'var(--th-mute)', fontFamily: 'var(--th-font-body)', marginTop: 10 }}>
        {T({
          en: 'Default half-life: 7 days. Untouched for 30 days, it approaches zero. High-frequency insights stay active regardless of age.',
          zh: '默认半衰期 7 天。30 天未访问则濒临归零。高频查询的核心知识始终保持活跃。',
        })}
      </div>
    </div>
  );
}

function LifecycleStep({ n, event, when, action }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '32px 1fr 2fr',
      gap: 12, padding: '12px 0',
      borderBottom: '1px solid var(--th-line)',
      fontSize: 14, alignItems: 'start',
    }}>
      <div style={{
        width: 28, height: 28, borderRadius: '50%',
        background: 'var(--th-accent)', color: 'var(--th-bg)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: 'var(--th-font-mono)', fontWeight: 700, fontSize: 12,
        flexShrink: 0,
      }}>{n}</div>
      <div>
        <div style={{ fontWeight: 600, fontFamily: 'var(--th-font-mono)', fontSize: 13 }}>{event}</div>
        <div style={{ color: 'var(--th-mute)', fontSize: 12, marginTop: 2 }}>{when}</div>
      </div>
      <div style={{ color: 'var(--th-mute)', lineHeight: 1.5 }}>{action}</div>
    </div>
  );
}

const OpenVikingCodingAgent = ({ t }) => {
  const T = t;

  return (
    <Article>
      <Lead>{T({
        en: 'Every time you open a new terminal window, your Coding Agent develops amnesia. OpenViking fixes this by giving Claude Code and Codex persistent, cross-session, and cross-device memory—completely automatically. One command to install, zero changes to your workflow.',
        zh: '每次开新窗口，你的 Coding Agent 就会把一切忘光。OpenViking 为 Claude Code 和 Codex 提供跨会话、跨设备的持久记忆——全自动。一行命令安装，零使用习惯改变。',
      })}</Lead>

      <H2 id="pain-points">{T({ en: 'The Problem', zh: '开发者的痛点' })}</H2>

      <PainPoint
        icon="1"
        title={T({ en: 'Memory Silos', zh: '记忆孤岛' })}
        detail={T({
          en: 'Context and history remain trapped in silos. Switching between machines or agents means you are always starting from scratch.',
          zh: '同一个项目在多台设备、多种 Agent 之间切换开发，上下文和历史记忆互不相通。',
        })}
      />
      <PainPoint
        icon="2"
        title={T({ en: 'No Experience Reuse', zh: '经验无法复用' })}
        detail={T({
          en: 'Current agent "memory" is merely the active context window patched with a static CLAUDE.md. It fails to accumulate real-world experience across different tasks or repositories.',
          zh: 'Coding Agent 的"记忆"本质上只是当前会话上下文，加上少量提炼后的 CLAUDE.md。无法跨需求、跨项目积累开发经验。',
        })}
      />
      <PainPoint
        icon="3"
        title={T({ en: 'Constant Resets', zh: '反复重置' })}
        detail={T({
          en: 'Start a new thread, and your architectural decisions, hard-won debugging insights, and coding preferences vanish. You\'re back to square one.',
          zh: '一旦开启新对话，之前的架构决策、踩坑记录、编码偏好全部归零，只能一遍遍重新输入。',
        })}
      />

      <P>{T({
        en: 'You\'re forced into a frustrating loop: either meticulously maintain dense environment documents by hand, or sound like a broken record briefing the AI in every single session.',
        zh: '要么手动维护大量环境文档，要么每次都像复读机一样向 AI 重复交代前置信息。',
      })}</P>

      <P>{T({
        en: 'OpenViking completely solves this.',
        zh: '现在，接入 OpenViking 可以彻底解决这些问题。',
      })}</P>

      <Callout type="info">
        <P>{T({
          en: <>New to OpenViking? Check out the <A href={ARCH_POST}>architecture overview</A>. In short: OpenViking is a dedicated <Strong>Context Database</Strong> for AI agents. It's far more than a simple RAG vector store—it continuously captures, distills, and evolves memory directly from your conversations.</>,
          zh: <>对 OpenViking 还不熟悉？先阅读<A href={ARCH_POST}>架构介绍</A>。简而言之，OpenViking 是一个专为 AI Agent 设计的<Strong>上下文数据库（Context Database）</Strong>——它能在对话中持续积累与提炼记忆，自动提取用户画像、技术偏好、项目决策，并随时间自我更新与优化。</>,
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="quick-start">{T({ en: 'Quick Start', zh: '快速开始' })}</H2>

      <P>{T({
        en: <>Whether you're running OpenViking <A href={LOCAL_DEPLOY_DOC}>locally deployed</A> or using the <A href={CLOUD_CONSOLE}>Volcengine Cloud</A>, installation takes just one command:</>,
        zh: <>如果你已拥有可用的 OpenViking 服务（<A href={LOCAL_DEPLOY_DOC}>本地部署</A>或 <A href={CLOUD_CONSOLE}>Volcengine Cloud 云服务</A>），只需一行命令即可完成插件安装：</>,
      })}</P>

      <H3>{T({ en: 'Claude Code Plugin', zh: 'Claude Code 插件' })}</H3>

      <Pre lang="bash" filename="terminal">{`bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)`}</Pre>

      <H3>{T({ en: 'Codex Plugin', zh: 'Codex 插件' })}</H3>

      <Pre lang="bash" filename="terminal">{`bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/codex-memory-plugin/setup-helper/install.sh)`}</Pre>

      <P>{T({
        en: 'The interactive installer guides you through connecting to a local server (no auth required) or a remote instance (via API key). It automatically configures ~/.openviking/ovcli.conf, clones the plugin source, and registers all necessary hooks and MCP services.',
        zh: '安装脚本提供交互式引导：可以选择连接无需认证的本地服务器（http://127.0.0.1:1933），或是需要 API Key 的远程服务。脚本会自动配置 ~/.openviking/ovcli.conf、克隆插件源码，并注册好所有的 Hook 和 MCP 服务。',
      })}</P>

      <Callout type="tip">
        <P>{T({
          en: 'The installation is completely idempotent—running it multiple times is perfectly safe. Once done, simply restart your terminal and launch claude or codex as usual. The plugin operates silently in the background.',
          zh: '安装过程是幂等的，重复执行安全无副作用。安装完成后重新打开终端，照常启动 claude 或 codex 即可。插件在后台静默工作，不需要改变任何使用习惯。',
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="capabilities">{T({ en: 'What the Plugin Gives Your Agent', zh: '插件赋予了 Agent 什么能力' })}</H2>

      <P>{T({
        en: 'Once integrated, your Coding Agent gains two core superpowers: invisible conversation hooks and proactively callable MCP tools.',
        zh: '接入插件后，Coding Agent 获得两项核心能力：无感的对话 Hook 和可主动调用的 MCP 工具。',
      })}</P>

      <H3 id="hooks">{T({ en: 'Conversation Hooks: Invisible Read/Write', zh: '对话 Hook：隐式拦截与无感读写' })}</H3>

      <P>{T({
        en: 'Hooks trigger automatically at critical points in the conversation lifecycle. They require zero explicit tool calls, making them completely transparent to both you and the model.',
        zh: 'Hook 在对话生命周期的关键节点自动触发，不需要模型主动调用工具，对用户和模型几乎透明。',
      })}</P>

      <LifecycleStep n="1" event="SessionStart"
        when={T({ en: 'Session begins / resumes', zh: '对话开始或恢复' })}
        action={T({ en: 'Automatically injects your developer profile, an index of available memories, and a summary of the previous session\'s Working Memory.', zh: '自动注入用户画像（profile.md）、可用记忆列表以及上一次对话的 Working Memory 摘要。' })}
      />
      <LifecycleStep n="2" event="UserPromptSubmit"
        when={T({ en: 'User sends a message', zh: '每次用户发送消息' })}
        action={T({ en: 'Performs semantic search against OpenViking based on your prompt, injecting the most relevant memories as context patches.', zh: '根据用户输入的语义检索 OpenViking，将最相关的记忆作为上下文补丁注入给模型。' })}
      />
      <LifecycleStep n="3" event="Stop"
        when={T({ en: 'Model completes a turn', zh: '模型完成一轮回复' })}
        action={T({ en: 'Stages the completed conversation turn into the current OpenViking Session.', zh: '将本轮对话记录暂存至 OpenViking Session。' })}
      />
      <LifecycleStep n="4" event="PreCompact"
        when={T({ en: 'Context about to be compressed', zh: '上下文即将被压缩前' })}
        action={T({ en: 'Force-commits the conversation history to prevent any detail loss before the model truncates the context window.', zh: '强制同步提交对话，确保在上下文截断前不丢失任何细节。' })}
      />
      <LifecycleStep n="5" event="SessionEnd"
        when={T({ en: 'Session terminates', zh: '对话彻底结束' })}
        action={T({ en: 'Commits all pending records and triggers the asynchronous background memory distillation process.', zh: '提交所有记录，并触发后台的"记忆提炼"流程。' })}
      />
      <LifecycleStep n="6" event="SubagentStart/Stop"
        when={T({ en: 'Sub-agent spawns / exits', zh: '子 Agent 启动/结束' })}
        action={T({ en: 'Provisions isolated sessions for sub-agents to prevent memory namespace pollution.', zh: '为子 Agent 创建独立隔离的 Session，防止记忆命名空间污染。' })}
      />

      <Quote cite={T({ en: 'Design principle', zh: '设计理念' })}>
        {T({
          en: 'You just focus on coding. The plugin remembers everything worth remembering and seamlessly feeds it back to the model exactly when it\'s needed.',
          zh: '你只管专注开发和对话，插件会自动帮你记住所有有价值的信息，并在合适的时机自动喂给模型。',
        })}
      </Quote>

      <P>{T({
        en: <>You won't feel any performance hit. The write path is asynchronous by default—hooks like <InlineCode>Stop</InlineCode> and <InlineCode>SessionEnd</InlineCode> instantly return <InlineCode>approve</InlineCode> to keep the chat flowing smoothly, while a detached background worker handles the actual HTTP requests. Zero perceived latency.</>,
        zh: <>性能方面完全无需担忧。Hook 的写入路径默认异步——<InlineCode>Stop</InlineCode> 和 <InlineCode>SessionEnd</InlineCode> 会立即返回 <InlineCode>approve</InlineCode> 让对话无缝继续，真正的 HTTP 写入由后台 detach 的 worker 进程完成，用户完全感受不到延迟。</>,
      })}</P>

      <H3 id="mcp-tools">{T({ en: 'MCP Tools: Active Memory Management', zh: 'MCP 工具：赋予 Agent 主动管理记忆的权限' })}</H3>

      <P>{T({
        en: 'Beyond automatic hooks, the plugin exposes standard MCP tools that the agent can actively query when it lacks context:',
        zh: '除了自动的 Hook，插件还通过 MCP 协议向 Agent 暴露一组工具。当模型认为有必要时，可以主动查询和管理知识库：',
      })}</P>

      <Table
        headers={[
          T({ en: 'Tool', zh: '工具名称' }),
          T({ en: 'Purpose', zh: '核心用途' }),
        ]}
        rows={[
          [<InlineCode>search</InlineCode>, T({ en: 'Semantic search across history, resources, and skills', zh: '语义检索历史记忆、相关资源和使用过的技能' })],
          [<InlineCode>read</InlineCode>, T({ en: 'Fetch the complete contents of a viking:// URI', zh: '读取指定 viking:// URI 的完整文件内容' })],
          [<InlineCode>list</InlineCode>, T({ en: 'Browse the memory directory structure (supports recursion)', zh: '遍历和浏览记忆目录结构（支持递归）' })],
          [<InlineCode>remember</InlineCode>, T({ en: 'Proactively lock current context into long-term memory', zh: '主动将当前重要的上下文存入长期记忆' })],
          [<InlineCode>add_resource</InlineCode>, T({ en: 'Import external files or URLs as knowledge sources (with auto-refresh)', zh: '引入外部文件或 URL 作为知识源（支持定时刷新）' })],
          ['grep / glob', T({ en: 'Regex text search / pattern-based file matching', zh: '正则搜索文本内容 / 按模式匹配检索文件' })],
          [<InlineCode>forget</InlineCode>, T({ en: 'Clean up redundant or outdated memories', zh: '清理或删除指定的冗余记忆' })],
          [<InlineCode>health</InlineCode>, T({ en: 'Check backend service health status', zh: '检查后端记忆服务的健康状态' })],
        ]}
      />

      <P>{T({
        en: <>These tools upgrade your agent from a passive listener to an active investigator. If the agent needs to recall an architectural spec from last week, it can proactively trigger <InlineCode>search</InlineCode> to retrieve the exact details on its own.</>,
        zh: <>这些工具让 Agent 从被动接收信息转变为主动探索。例如，当 Agent 需要回忆某个曾在上周讨论过的架构方案时，它可以主动调用 <InlineCode>search</InlineCode> 将细节翻找出来。</>,
      })}</P>

      <Hr ornament />

      <H2 id="memory-lifecycle">{T({ en: 'How Memory Accumulates and Distills', zh: '记忆是如何积累和提炼的' })}</H2>

      <P>{T({
        en: 'This is where OpenViking truly diverges from standard RAG setups. Conversations aren\'t just blindly embedded; they undergo rigorous, multi-stage memory lifecycle management.',
        zh: '这是 OpenViking 区别于简单 RAG 向量库的关键所在。对话被收集后，并非简单地灌入向量库，而是会经历一套严密的记忆生命周期管理。',
      })}</P>

      <H3 id="storage">{T({ en: 'Conversation Storage and Archival', zh: '对话的存储与归档' })}</H3>

      <P>{T({
        en: <>Every terminal window maps to an OpenViking Session. In-progress dialog lives at <InlineCode>{'viking://session/{id}/messages.jsonl'}</InlineCode>. Once a session concludes or hits a token threshold (default 8,000), the archival mechanism kicks in.</>,
        zh: <>每一个对话窗口对应一个 Session。未提交的进行中对话存放在 <InlineCode>{'viking://session/{id}/messages.jsonl'}</InlineCode>；一旦对话完成或 token 超过阈值（默认 8000），就会触发归档机制。</>,
      })}</P>

      <Figure
        src={`${IMG}/session-storage.png`}
        alt={T({ en: 'Session storage structure', zh: 'Session 存储结构' })}
        caption={T({ en: 'Session storage: in-progress conversations and archived history', zh: 'Session 存储结构：未提交的对话和归档的历史' })}
        size="lg"
      />

      <P>{T({
        en: 'Archival is more than just moving files around. It operates in two critical phases:',
        zh: '归档过程不仅仅是"移动文件"，它包含两个至关重要的阶段：',
      })}</P>

      <P><Strong>{T({ en: 'Phase 1: Message Archival', zh: '阶段一：消息归档' })}</Strong>{T({
        en: ' — Old messages are shifted to the archive directory. The active Session retains only a sliding window of recent turns, preventing infinite context bloat.',
        zh: '——将旧消息移入归档区，当前 Session 仅保留最近几轮对话作为滑动窗口，防止 OV 端 Session 无限膨胀。',
      })}</P>

      <P><Strong>{T({ en: 'Phase 2: Memory Distillation (async)', zh: '阶段二：记忆提炼（异步）' })}</Strong>{T({
        en: ' — This is where the magic happens. OpenViking spins up an asynchronous LLM task in the background to execute deep processing:',
        zh: '——归档后，OpenViking 在后台启动一个大模型异步任务，执行深度处理：',
      })}</P>

      <Ol>
        <Li>{T({
          en: <>Generate <Strong>Working Memory</Strong>: Condense the archived chat into a structured 7-part summary (title, current state, goals, key decisions, relevant files, error fixes, open issues).</>,
          zh: <>生成<Strong>工作记忆（Working Memory）</Strong>：将归档的长对话浓缩为 7 段式结构化摘要（会话标题、当前状态、任务目标、关键决策、相关文件、错误修正、遗留问题）。</>,
        })}</Li>
        <Li>{T({
          en: <>Extract <Strong>8 types of structured memories</Strong> across two dimensions:</>,
          zh: <>进行<Strong>多维提取 8 类结构化记忆</Strong>：</>,
        })}</Li>
      </Ol>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 10, margin: '16px 0' }}>
        <MemoryTypeCard dimension={T({ en: 'User', zh: '用户' })} type="Profile" desc={T({ en: 'Identity, tech background, and core skills — continuously merged into a single source of truth.', zh: '身份、技术背景与核心技能（单一文件，持续融合更新）' })} color="#4f8ff7" />
        <MemoryTypeCard dimension={T({ en: 'User', zh: '用户' })} type="Preferences" desc={T({ en: 'Code styling, tooling choices, and workflow habits.', zh: '代码风格、工具选择偏好' })} color="#4f8ff7" />
        <MemoryTypeCard dimension={T({ en: 'User', zh: '用户' })} type="Entities" desc={T({ en: 'Specific projects, people, and abstract concepts discussed.', zh: '讨论过的特定项目、人物或概念抽象' })} color="#4f8ff7" />
        <MemoryTypeCard dimension={T({ en: 'User', zh: '用户' })} type="Events" desc={T({ en: 'Key architectural decisions, milestones, and changelogs.', zh: '项目的重要决策、里程碑和变更记录' })} color="#4f8ff7" />
        <MemoryTypeCard dimension={T({ en: 'Agent', zh: 'Agent' })} type="Cases" desc={T({ en: 'Classic problem–solution pairs.', zh: '经典的"问题-解决方案"对' })} color="#e5684a" />
        <MemoryTypeCard dimension={T({ en: 'Agent', zh: 'Agent' })} type="Patterns" desc={T({ en: 'Reusable logic workflows and strategies.', zh: '可被未来复用的流程和策略' })} color="#e5684a" />
        <MemoryTypeCard dimension={T({ en: 'Agent', zh: 'Agent' })} type="Tools" desc={T({ en: 'Tool usage insights: success rates, timings, and parameter configs.', zh: '工具的使用心得（成功率、耗时、推荐参数等）' })} color="#e5684a" />
        <MemoryTypeCard dimension={T({ en: 'Agent', zh: 'Agent' })} type="Skills" desc={T({ en: 'Memory traces of successfully executed workflows.', zh: '成功执行过的工作流记忆' })} color="#e5684a" />
      </div>

      <Ol>
        <Li>{T({
          en: <>Perform <Strong>Knowledge Fusion</Strong>: New insights aren't blindly overwritten; they are intelligently merged with existing ones. For instance, your profile.md grows sharper and more accurate with every session.</>,
          zh: <>执行<Strong>知识融合</Strong>：新提取的记忆与旧记忆智能合并。例如 profile.md 会随使用次数的增加刻画得越来越精准，绝不是简单粗暴的覆盖。</>,
        })}</Li>
      </Ol>

      <Figure
        src={`${IMG}/memory-tree.png`}
        alt={T({ en: 'Memory directory structure', zh: '记忆目录结构' })}
        caption={T({ en: 'Organized memories neatly stored under viking://user/{name}/memories/', zh: '提炼后的记忆有序归类在 viking://user/{name}/memories/ 命名空间下' })}
        size="lg"
      />

      <H3 id="hotness">{T({ en: 'Memory Temperature Decay', zh: '记忆的温度衰减机制' })}</H3>

      <P>{T({
        en: 'Human memory naturally fades over time, and OpenViking mimics this behavior. A memory\'s "Hotness" is dictated by both its access frequency and time decay:',
        zh: '人的记忆会随时间淡忘，OpenViking 也模拟了这一机制。每条记忆的热度（Hotness）由"访问频率"和"时间衰减"共同决定：',
      })}</P>

      <FormulaBlock T={T} />

      <Hr ornament />

      <H2 id="experience">{T({ en: 'Real-World Experience: An AI That Actually "Gets" You', zh: '实际体验：这台 Agent 真的懂我' })}</H2>

      <H3 id="auto-profile">{T({ en: 'Session Start: The Auto-Injected Profile', zh: '对话伊始：自动就位的用户画像' })}</H3>

      <P>{T({
        en: 'Whenever you launch or resume a chat, the plugin silently constructs and injects a bespoke context payload right from the start:',
        zh: '每次开启或恢复对话时，插件都会在背景里默默构建一段专属上下文。以下是一个典型的注入载荷：',
      })}</P>

      <div className="b-pre">
        <div className="b-pre__bar"><span className="b-pre__file">injected context</span></div>
        <pre className="b-pre__code"><code>{`<openviking-context source="resume">
<user-profile uri="viking://user/zeus/memories/profile.md">
# Zayn (@zaynjarvis)
- Role: Senior Engineer
- Repos: volcengine/OpenViking, ZaynJarvis/*, ...
- Focus: OpenViking optimization, swarm model analysis, Zouk UI
- 2026-05-19: Completed atlas-fs toolcall toggle optimization...
</user-profile>
<available-memories>
  viking://user/zeus/memories/preferences/
    - @zaynjarvis/code_development_preference.md
    - ... (13 preference files)
  viking://user/zeus/memories/entities/
    - software_project/openviking.md
    - ... (20 entity files)
</available-memories>
<session-archive>
  <archive-overview>
# Working Memory
## Session Title
Zeus Agent Post-Restructuring Task Catch-Up & Atlas Fix
## Current State
Zeus completed atlas-fs toolcall toggle fix (PR #6)...
## Open Issues
- Zouk UI task clarification pending
- Open issues: #23, #42, #50
  </archive-overview>
</session-archive>
</openviking-context>`}</code></pre>
      </div>

      <Pull side="right">
        {T({
          en: 'Before you even type a keystroke, the AI already knows who you are, your coding quirks, exactly where you left off, and what issues are still open.',
          zh: '在你敲下第一行字之前，模型就已经知道：你是谁、你的代码癖好是什么、上次你们肝到了哪里、还有哪些 Bug 没修完。',
        })}
      </Pull>

      <P>{T({
        en: 'The days of typing "Hi, last time we were working on XXX..." are officially over.',
        zh: '那些诸如"你好，我们上次做到了 XXX"的废话开场白，从此成为历史。',
      })}</P>

      <H3 id="realtime-recall">{T({ en: 'Mid-Coding: Precision Recall', zh: '编码途中：精准的实时召回' })}</H3>

      <P>{T({
        en: 'With every prompt you submit, the plugin executes a millisecond-level semantic search under the hood. Ask a passing question like "How does OpenViking handle MCP OAuth?" and it instantly fetches relevant history:',
        zh: '伴随你的每一次输入，插件都在进行毫秒级的语义搜索。比如随口问一句"OpenViking 是怎么处理 MCP 的 OAuth 的？"，插件瞬间在后台召回了以下记忆并拼接到 prompt 中：',
      })}</P>

      <Pre lang="bash" filename="recalled memories" lineNumbers={false}>{`- [memory 60%] # MCP-Key2OAuth项目信息
- 项目地址：https://github.com/t0saki/MCP-Key2OAuth...
- [memory 55%] # OpenViking MCP协议实现
- 工具列表：find、search、read、list...
- [memory 54%] # OpenViking
- MCP端点：注册为精确匹配的Starlette Route...`}</Pre>

      <P>{T({
        en: 'The model utilizes these highly relevant historical assets to synthesize a precise answer—saving you from digging through project docs manually. The confidence score (e.g., 60%) helps the model accurately weigh the information\'s reliability.',
        zh: '模型直接利用这些高度相关的历史资产给出了完美解答，甚至不需要翻看之前的项目文档。每条记录前的置信度分数（如 60%），也帮助模型更好地判断信息权重。',
      })}</P>

      <H3 id="cross-session">{T({ en: 'Cross-Session Enlightenment', zh: '跨越周期的长期启发' })}</H3>

      <P>{T({
        en: 'This is where you get that "this AI actually gets me" feeling. During a recent architecture review, OpenViking proactively recalled a side-by-side comparison of three design patterns we had discussed weeks prior, in a completely unrelated terminal window:',
        zh: '这正是让人产生"这台 Agent 真的很聪明"错觉的时刻。在一次架构选型中，OpenViking 竟然主动召回了几周前、另一个不相关的对话窗口中做过的一份"三套架构方案优劣势横评"——精准点出了当时遗留的技术债：',
      })}</P>

      <Figure
        src={`${IMG}/cross-session-recall.png`}
        alt={T({ en: 'Cross-session recall of architecture comparison', zh: '跨 session 召回架构方案对比' })}
        caption={T({ en: 'OpenViking automatically surfaced a design-phase architecture comparison from weeks ago', zh: 'OpenViking 自动召回了之前设计阶段的方案对比' })}
        size="lg"
      />

      <Quote>
        {T({
          en: '"Having your past technical wisdom automatically surface exactly when you need it"—this is an experience that manually updating a CLAUDE.md file can never replicate.',
          zh: '"总能在你需要时，恰如其分地浮现出你过去的智慧"——这种体验，是靠手写 CLAUDE.md 永远无法企及的。',
        })}
      </Quote>

      <Hr ornament />

      <H2 id="cross-platform">{T({ en: 'Breaking Down Walls: Cross-Platform Memory Sharing', zh: '打破壁垒：跨平台的记忆共享' })}</H2>

      <P>{T({
        en: 'Because of its client-server architecture, your memory assets reside securely on the OpenViking server, rather than being fragmented across dozens of local project folders.',
        zh: '得益于 C/S 架构，记忆资产存在于 OpenViking 服务端，而非散落在各个本地项目中。',
      })}</P>

      <P>{T({
        en: 'Run both the Claude Code and Codex plugins, and they share a single, unified brain. A tricky bug you resolved in Claude Code instantly becomes knowledge available to Codex.',
        zh: '如果同时安装了 Claude Code 插件和 Codex 插件，它们将共享同一个大脑。在 Claude Code 中调试出的经验，切换到 Codex 里照样能用。',
      })}</P>

      <P>{T({
        en: 'It gets better. Because OpenViking exposes standard MCP endpoints, *any* MCP-compatible client can tap into it. Connect a standard desktop Claude Chat to OpenViking, and you can instantly generate a comprehensive weekly dev report pulling from all your recent terminal activities:',
        zh: '更奇妙的是，由于 OpenViking 提供标准 MCP 接口，任何支持 MCP 的客户端都能接入。打开一个 Claude Chat 对话框，连上 OpenViking，就能一键生成本周的开发周报：',
      })}</P>

      <Figure
        src={`${IMG}/weekly-report.png`}
        alt={T({ en: 'Auto-generating weekly report from OpenViking memories', zh: '利用 OpenViking 记忆自动生成周报' })}
        caption={T({ en: 'Claude Chat auto-generates a weekly report by pulling from OpenViking memories', zh: 'Claude Chat 利用 OpenViking 记忆自动生成周报' })}
        size="lg"
      />

      <P>{T({
        en: 'You can even upload the finalized report back into OpenViking, forming a perfect memory loop:',
        zh: '甚至可以将定稿的周报反向上传回 OpenViking，形成完美的记忆闭环：',
      })}</P>

      <Figure
        src={`${IMG}/memory-loop.png`}
        alt={T({ en: 'Uploading report back to OpenViking', zh: '周报上传回 OpenViking' })}
        caption={T({ en: 'Report content uploaded back to OpenViking as long-term memory', zh: '周报内容上传 OpenViking 形成长期记忆' })}
        size="lg"
      />

      <Pull side="left">
        {T({
          en: 'Accumulate once, available everywhere.',
          zh: '一次积累，全端可用。',
        })}
      </Pull>

      <Hr ornament />

      <H2 id="plugin-diff">{T({ en: 'Claude Code vs Codex Plugin Differences', zh: 'Claude Code 与 Codex 插件的差异说明' })}</H2>

      <P>{T({
        en: 'Though built on the same core philosophy, the plugins have slight implementation differences to accommodate their respective host environments:',
        zh: '尽管核心理念一致，但受限于两者的宿主环境差异，插件在具体实现上做了适配调整：',
      })}</P>

      <Table
        headers={[
          T({ en: 'Feature', zh: '特性' }),
          T({ en: 'Claude Code', zh: 'Claude Code 插件' }),
          T({ en: 'Codex', zh: 'Codex 插件' }),
        ]}
        rows={[
          [
            T({ en: 'Hook Count', zh: 'Hook 数量' }),
            T({ en: '7 (full SessionEnd + sub-agent lifecycle)', zh: '7 个（完美支持 SessionEnd 和子 Agent 生命周期）' }),
            T({ en: '4 (no explicit exit callback)', zh: '4 个（缺少显式的退出回调）' }),
          ],
          [
            T({ en: 'Session Start', zh: '对话开始时' }),
            T({ en: 'Inject profile + historical Working Memory', zh: '注入画像 + 历史 Working Memory' }),
            T({ en: 'Heuristics: automatically commit previous idle sessions', zh: '启发式策略，先尝试提交上次的闲置 Session' }),
          ],
          [
            T({ en: 'Session End', zh: '对话结束时' }),
            T({ en: 'Accurate SessionEnd trigger', zh: '依赖 SessionEnd 准确触发最终提交' }),
            T({ en: 'Next-start check + 30min idle fallback', zh: '依赖下次启动时的活跃窗口检查 + 30分钟闲置兜底' }),
          ],
          [
            T({ en: 'MCP Transport', zh: 'MCP 传输机制' }),
            T({ en: 'Standard HTTP (direct /mcp endpoint)', zh: '标准 HTTP（直连 /mcp 端点）' }),
            T({ en: 'Streamable HTTP (with env token support)', zh: 'Streamable HTTP（支持携带环境变量 Token）' }),
          ],
          [
            T({ en: 'Sub-agents', zh: '子 Agent 支持' }),
            T({ en: 'Fully isolated sessions', zh: '完美隔离（各自拥有独立 Session 记录）' }),
            T({ en: 'Not yet supported', zh: '暂不支持' }),
          ],
          [
            T({ en: 'Runtime', zh: '运行环境' }),
            'Node.js',
            'Node.js 22+',
          ],
        ]}
      />

      <Callout type="note">
        <P>{T({
          en: <><strong>For Codex users:</strong> Because hitting <InlineCode>Ctrl+C</InlineCode> in the terminal doesn't trigger a clean exit hook, the plugin employs a clever fallback. Upon your next startup, it checks for recently active sessions. Additionally, any session left idle for over 30 minutes is automatically swept up, archived, and distilled by a background process.</>,
          zh: <>对于 Codex 用户：由于终端下 <InlineCode>Ctrl+C</InlineCode> 退出往往不会触发任何 Hook，插件设计了巧妙的兜底机制——每次启动时会检查是否存在"刚结束不久"的活跃记录；如果一个 Session 超过 30 分钟没有新动作，也会被后台判定为结束并自动触发记忆归档。</>,
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="advanced">{T({ en: 'Advanced Tuning and Security', zh: '高级调优与安全设计' })}</H2>

      <P>{T({
        en: 'The plugin offers extensive configuration options via environment variables or your ovcli.conf file:',
        zh: '插件提供了极高的可定制性，可通过环境变量或 ovcli.conf 覆盖默认配置：',
      })}</P>

      <Pre lang="bash" filename="~/.openviking/ovcli.conf">{`# Memory recall tuning
OPENVIKING_RECALL_LIMIT=6           # Max 6 memories per injection
OPENVIKING_SCORE_THRESHOLD=0.35     # Filter out items below 35% relevance
OPENVIKING_RECALL_TOKEN_BUDGET=2000 # Cap injected token count to protect context

# Capture strategy
OPENVIKING_CAPTURE_MODE=semantic    # semantic (default continuous) or keyword (triggered)
OPENVIKING_CAPTURE_ASSISTANT_TURNS=true  # Include AI replies in memory extraction

# Debug & Overrides
OPENVIKING_DEBUG=true               # Output logs: ~/.openviking/logs/cc-hooks.log
OPENVIKING_BYPASS_SESSION=true      # Disable hooks entirely for highly sensitive sessions`}</Pre>

      <H3 id="security">{T({ en: 'Security by Design', zh: '安全设计' })}</H3>

      <Ul>
        <Li><Strong>{T({ en: 'Credentials never touch the disk', zh: '凭证绝不落盘' })}</Strong>{T({
          en: ': API keys are dynamically injected into process environment variables via a shell wrapper. They are never written to .mcp.json, remaining entirely invisible to npm scripts and crash dumps.',
          zh: '：API Key 通过 shell wrapper 动态注入到进程环境变量中，既不写进 .mcp.json，也避免被 npm、崩溃转储等子进程窃取。',
        })}</Li>
        <Li><Strong>{T({ en: 'Self-pollution prevention', zh: '防止记忆自污染' })}</Strong>{T({
          en: ': Before sending conversational records for LLM distillation, the plugin meticulously strips out <openviking-context> tags, ensuring that "answers generated using memory" aren\'t recursively stored as new knowledge.',
          zh: '：在对话记录提交给 LLM 提炼前，插件会自动清洗掉 <openviking-context> 等注入标签，防止"用记忆生成的回答"再次被当做新知识存入。',
        })}</Li>
        <Li><Strong>{T({ en: 'Sub-agent namespace isolation', zh: '子 Agent 命名空间隔离' })}</Strong>{T({
          en: <>: Divergent thoughts from sub-agents are strictly isolated. Each receives a unique session ID like <InlineCode>{'cc-<session>__agent-<id>'}</InlineCode> to prevent them from polluting the main conversational timeline.</>,
          zh: <>：每个子 Agent 被分配如 <InlineCode>{'cc-<session>__agent-<id>'}</InlineCode> 的独立 Session ID，防止发散的思考污染主干对话记忆。</>,
        })}</Li>
      </Ul>

      <Hr ornament />

      <H2 id="vs-native">{T({ en: 'Why Not Just Use MEMORY.md?', zh: '为什么不直接用内置的 MEMORY.md？' })}</H2>

      <P>{T({
        en: 'OpenViking is designed as a powerful complement and upgrade, not a strict replacement for native memory systems.',
        zh: 'OpenViking 插件的定位是补充与升维，而非替代原生系统。',
      })}</P>

      <Table
        headers={[
          T({ en: 'Dimension', zh: '维度' }),
          T({ en: 'Native MEMORY.md / AGENTS.md', zh: '原生 MEMORY.md / AGENTS.md' }),
          T({ en: 'OpenViking Plugin', zh: 'OpenViking 记忆插件' }),
        ]}
        rows={[
          [
            T({ en: 'Storage Format', zh: '存储形态' }),
            T({ en: 'Flat Markdown text', zh: '扁平的 Markdown 纯文本' }),
            T({ en: 'Vector DB + relational graphs + structured objects', zh: '向量库 + 关系图谱 + 结构化对象' }),
          ],
          [
            T({ en: 'Retrieval Engine', zh: '检索机制' }),
            T({ en: 'Entire file dumped blindly into context window', zh: '粗暴地全量塞入模型上下文' }),
            T({ en: 'Precision semantic similarity + strict token budgets', zh: '语义相似度召回 + 严格的 Token 预算控制' }),
          ],
          [
            T({ en: 'Operational Scope', zh: '作用范围' }),
            T({ en: 'Trapped inside a single project directory', zh: '局限于当前单项目目录' }),
            T({ en: 'Cross-project, cross-session, cross-client connectivity', zh: '打通跨项目、跨 Session、跨客户端的全局记忆' }),
          ],
          [
            T({ en: 'Capacity Limit', zh: '容量限制' }),
            T({ en: '~200 lines max (bounded by context)', zh: '受限于文件长度（约 200 行极值）' }),
            T({ en: 'Virtually unlimited (backed by server storage)', zh: '几乎无限（受服务端存储支持）' }),
          ],
          [
            T({ en: 'Knowledge Entry', zh: '知识录入' }),
            T({ en: 'Requires manual developer curation', zh: '需要开发者手动梳理并编写规则' }),
            T({ en: 'LLM-driven automated, implicit extraction', zh: 'LLM 驱动的自动化隐式提取' }),
          ],
        ]}
      />

      <P>{T({
        en: <>Keep using <InlineCode>MEMORY.md</InlineCode> for static, project-wide coding conventions. But for dynamic, organically growing knowledge—like "how did I bypass that weird auth bug last Tuesday?" or "what's my preferred way to structure React hooks?"—let OpenViking's automated memory engine handle the heavy lifting.</>,
        zh: <>把 <InlineCode>MEMORY.md</InlineCode> 用来定义"当前项目必须遵守的编码规范"依然是好主意；但对于"上周我怎么解决的那个偶发 Crash"、"我习惯用哪种风格封装 Axios"这类随时间动态生长的经验，交给 OpenViking 自动打理才是最优解。</>,
      })}</P>

      <Hr ornament />

      <H2 id="conclusion">{T({ en: 'Conclusion', zh: '结语' })}</H2>

      <P>{T({
        en: 'By integrating OpenViking, your Coding Agent evolves from a stateless, forgetful utility into an intelligent pair programmer that learns your habits and scales with your expertise:',
        zh: '接入 OpenViking 后，Coding Agent 完成了一次重要的进化——从"用完即走、过目即忘的无状态工具"，蜕变为"熟悉你习惯、伴随你成长的智能结推伙伴"：',
      })}</P>

      <Ul>
        <Li><Strong>{T({ en: 'Auto-Accumulate', zh: '自动积累' })}</Strong>{T({ en: ': Every debugging session and strategic decision becomes a permanent digital asset. Zero manual maintenance.', zh: '——告别手动维护，每次踩坑和决策自动沉淀为数字资产。' })}</Li>
        <Li><Strong>{T({ en: 'Smart Recall', zh: '智能召回' })}</Strong>{T({ en: ': Historical context surfaces naturally exactly when needed. No pre-prompting required.', zh: '——告别前置预热，在恰当的语境下，历史记忆自然浮现。' })}</Li>
        <Li><Strong>{T({ en: 'Cross-Platform', zh: '多端共享' })}</Strong>{T({ en: ': Break out of context silos. Accumulate once, and share across Claude Code, Codex, Claude Chat, and any MCP client.', zh: '——告别上下文孤岛，一次积累，全端生态互通。' })}</Li>
        <Li><Strong>{T({ en: 'Continuous Evolution', zh: '持续进化' })}</Strong>{T({ en: ': Memories constantly merge, distill, and gracefully decay, ensuring your AI always operates with maximum information density.', zh: '——记忆会合并、会提炼、会衰减，永远保持最高的信息密度。' })}</Li>
      </Ul>

      <P>{T({
        en: 'If you\'re exhausted from endlessly re-onboarding your AI assistant every time you open a new terminal, it\'s time to install OpenViking.',
        zh: '如果你已经厌倦了每次打开新窗口都要重新"调教"AI，不妨现在就试试 OpenViking。',
      })}</P>

      <Hr ornament />

      <H2 id="links" toc={false}>{T({ en: 'Links', zh: '相关传送门' })}</H2>

      <Ul>
        <Li><A href={OPENVIKING_GITHUB}>OpenViking GitHub</A></Li>
        <Li><A href={OPENVIKING_DOCS}>OpenViking Docs</A></Li>
        <Li><A href={CLAUDE_PLUGIN_SRC}>{T({ en: 'Claude Code Plugin Source & README', zh: 'Claude Code 插件源码及 README' })}</A></Li>
        <Li><A href={CODEX_PLUGIN_SRC}>{T({ en: 'Codex Plugin Source & README', zh: 'Codex 插件源码及 README' })}</A></Li>
        <Li><A href={ARCH_POST}>{T({ en: 'Deep Dive: OpenViking Architecture', zh: '深度阅读：OpenViking 架构设计' })}</A></Li>
      </Ul>
    </Article>
  );
};

export default {
  id: 'openviking-coding-agent',
  Component: OpenVikingCodingAgent,
  meta: {
    title: {
      zh: '在 Claude Code / Codex 中接入 OpenViking：让你的 Coding Agent 拥有长期记忆',
      en: 'OpenViking for Claude Code & Codex: Give Your Coding Agent Persistent Memory',
    },
    description: {
      zh: '一行命令为 Claude Code 和 Codex 接入 OpenViking，实现跨会话、跨设备的自动记忆积累、语义召回与多端共享。',
      en: 'One command gives Claude Code and Codex persistent, cross-session memory powered by OpenViking. Experience automatic knowledge accumulation, semantic recall, and multi-platform sharing.',
    },
    cover: '/assets/covers/openviking-coding-agent.png',
    publishedAt: '2026-05-20',
    readingTime: { zh: 12, en: 10 },
    category: { zh: '工程', en: 'Engineering' },
    tags: ['openviking', 'claude-code', 'codex', 'mcp', 'memory'],
    languages: ['en', 'zh'],
    authors: [{ name: 'tosaki', github: 't0saki', role: { en: 'Engineer', zh: '工程师' } }],
  },
};