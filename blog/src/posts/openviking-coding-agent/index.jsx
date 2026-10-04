import React from 'react';
import {
  Article, Lead, P, H2, H3, Pre, Quote, Pull, Callout, Hr, Figure,
  Ol, Li, Ul, Table, A, InlineCode, Strong,
} from '../../blog-components';

const OPENVIKING_GITHUB = 'https://github.com/volcengine/OpenViking?utm_source=blog&utm_medium=article&utm_campaign=openviking-coding-agent';
const OPENVIKING_DOCS = 'https://docs.openviking.ai';
const CLAUDE_PLUGIN_SRC = 'https://github.com/volcengine/OpenViking/tree/main/examples/claude-code-memory-plugin';
const CODEX_PLUGIN_SRC = 'https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin';
const ARCH_POST = '/post/openviking-context-database-architecture';
const LOCAL_DEPLOY_DOC = 'https://docs.openviking.ai/zh/getting-started/02-quickstart';
const CLOUD_CONSOLE = 'https://console.volcengine.com/vikingdb/openviking';
const LLM_PATH = '/post/openviking-coding-agent/llm.txt';
const CLAUDE_MEMORY_DOC = 'https://code.claude.com/docs/en/memory';

const IMG = '/assets/posts/openviking-coding-agent';
// Keep table columns readable on phones; .b-table-wrap already scrolls horizontally.
const TABLE_STYLE = '.ov-readable-tables .b-table th, .ov-readable-tables .b-table td { min-width: 8em; }';

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

function MemoryTypeCard({ group, type, desc, tone }) {
  return (
    <div style={{
      padding: '12px 14px',
      borderRadius: 8,
      border: '1px solid var(--th-line)',
      borderLeft: `3px solid ${tone}`,
      background: 'var(--th-bg-2)',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap',
      }}>
        <span style={{
          display: 'inline-block', padding: '2px 8px', borderRadius: 4,
          border: '1px solid var(--th-line)', color: 'var(--th-mute)',
          fontFamily: 'var(--th-font-mono)', fontSize: 11, fontWeight: 600,
        }}>{group}</span>
        <span style={{
          fontFamily: 'var(--th-font-mono)', fontWeight: 600, fontSize: 14,
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
      background: 'var(--th-bg-2)',
      fontFamily: 'var(--th-font-mono)',
      fontSize: 15,
      textAlign: 'center',
      lineHeight: 2,
      overflowWrap: 'anywhere',
    }}>
      <div style={{ fontSize: 13, color: 'var(--th-mute)', fontFamily: 'var(--th-font-body)', marginBottom: 8 }}>
        {T({ en: 'Hotness score', zh: '热度分' })}
      </div>
      <code>hotness = sigmoid(log1p(active_count)) × exp(−ln2 / 7 × age_days)</code>
      <div style={{ fontSize: 13, color: 'var(--th-mute)', fontFamily: 'var(--th-font-body)', marginTop: 10 }}>
        {T({
          en: 'Default half-life: 7 days. After 30 days without an update, the time factor is about 0.05. Use count can push the frequency factor toward 1, but cannot offset the decay.',
          zh: '默认半衰期 7 天。30 天没有更新，时间因子约为 0.05。使用次数最多把频率因子推近 1，抵消不了时间衰减。',
        })}
      </div>
    </div>
  );
}

function LifecycleStep({ n, event, when, action }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '32px minmax(0, 1fr) minmax(0, 2fr)',
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
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontFamily: 'var(--th-font-mono)', fontSize: 13, overflowWrap: 'anywhere' }}>{event}</div>
        <div style={{ color: 'var(--th-mute)', fontSize: 12, marginTop: 2 }}>{when}</div>
      </div>
      <div style={{ color: 'var(--th-mute)', lineHeight: 1.5, minWidth: 0 }}>{action}</div>
    </div>
  );
}

// A directory tree drawn with theme tokens; rows are [depth, name, note].
function TreeDiagram({ title, rows, caption }) {
  return (
    <figure style={{ margin: '1.5rem 0' }}>
      <div style={{
        border: '1px solid var(--th-line)',
        borderRadius: 8,
        background: 'var(--th-bg-2)',
        padding: '14px 16px',
        overflowX: 'auto',
      }}>
        <div style={{
          fontFamily: 'var(--th-font-mono)', fontSize: 12, color: 'var(--th-mute)',
          marginBottom: 8, overflowWrap: 'anywhere',
        }}>{title}</div>
        <div style={{ display: 'grid', gap: 2, minWidth: 0 }}>
          {rows.map(([depth, name, note], i) => (
            <div key={i} style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(12rem, max-content) minmax(0, 1fr)',
              gap: 16,
              alignItems: 'baseline',
              padding: '3px 0',
              borderTop: i === 0 ? 'none' : '1px dashed var(--th-line)',
            }}>
              <span style={{
                fontFamily: 'var(--th-font-mono)', fontSize: 13,
                paddingLeft: `${depth * 1.1}rem`, color: 'var(--th-ink)', whiteSpace: 'nowrap',
              }}>{name}</span>
              <span style={{ color: 'var(--th-mute)', fontSize: 13, lineHeight: 1.45 }}>{note}</span>
            </div>
          ))}
        </div>
      </div>
      {caption ? (
        <figcaption style={{ color: 'var(--th-mute)', fontSize: 13, marginTop: 8, textAlign: 'center' }}>{caption}</figcaption>
      ) : null}
    </figure>
  );
}

const OpenVikingCodingAgent = ({ t }) => {
  const T = t;

  return (
    <Article className="ov-readable-tables">
      <style>{TABLE_STYLE}</style>
      <Lead>{T({
        en: 'Every time you open a new terminal window, your coding agent starts over. OpenViking gives Claude Code and Codex long-term memory that survives sessions and machines: conversations are captured and distilled on their own, recalled when relevant, and shared by both clients. One command installs it, and afterwards you use claude or codex as before.',
        zh: '每次开新窗口，你的 Coding Agent 就会把之前的一切忘光。OpenViking 让 Claude Code 和 Codex 拥有跨会话、跨设备的长期记忆：对话自动捕获、提炼，需要时召回，两个客户端还能共用同一份记忆。一行命令安装，装好后照常使用 claude 或 codex。',
      })}</Lead>

      <H2 id="pain-points">{T({ en: 'The Problem', zh: '开发者的痛点' })}</H2>

      <PainPoint
        icon="1"
        title={T({ en: 'Memory Silos', zh: '记忆孤岛' })}
        detail={T({
          en: 'Work on one project across several machines and agents, and the context and history in each stay separate. Switching means starting from scratch.',
          zh: '同一个项目在多台设备、多种 Agent 之间切换开发，上下文和历史记忆互不相通。',
        })}
      />
      <PainPoint
        icon="2"
        title={T({ en: 'No Experience Reuse', zh: '经验无法复用' })}
        detail={T({
          en: 'A coding agent\'s "memory" is mostly the current context window plus local files such as CLAUDE.md, AGENTS.md, and per-project notes. They live on one machine and follow one project, so experience does not build up across tasks or repositories.',
          zh: 'Coding Agent 的“记忆”主要是当前会话的上下文，加上 CLAUDE.md、AGENTS.md 这类本地文件和按项目存放的笔记。它们放在本机、跟着单个项目，很难跨需求、跨项目积累开发经验。',
        })}
      />
      <PainPoint
        icon="3"
        title={T({ en: 'Constant Resets', zh: '反复重置' })}
        detail={T({
          en: 'Start a new thread, and your architectural decisions, hard-won debugging insights, and coding preferences are out of sight. You explain them again.',
          zh: '一旦开启新对话，之前的架构决策、踩坑记录、编码偏好都不在眼前，只能一遍遍重新交代。',
        })}
      />

      <P>{T({
        en: 'The result is a frustrating loop: either maintain dense environment documents by hand, or brief the AI like a broken record in every session.',
        zh: '结果就是：要么手动维护大量环境文档，要么每次都像复读机一样向 AI 重复交代前置信息。',
      })}</P>

      <P>{T({
        en: 'The OpenViking plugin moves that work from you to the system.',
        zh: '接入 OpenViking 插件，就是把这部分工作从人身上交给系统。',
      })}</P>

      <Callout type="info">
        <P>{T({
          en: <>New to OpenViking? Start with the <A href={ARCH_POST}>architecture overview</A>. In short, OpenViking is a <Strong>context database</Strong> for AI agents. Beyond storing vectors for RAG, it keeps conversations, distills them into memories such as your profile, preferences, and project decisions, and updates those memories as later conversations arrive.</>,
          zh: <>对 OpenViking 还不熟悉？先读<A href={ARCH_POST}>架构介绍</A>。简单说，OpenViking 是一个为 AI Agent 设计的<Strong>上下文数据库（Context Database）</Strong>：它不只是 RAG 用的向量库，还会保存对话，从中提炼出用户画像、技术偏好、项目决策这些记忆，并在后续对话里持续更新。</>,
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="quick-start">{T({ en: 'Quick Start', zh: '快速开始' })}</H2>

      <P>{T({
        en: <>You need a running OpenViking server, either <A href={LOCAL_DEPLOY_DOC}>deployed locally</A> or the hosted service on <A href={CLOUD_CONSOLE}>Volcengine</A>. The server must support the viking://~ home alias, which the plugin uses to address your own memory space. Claude Code and Codex share one installer, which asks which client to set up:</>,
        zh: <>先要有一个可用的 OpenViking 服务：<A href={LOCAL_DEPLOY_DOC}>本地部署</A>，或火山引擎的<A href={CLOUD_CONSOLE}>托管服务</A>。服务端需要支持 viking://~ 家目录别名，插件用它读写你自己的记忆空间。Claude Code 和 Codex 共用一个安装脚本，运行时会询问要装哪个，一条命令即可覆盖两者：</>,
      })}</P>

      <Pre lang="bash" filename="terminal">{`curl -fsSL https://openviking.ai/install | bash

# or pick the client up front
curl -fsSL https://openviking.ai/install | bash -s -- --harness claude
curl -fsSL https://openviking.ai/install | bash -s -- --harness codex`}</Pre>

      <P>{T({
        en: 'The installer downloads plugins from the OpenViking documentation site and never contacts github.com. If openviking.ai is slow to reach, openviking.net serves the same script, and so does the Volcengine TOS mirror:',
        zh: '安装器从 OpenViking 文档站下载插件，不访问 github.com。openviking.ai 访问不畅时，可以换成 openviking.net，或者运行火山引擎 TOS 上的同一个安装器：',
      })}</P>

      <Pre lang="bash" filename="terminal">{`bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)`}</Pre>

      <P>{T({
        en: 'The installer walks you through connecting to a local server (http://127.0.0.1:1933, no authentication by default) or a remote one with an API key. For a remote server, use a user key, not the root key. It checks the server, prints every change it will make, and writes nothing until you confirm. Then it writes ~/.openviking/ovcli.conf, installs openviking-memory into Claude Code (version 2.1.224 and later keep it updated on their own), and registers the plugin for Codex with plugin hooks enabled.',
        zh: '安装脚本提供交互式引导：可以连接本地服务（http://127.0.0.1:1933，默认不需要认证），也可以连接需要 API Key 的远程服务。远程服务填的是 user key，不是 root key。脚本会先检查服务是否可用，列出将要修改的内容，确认之后才写入：配置好 ~/.openviking/ovcli.conf，为 Claude Code 安装 openviking-memory 插件（2.1.224 及以上版本之后会自动更新），为 Codex 注册插件并开启插件 hooks。',
      })}</P>

      <Callout type="tip">
        <P>{T({
          en: 'Re-running the installer is safe. Once done, reopen your terminal and launch claude or codex as usual. On its first start, Codex stops at "6 hooks need review": choose Trust all and continue. If you skip it, the MCP tools still work, but recall and capture never fire.',
          zh: '重复执行安装脚本是安全的。装好后重新打开终端，照常启动 claude 或 codex。Codex 第一次启动会停在“6 hooks need review”，选 Trust all and continue；跳过的话 MCP 工具照样能用，但自动召回和捕获都不会触发。',
        })}</P>
      </Callout>

      <P>{T({
        en: 'If you prefer to control each step, install from the plugin marketplaces instead. Write ~/.openviking/ovcli.conf yourself first, or run the setup.mjs wizard bundled with the plugin; for Codex, also turn on hooks under [features] in ~/.codex/config.toml if your build has them off. Pick one path: enabling the plugin from both the installer and a marketplace runs every hook twice.',
        zh: '想自己控制每一步，也可以走插件市场。这条路需要先自己写好 ~/.openviking/ovcli.conf，或者运行插件自带的 setup.mjs 向导；Codex 如果默认没开 hooks，还要在 ~/.codex/config.toml 的 [features] 里打开。安装器和插件市场二选一：两边都启用，每个 hook 都会执行两遍。',
      })}</P>

      <Pre lang="bash" filename="terminal">{`# Claude Code
claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
claude plugin install openviking-memory@openviking

# Codex
codex plugin marketplace add volcengine/OpenViking
codex plugin add openviking-memory@openviking`}</Pre>

      <Hr ornament />

      <H2 id="capabilities">{T({ en: 'What the Plugin Gives Your Agent', zh: '插件赋予了 Agent 什么能力' })}</H2>

      <P>{T({
        en: 'Once integrated, your coding agent gains two capabilities: conversation hooks that run without being asked, and MCP tools it can call on purpose.',
        zh: '接入插件后，Coding Agent 获得两项能力：无感的对话 Hook，和可以主动调用的 MCP 工具。',
      })}</P>

      <H3 id="hooks">{T({ en: 'Conversation Hooks: Invisible Read/Write', zh: '对话 Hook：隐式拦截与无感读写' })}</H3>

      <P>{T({
        en: 'Hooks fire at fixed points in the conversation lifecycle. The model does not call them, so they stay out of the way of both you and the model. The list below follows Claude Code; Codex differences come later.',
        zh: 'Hook 在对话生命周期的关键节点自动触发，不需要模型主动调用工具，对用户和模型几乎透明。下面以 Claude Code 为例，Codex 的差异放在后面说。',
      })}</P>

      <LifecycleStep n="1" event="SessionStart"
        when={T({ en: 'Start, resume, clear, or after compaction', zh: '对话开始、恢复、清空或压缩之后' })}
        action={T({ en: 'Injects your profile (profile.md), an index of your preference and entity memories, and a catalog of your skills. On resume and after compaction it adds the latest archived Working Memory. The whole block has a size cap; it is a starting point, not all of your history.', zh: '注入用户画像 profile.md、偏好和实体记忆的索引、可用技能清单；恢复会话或压缩之后，再附上最近一次归档的 Working Memory。整段内容有大小上限，是一个起点，而不是全部历史。' })}
      />
      <LifecycleStep n="2" event="UserPromptSubmit"
        when={T({ en: 'Each message you send', zh: '每次用户发送消息' })}
        action={T({ en: 'Searches OpenViking with your prompt. The server assembles the most relevant memories and skills within a token budget (1,600 by default) and skips URIs already injected in the last five turns. By default the plugin then compresses the result into a few bullets with a local claude -p call before injecting it.', zh: '按这条输入检索 OpenViking。服务端在 token 预算内（默认 1600）组装出最相关的记忆和技能，最近 5 轮已经注入过的 URI 不再重复。默认配置下，插件还会用本地的 claude -p 把结果压成几条要点再注入。' })}
      />
      <LifecycleStep n="3" event="Stop"
        when={T({ en: 'Model completes a turn', zh: '模型完成一轮回复' })}
        action={T({ en: 'Appends the new turns to your OpenViking session, and commits once pending content passes 20,000 tokens.', zh: '把新增的对话追加到 OpenViking Session；待处理内容超过 20000 token 时提交一次。' })}
      />
      <LifecycleStep n="4" event="PreCompact"
        when={T({ en: 'Before context compaction', zh: '上下文即将被压缩前' })}
        action={T({ en: 'Commits synchronously, so the full pre-compaction conversation becomes an archive before Claude Code rewrites the transcript.', zh: '同步提交，让压缩前的完整对话先变成归档，再交给 Claude Code 改写记录。' })}
      />
      <LifecycleStep n="5" event="SessionEnd"
        when={T({ en: 'Session closes', zh: '对话结束' })}
        action={T({ en: 'Final commit, so the last stretch of conversation is archived and goes through memory extraction too.', zh: '最后一次提交，让最后一段对话也进入归档和记忆提炼。' })}
      />
      <LifecycleStep n="6" event="SubagentStart/Stop"
        when={T({ en: 'Sub-agent spawns / exits', zh: '子 Agent 启动/结束' })}
        action={T({ en: 'Gives each sub-agent its own session and commits it when the sub-agent finishes, so its side explorations stay out of the main conversation.', zh: '为每个子 Agent 建独立的 Session，结束时单独提交，避免发散的过程混进主线对话。' })}
      />
      <LifecycleStep n="7" event="PreToolUse"
        when={T({ en: 'Read, Grep, Bash, and similar tools', zh: '调用 Read、Grep、Bash 等工具时' })}
        action={T({ en: 'If the path is a viking:// URI, points the model to the matching OpenViking tool, since a local file tool cannot open it.', zh: '如果路径是 viking:// 地址，提示模型改用对应的 OpenViking 工具，因为本地文件工具打不开它。' })}
      />

      <Quote cite={T({ en: 'Design principle', zh: '设计理念' })}>
        {T({
          en: 'You focus on coding. The plugin records what is worth keeping in the background and hands it back to the model when it becomes relevant.',
          zh: '你只管专注开发和对话，插件在后台记下值得留下的信息，并在用得上的时候送回给模型。',
        })}
      </Quote>

      <P>{T({
        en: <>Writes stay off your critical path. <InlineCode>Stop</InlineCode>, <InlineCode>SessionEnd</InlineCode>, and <InlineCode>SubagentStop</InlineCode> return <InlineCode>approve</InlineCode> immediately and hand the HTTP work to a detached background worker, so you never wait for a commit round trip. <InlineCode>PreCompact</InlineCode> is the exception: Claude Code rewrites the transcript right after it, so it runs synchronously. Reads are different. Auto-recall happens before the model answers, so search and local compression time shows up in every turn. If that feels slow, move compression to the server, turn it off, or disable auto-recall and keep capture only.</>,
        zh: <>写入路径默认不挡在对话前面。<InlineCode>Stop</InlineCode>、<InlineCode>SessionEnd</InlineCode> 和 <InlineCode>SubagentStop</InlineCode> 会立即返回 <InlineCode>approve</InlineCode> 让对话继续，真正的 HTTP 写入交给后台 detach 出去的 worker，提交的往返时间不落在你身上。<InlineCode>PreCompact</InlineCode> 是例外：Claude Code 紧接着就要改写对话记录，所以它同步执行。读路径不一样：自动召回发生在模型回答之前，检索和本地压缩的耗时会体现在每一轮的等待里。觉得慢，可以把压缩交给服务端、关掉压缩，或者关闭自动召回、只保留捕获。</>,
      })}</P>

      <P>{T({
        en: 'When the server is unreachable, writes are queued under ~/.openviking/pending/ with owner-only file permissions. The queue is replayed at the next session start, up to three retries per item, and stale entries expire after seven days.',
        zh: '服务暂时连不上时，写操作会排进本地的 ~/.openviking/pending/，文件只有本人可读写。下次会话开始时重放这个队列，每条最多重试 3 次，7 天后过期清理。',
      })}</P>

      <H3 id="mcp-tools">{T({ en: 'MCP Tools: Active Memory Management', zh: 'MCP 工具：让 Agent 主动管理记忆' })}</H3>

      <P>{T({
        en: 'Beyond the hooks, the plugin starts a local stdio MCP proxy that forwards to the server\'s /mcp endpoint. When the model decides it needs more context, it can query and manage the store directly:',
        zh: '除了自动的 Hook，插件还启动一个本地 stdio MCP 代理，转发到服务端的 /mcp 端点。模型认为有必要时，可以主动查询和管理知识库：',
      })}</P>

      <Table
        headers={[
          T({ en: 'Tool', zh: '工具名称' }),
          T({ en: 'Purpose', zh: '核心用途' }),
        ]}
        rows={[
          [<InlineCode>find / search</InlineCode>, T({ en: 'Semantic search over memories, resources, and skills; search can also assemble an injectable context block', zh: '语义检索记忆、资源和技能；search 还能直接组装出可注入的上下文块' })],
          [<InlineCode>read</InlineCode>, T({ en: 'Read one or more viking:// URIs in full', zh: '读取一个或多个 viking:// URI 的完整内容' })],
          [<InlineCode>list / tree</InlineCode>, T({ en: 'Browse the directory structure, recursively if needed', zh: '浏览目录结构，支持递归' })],
          ['grep / glob', T({ en: 'Regex search over content / match files by pattern', zh: '正则搜索文本内容 / 按模式匹配文件' })],
          [<InlineCode>remember</InlineCode>, T({ en: 'Send messages straight into long-term memory extraction', zh: '把当前重要的内容直接送去提取长期记忆' })],
          [<InlineCode>add_resource</InlineCode>, T({ en: 'Import files or URLs as knowledge sources, with optional periodic refresh', zh: '引入外部文件或 URL 作为知识源，可设定时刷新' })],
          [<InlineCode>add_skill</InlineCode>, T({ en: 'Create, install, or share a skill', zh: '新建、安装或共享技能' })],
          [<InlineCode>write / edit</InlineCode>, T({ en: 'Write or patch a viking:// file', zh: '写入或局部修改 viking:// 文件' })],
          [<InlineCode>forget</InlineCode>, T({ en: 'Delete a stale or wrong memory', zh: '删除过时或错误的记忆' })],
          [<InlineCode>health</InlineCode>, T({ en: 'Check backend service health', zh: '检查后端服务的健康状态' })],
        ]}
      />

      <P>{T({
        en: <>These tools turn the agent from a passive receiver into an active investigator. To recall an architecture plan discussed last week, it can call <InlineCode>search</InlineCode> and dig up the details itself. It can pull a design doc or API spec in with <InlineCode>add_resource</InlineCode> and keep it refreshed on a schedule, store a key decision with <InlineCode>remember</InlineCode> instead of waiting for the next commit, and remove a memory that turned out to be wrong with <InlineCode>forget</InlineCode>. The server's tool list is authoritative; the table above covers the ones you will use most.</>,
        zh: <>这些工具让 Agent 从被动接收信息变成主动探索。比如需要回忆上周讨论过的某个架构方案时，它可以主动调用 <InlineCode>search</InlineCode> 把细节翻出来；可以用 <InlineCode>add_resource</InlineCode> 引入一份设计文档或 API 规范，并让它定时同步；遇到关键决定，用 <InlineCode>remember</InlineCode> 当场记下，不必等下一次提交；发现一条记忆已经错了，用 <InlineCode>forget</InlineCode> 删掉。完整的工具列表以服务端为准，上表是最常用的部分。</>,
      })}</P>

      <Hr ornament />

      <H2 id="memory-lifecycle">{T({ en: 'How Memory Accumulates and Distills', zh: '记忆是如何积累和提炼的' })}</H2>

      <P>{T({
        en: 'This is where OpenViking departs from a plain RAG setup. Conversations are not just embedded as they are; they go through a memory lifecycle.',
        zh: '这是 OpenViking 区别于简单 RAG 向量库的地方。对话收集上来之后，不是直接灌进向量库，而是要经过一套记忆生命周期。',
      })}</P>

      <H3 id="storage">{T({ en: 'Conversation Storage and Archival', zh: '对话的存储与归档' })}</H3>

      <P>{T({
        en: <>Every conversation window maps to one OpenViking session whose ID derives from the host's session ID: <InlineCode>{'cc-<session_id>'}</InlineCode> for Claude Code and <InlineCode>{'cx-<session_id>'}</InlineCode> for Codex, so resume and compaction keep writing to the same session. Uncommitted messages live at <InlineCode>{'viking://user/{you}/sessions/{id}/messages.jsonl'}</InlineCode>. A commit happens when pending content passes the threshold (20,000 tokens by default), before compaction, and when the session ends.</>,
        zh: <>每一个对话窗口对应一个 OpenViking Session，ID 由宿主的会话 ID 派生：Claude Code 是 <InlineCode>{'cc-<session_id>'}</InlineCode>，Codex 是 <InlineCode>{'cx-<session_id>'}</InlineCode>，恢复和压缩之后都写回同一个 Session。未提交的消息存放在 <InlineCode>{'viking://user/{你}/sessions/{id}/messages.jsonl'}</InlineCode>；待处理内容超过阈值（默认 20000 token）、上下文压缩前、对话结束时，都会触发提交。</>,
      })}</P>

      <TreeDiagram
        title="viking://user/alice/sessions/"
        caption={T({ en: 'Session storage: in-progress messages and archived history (illustrative)', zh: 'Session 存储结构：未提交的对话和归档的历史（示意）' })}
        rows={[
          [0, 'cc-9b0962da-…/', T({ en: 'one Claude Code window', zh: '一个 Claude Code 窗口' })],
          [1, 'messages.jsonl', T({ en: 'messages not yet committed', zh: '尚未提交的消息' })],
          [1, 'history/', ''],
          [2, 'archive_001/', T({ en: 'first commit', zh: '第一次提交' })],
          [3, 'messages.jsonl', T({ en: 'archived messages (phase 1)', zh: '归档的消息（阶段一）' })],
          [3, '.overview.md', T({ en: 'Working Memory (phase 2)', zh: 'Working Memory（阶段二）' })],
          [3, 'memory_diff.json', T({ en: 'memories added, updated, deleted (phase 2)', zh: '本次新增、修改、删除了哪些记忆（阶段二）' })],
          [3, '.done', T({ en: 'background processing finished', zh: '后台处理完成标记' })],
          [2, 'archive_002/', '…'],
          [0, 'cc-9b0962da-…__subagent-a1b2…/',T({ en: 'a sub-agent\'s own session', zh: '子 Agent 的独立 Session' })],
        ]}
      />

      <P>{T({
        en: 'Archival is more than moving files. A commit runs in two phases:',
        zh: '归档不只是“移动文件”，一次提交分两个阶段：',
      })}</P>

      <P><Strong>{T({ en: 'Phase 1: Message Archival', zh: '阶段一：消息归档' })}</Strong>{T({
        en: ' — Under a path lock, the server assigns an archive number, writes the messages to history/archive_NNN/, and keeps only what was not archived in the live session, so the session does not grow forever. Codex threshold commits keep the latest ten messages live as a sliding window. The request returns a task_id.',
        zh: '——在路径锁保护下分配归档编号，把消息写进 history/archive_NNN/，当前 Session 只保留未归档的部分，防止 OV 端的 Session 无限膨胀。Codex 达到阈值提交时会保留最近 10 条，作为滑动窗口。请求返回一个 task_id。',
      })}</P>

      <P><Strong>{T({ en: 'Phase 2: Memory Distillation (async)', zh: '阶段二：记忆提炼（异步）' })}</Strong>{T({
        en: ' — A background task picks up the archive from a persistent queue, so it resumes if the server restarts. It does the real processing:',
        zh: '——后台任务从持久化队列里取出这次归档，服务重启后也会继续。真正的加工在这里完成：',
      })}</P>

      <Ol>
        <Li>{T({
          en: <>Generate <Strong>Working Memory</Strong>: a seven-section summary of the archive (session title, current state, task and goals, key facts and decisions, files and context, errors and corrections, open issues). When a previous Working Memory exists, each section is updated incrementally and still-valid information carries forward.</>,
          zh: <>生成<Strong>工作记忆（Working Memory）</Strong>：把归档的对话浓缩成 7 段式结构化摘要（会话标题、当前状态、任务与目标、关键事实与决策、相关文件、错误与修正、遗留问题）。已有 Working Memory 时按段增量更新，仍然有效的信息会延续下去。</>,
        })}</Li>
        <Li>{T({
          en: <>Extract <Strong>long-term memories</Strong> into typed files:</>,
          zh: <>提取<Strong>长期记忆</Strong>，按类型写成文件：</>,
        })}</Li>
      </Ol>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10, margin: '16px 0' }}>
        <MemoryTypeCard group={T({ en: 'About you', zh: '关于你' })} type="profile" desc={T({ en: 'Identity, background, and core skills in one file that keeps getting merged.', zh: '身份、技术背景与核心技能，单一文件，持续融合更新。' })} tone="var(--th-accent)" />
        <MemoryTypeCard group={T({ en: 'About you', zh: '关于你' })} type="preferences" desc={T({ en: 'Code style, tool choices, and workflow habits, one topic per file.', zh: '代码风格、工具选择和工作习惯，按主题分文件。' })} tone="var(--th-accent)" />
        <MemoryTypeCard group={T({ en: 'About you', zh: '关于你' })} type="entities" desc={T({ en: 'Projects, people, services, and concepts you work with.', zh: '讨论过的项目、人物、服务和概念。' })} tone="var(--th-accent)" />
        <MemoryTypeCard group={T({ en: 'About you', zh: '关于你' })} type="events" desc={T({ en: 'Decisions, milestones, and changes, with dates.', zh: '项目的重要决策、里程碑和变更记录，带时间。' })} tone="var(--th-accent)" />
        <MemoryTypeCard group={T({ en: 'About the assistant', zh: '关于助手' })} type="identity / soul" desc={T({ en: 'The assistant\'s name, persona, principles, and boundaries.', zh: '助手的名字、形象、原则和边界。' })} tone="var(--th-accent-2)" />
        <MemoryTypeCard group={T({ en: 'Agent Evolution', zh: 'Agent Evolution' })} type="cases / trajectories / experiences" desc={T({ en: 'Tasks as cases, executions as trajectories, and reusable experience distilled from results. Only when Agent Evolution is enabled.', zh: '把任务组织成 case、把执行记成 trajectory，再从结果里提炼可复用的经验。需要开启 Agent Evolution。' })} tone="var(--th-tip)" />
      </div>

      <Ol>
        <Li>{T({
          en: <>Perform <Strong>knowledge fusion</Strong>: existing memories take part in extraction, and an update can merge into, modify, or delete an existing file. Your profile.md gets sharper the more you use it, instead of being overwritten each time.</>,
          zh: <>执行<Strong>知识融合</Strong>：已有的记忆会参与提取，更新可以合并、修改甚至删除现有文件。profile.md 随着使用次数增加刻画得越来越准确，而不是每次简单覆盖。</>,
        })}</Li>
        <Li>{T({
          en: <>Write <Strong>memory_diff.json</Strong> into the archive: which memories were added, updated, or deleted, and which proposed operations a policy skipped. It is the most direct answer to "what did that conversation actually leave behind?" A commit can also leave nothing new.</>,
          zh: <>在归档目录写入 <Strong>memory_diff.json</Strong>：这次新增、修改、删除了哪些记忆，哪些操作被策略跳过。想知道一次对话到底留下了什么，看这个文件最直接。一次提交也可能没有留下任何新记忆。</>,
        })}</Li>
      </Ol>

      <TreeDiagram
        title="viking://user/alice/"
        caption={T({ en: 'Distilled memories are organized under your own memory space (illustrative)', zh: '提炼后的记忆有序归类在你自己的记忆空间下（示意）' })}
        rows={[
          [0, 'memories/', T({ en: 'user-level memory, shared across projects', zh: '用户级记忆，跨项目共用' })],
          [1, 'profile.md', T({ en: 'who you are', zh: '你是谁' })],
          [1, 'preferences/', T({ en: 'e.g. code_review.md, commit_style.md', zh: '如 code_review.md、commit_style.md' })],
          [1, 'entities/', T({ en: 'e.g. checkout_service.md', zh: '如 checkout_service.md' })],
          [1, 'events/', T({ en: 'e.g. 2026-09-12_order_api_v2.md', zh: '如 2026-09-12_order_api_v2.md' })],
          [0, 'peers/', ''],
          [1, 'github.com-acme-checkout/', T({ en: 'one repository, derived from its Git origin', zh: '一个代码仓库，由 Git origin 推导' })],
          [2, 'memories/', T({ en: 'project memory for this repository', zh: '这个仓库的项目记忆' })],
          [0, 'skills/', T({ en: 'your skills; shared ones live in viking://agent/skills', zh: '你的技能；共享技能在 viking://agent/skills' })],
          [0, 'sessions/', T({ en: 'see above', zh: '见上图' })],
        ]}
      />

      <H3 id="hotness">{T({ en: 'Memory Temperature', zh: '记忆的温度' })}</H3>

      <P>{T({
        en: 'Human memory fades with time, and OpenViking tracks something similar. Each memory records how often it was used and when it was last updated, and the two combine into a hotness score:',
        zh: '人的记忆会随时间淡忘，OpenViking 也记录了类似的东西。每条记忆都有使用次数和最近更新时间，两者合成一个热度分：',
      })}</P>

      <FormulaBlock T={T} />

      <P>{T({
        en: 'Today the score drives memory health statistics: it sorts memories into cold, warm, and hot so you can see which ones have not been touched in a long time. It does not affect retrieval ranking, which stays on relevance, and it does not delete cold memories.',
        zh: '这个分数目前用于记忆的健康统计：把记忆分成冷、温、热三档，让你看出哪些记忆已经很久没被用到。它不参与检索排序，召回仍然按相关性打分；它也不会自动删除冷记忆。',
      })}</P>

      <Hr ornament />

      <H2 id="experience">{T({ en: 'Real-World Experience: An AI That Actually "Gets" You', zh: '实际体验：这台 Agent 真的懂我' })}</H2>

      <H3 id="auto-profile">{T({ en: 'Session Start: The Auto-Injected Profile', zh: '对话伊始：自动就位的用户画像' })}</H3>

      <P>{T({
        en: 'Whenever you launch or resume a chat, the plugin builds a context payload before you type anything. Here is what a resumed session can receive (the content is fictional):',
        zh: '每次开启或恢复对话时，插件都会在你开口之前先构建一段专属上下文。下面是恢复会话时的一个注入示例（内容为虚构）：',
      })}</P>

      <Pre lang="text" filename="injected context" lineNumbers={false}>{`<openviking-context source="resume">
<user-profile uri="viking://user/alice/memories/profile.md">
# Alice
- Role: backend engineer
- Repos: acme/checkout, acme/payments-sdk
- Focus: order API v2 migration, flaky integration tests
</user-profile>
<available-memories>
  viking://user/alice/memories/preferences/
    - code_review.md — small PRs, always with a test plan
    - ... (12 preference files)
  viking://user/alice/memories/entities/
    - checkout_service.md — owns order API v1 and v2
    - ... (18 entity files)
</available-memories>
<available-skills>
  viking://user/alice/skills/
    - pr-review — Review a pull request against the team checklist.
</available-skills>
<session-archive>
  <archive-overview>
# Working Memory
## Session Title
Order API v2 migration: keep v1 for mobile clients
## Current State
v2 endpoints merged; v1 stays behind a compatibility shim.
## Open Issues
- Mobile client 4.x still calls v1
- Flaky test in checkout/integration
  </archive-overview>
</session-archive>
</openviking-context>`}</Pre>

      <Pull side="right">
        {T({
          en: 'Before you type a keystroke, the model already knows who you are, how you like to work, where you left off, and which issues are still open.',
          zh: '在你敲下第一行字之前，模型就已经知道：你是谁、你的代码习惯是什么、上次做到了哪里、还有哪些问题没解决。',
        })}
      </Pull>

      <P>{T({
        en: 'You can drop the "Hi, last time we were working on XXX..." opener.',
        zh: '那些“你好，我们上次做到了 XXX”的开场白，可以省掉了。',
      })}</P>

      <H3 id="realtime-recall">{T({ en: 'Mid-Coding: Recall on Demand', zh: '编码途中：按需召回' })}</H3>

      <P>{T({
        en: 'Each time you submit a prompt, the plugin searches once before the model answers. Ask in passing, "How does OpenViking handle MCP OAuth?", and the recalled memories are spliced into the context like this (excerpt):',
        zh: '伴随你的每一次输入，插件都会在模型回答之前检索一次。比如随口问一句“OpenViking 是怎么处理 MCP 的 OAuth 的？”，召回的记忆会以这样的形式拼进上下文（节选）：',
      })}</P>

      <Pre lang="text" filename="recalled memories" lineNumbers={false}>{`<memory uri="viking://user/alice/memories/entities/mcp_key2oauth.md" type="entities" score="0.60" detail="abstract">
MCP-Key2OAuth: https://github.com/t0saki/MCP-Key2OAuth ...
</memory>
<memory uri="viking://user/alice/memories/entities/openviking_mcp.md" type="entities" score="0.55" detail="abstract">
OpenViking MCP implementation. Tools: find, search, read, list ...
</memory>
<memory uri="viking://user/alice/memories/entities/openviking.md" type="entities" score="0.54" detail="abstract">
OpenViking: the MCP endpoint is registered as an exact-match Starlette Route ...
</memory>`}</Pre>

      <P>{T({
        en: 'The model answers from these records directly, without you digging through project docs. The score is a relevance score used for ranking and filtering (results under 0.35 are not injected by default); it does not mean a memory is "60% likely to be true." Before acting on a memory, have the agent read the URI. The default auto mode prefers a local CLI call—claude -p in Claude Code, codex exec in Codex—to compress this block into a few bullets that keep their URIs. If no local compressor is available, it requests server compression. If a local call fails, it keeps the recalled content within the injection budget. How long all this takes depends on the network and on compression; the plugin\'s status line shows how many memories were injected in each turn and how long it took.',
        zh: '模型直接利用这些历史记录作答，不需要你再翻项目文档。score 是相关性分数，用来排序和筛选（默认低于 0.35 的不注入），不是“这条记忆有 60% 的可能是对的”。真要依据某条记忆做决定，让 Agent 用 read 打开对应的 URI 看原文。默认 auto 模式优先通过本地 CLI 压缩成几条带 URI 的要点：Claude Code 用 claude -p，Codex 用 codex exec。本地压缩器不可用时，会请求服务端压缩；本地调用失败时，则保留注入预算内的召回内容。整个过程花多久，取决于网络和压缩；插件的状态栏会显示每一轮注入了几条记忆、用了多长时间。',
      })}</P>

      <H3 id="cross-session">{T({ en: 'Cross-Session Insight', zh: '跨越周期的长期启发' })}</H3>

      <P>{T({
        en: 'The "this AI actually gets me" moment usually comes from a conversation long gone. During one storage review we compared three ways to support multiple workspaces: a workspace_id column, a table per workspace, or a schema per workspace. While weighing them, the model cited a constraint recorded weeks earlier, in a different and unrelated window: the service runs as a single replica and loads every workspace\'s data from the database into memory at startup. The conclusion changed because of it. At that scale the database was not the bottleneck; memory would hit the wall first.',
        zh: '让人觉得“这台 Agent 真的懂我”的时刻，往往来自很久以前的一次对话。有一次评估多工作区的存储方案——加一列 workspace_id、按工作区分表、还是每个工作区一个 schema——模型在比较三种方案时，引用了几周前另一个毫不相干的对话窗口里记下的一条约束：服务只有单副本，启动时会把所有工作区的数据从数据库全量加载进内存。结论因此变了：在这个规模下，数据库不是瓶颈，先撞墙的是内存。',
      })}</P>

      <P>{T({
        en: 'That memory scored only 0.37 and was nowhere near the top of the list. It was still the one fact that overturned the intuitive answer.',
        zh: '这条记忆的相关性分数只有 0.37，排得并不靠前，却恰好是推翻直觉判断的那条信息。',
      })}</P>

      <Quote>
        {T({
          en: '"Your past technical judgment surfaces right when you need it." That is hard to get from a hand-maintained CLAUDE.md.',
          zh: '“在你需要时，过去的经验恰好浮现出来”——这种体验，靠手写 CLAUDE.md 很难做到。',
        })}
      </Quote>

      <Hr ornament />

      <H2 id="cross-platform">{T({ en: 'Breaking Down Walls: Cross-Platform Memory Sharing', zh: '打破壁垒：跨平台的记忆共享' })}</H2>

      <P>{T({
        en: 'Because of the client-server architecture, your memory lives on the OpenViking server instead of being scattered across local project folders.',
        zh: '得益于 C/S 架构，记忆存在 OpenViking 服务端，而不是散落在各个本地项目里。',
      })}</P>

      <P>{T({
        en: 'When the Claude Code and Codex plugins connect to the same server as the same user, they share one memory. A tricky bug you solved in Claude Code is available in Codex. Project memory is keyed by the repository\'s Git origin, so every clone, worktree, and subdirectory of a repository, even a checkout on another machine, lands under the same peer, while a fork with a different origin stays separate. By default recall is broad: memories from other projects can come back too, ranked lower. To see only the current project plus your user-level memory, set the recall scope to actor.',
        zh: '如果 Claude Code 插件和 Codex 插件连到同一个服务、使用同一个用户身份，它们就共享同一个大脑：在 Claude Code 里调试出的经验，切到 Codex 照样能用。项目记忆按仓库的 Git origin 归属，所以同一个仓库的不同 clone、worktree、子目录，哪怕是另一台机器上的 checkout，都落在同一个 peer 下；origin 不同的 fork 默认分开。召回默认是宽范围的：其他项目的记忆也可能被召回，只是降权排在后面。想只看当前项目和用户级记忆，把召回范围设为 actor。',
      })}</P>

      <P>{T({
        en: 'It goes further. Because OpenViking exposes a standard MCP endpoint, any MCP-capable client can connect. Hook the Claude chat app up to OpenViking and ask for a weekly report built from your recent sessions and memories:',
        zh: '更进一步，由于 OpenViking 提供标准 MCP 接口，任何支持 MCP 的客户端都能接入。在 Claude 的对话应用里连上 OpenViking，就能根据最近的会话和记忆写一份本周开发周报：',
      })}</P>

      <Figure
        src={`${IMG}/weekly-report.png`}
        alt={T({ en: 'A weekly report generated from OpenViking memories', zh: '利用 OpenViking 记忆生成的周报' })}
        caption={T({ en: 'The Claude chat app reads OpenViking over MCP and drafts a weekly report', zh: 'Claude 对话应用通过 MCP 读取 OpenViking，生成一周周报' })}
        size="lg"
      />

      <P>{T({
        en: 'Once the report is final, you can upload it back to OpenViking as a resource. Next week, last week\'s report is searchable material too:',
        zh: '定稿的周报还可以作为资源传回 OpenViking。下周再写时，上周的定稿也成了可以检索的资料：',
      })}</P>

      <Figure
        src={`${IMG}/memory-loop.png`}
        alt={T({ en: 'Uploading the report back to OpenViking', zh: '把周报传回 OpenViking' })}
        caption={T({ en: 'The report is added with add_resource under viking://resources/', zh: '周报通过 add_resource 存入 viking://resources/' })}
        size="lg"
      />

      <Pull side="left">
        {T({
          en: 'Accumulate once, use it from every client.',
          zh: '一次积累，多端可用。',
        })}
      </Pull>

      <P>{T({
        en: 'What travels is server-side context. Each host\'s process state, conversations not yet captured, and tool configuration stay where they are.',
        zh: '跟着走的是服务端的上下文。各个宿主自己的进程状态、还没捕获的对话、工具配置，不会一起迁过去。',
      })}</P>

      <Hr ornament />

      <H2 id="plugin-diff">{T({ en: 'Claude Code vs Codex Plugin Differences', zh: 'Claude Code 与 Codex 插件的差异说明' })}</H2>

      <P>{T({
        en: 'The two plugins share the same design and most of their code, but each adapts to what its host exposes:',
        zh: '两个插件的核心理念一致，大部分代码也是共用的，但受宿主环境所限，具体实现上做了适配：',
      })}</P>

      <Table
        headers={[
          T({ en: 'Feature', zh: '特性' }),
          T({ en: 'Claude Code', zh: 'Claude Code 插件' }),
          T({ en: 'Codex', zh: 'Codex 插件' }),
        ]}
        rows={[
          [
            T({ en: 'Registered hooks', zh: '注册的 Hook' }),
            T({ en: '9 entries, including SessionEnd and the sub-agent lifecycle', zh: '9 项，含 SessionEnd 和子 Agent 生命周期' }),
            T({ en: '6: SessionStart, UserPromptSubmit, PreToolUse, Stop, PreCompact, SessionEnd', zh: '6 项：SessionStart、UserPromptSubmit、PreToolUse、Stop、PreCompact、SessionEnd' }),
          ],
          [
            T({ en: 'Session start', zh: '对话开始时' }),
            T({ en: 'Inject profile, memory index, skills; archive overview on resume/compact', zh: '注入画像、记忆索引、技能；恢复或压缩后加归档摘要' }),
            T({ en: 'Same injection; on startup and clear, also sweeps and commits sessions left behind', zh: '同样注入；startup 和 clear 时还会清扫并提交遗留的 Session' }),
          ],
          [
            T({ en: 'Session end', zh: '对话结束时' }),
            T({ en: 'SessionEnd commits through a background worker', zh: 'SessionEnd 由后台 worker 提交' }),
            T({ en: 'SessionEnd on graceful exit (Codex 0.145+), with an end marker and a detached worker; otherwise the next startup sweep', zh: '正常退出时触发 SessionEnd（Codex 0.145 起），写结束标记并交给后台 worker；否则由下次启动的清扫兜底' }),
          ],
          [
            T({ en: 'MCP transport', zh: 'MCP 传输机制' }),
            T({ en: 'Local stdio proxy to /mcp', zh: '本地 stdio 代理转发到 /mcp' }),
            T({ en: 'Local stdio proxy to /mcp', zh: '本地 stdio 代理转发到 /mcp' }),
          ],
          [
            T({ en: 'Sub-agents', zh: '子 Agent 支持' }),
            T({ en: 'Each sub-agent gets its own session', zh: '每个子 Agent 拥有独立 Session' }),
            T({ en: 'No sub-agent hooks', zh: '没有子 Agent 相关的 Hook' }),
          ],
          [
            T({ en: 'Recall compression', zh: '召回压缩' }),
            T({ en: 'auto: local claude -p when available; otherwise server compression', zh: 'auto：本地 claude -p，不可用时请求服务端压缩' }),
            T({ en: 'auto: local codex exec with a small model when available; otherwise server compression', zh: 'auto：本地 codex exec 调用小模型，不可用时请求服务端压缩' }),
          ],
          [
            T({ en: 'Runtime', zh: '运行环境' }),
            'Node.js 18+',
            T({ en: 'Codex\'s bundled Node.js 22+', zh: 'Codex 自带的 Node.js 22+' }),
          ],
        ]}
      />

      <Callout type="note">
        <P>{T({
          en: <><strong>For Codex users:</strong> Since Codex 0.145, <InlineCode>/quit</InlineCode>, <InlineCode>/exit</InlineCode>, a double <InlineCode>Ctrl+C</InlineCode>, and the end of <InlineCode>codex exec</InlineCode> fire SessionEnd. Codex gives that hook only a few seconds, so the plugin writes an end marker and lets a detached worker do the commit. A closed terminal, <InlineCode>kill</InlineCode>, or a crash fires nothing. For those, the next Codex startup or <InlineCode>/clear</InlineCode> sweeps the leftovers: sessions with an end marker whose commit failed, and sessions idle for more than 30 minutes. <InlineCode>/resume</InlineCode> never sweeps. After an upgrade that adds a hook, approve it in <InlineCode>/hooks</InlineCode>, or it silently never runs.</>,
          zh: <>对于 Codex 用户：从 Codex 0.145 起，<InlineCode>/quit</InlineCode>、<InlineCode>/exit</InlineCode>、连按两次 <InlineCode>Ctrl+C</InlineCode> 以及 <InlineCode>codex exec</InlineCode> 运行结束，都会触发 SessionEnd。Codex 只给这个 Hook 几秒钟，所以插件先写一个结束标记，再交给 detach 出去的 worker 完成提交。直接关掉终端、<InlineCode>kill</InlineCode> 或进程崩溃，则什么都不会触发；这时由下一次启动 Codex 或执行 <InlineCode>/clear</InlineCode> 来清扫：带结束标记但没提交成功的 Session，以及闲置超过 30 分钟的 Session，都会在这时补交。<InlineCode>/resume</InlineCode> 不做清扫。升级后如果新增了 Hook，记得在 <InlineCode>/hooks</InlineCode> 里批准，否则它会一直静默不执行。</>,
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="advanced">{T({ en: 'Advanced Tuning and Security', zh: '高级调优与安全设计' })}</H2>

      <P>{T({
        en: 'Plugin behavior can be tuned with environment variables, or set under the plugin section of ~/.openviking/ovcli.conf; environment variables win:',
        zh: '插件行为可以用环境变量调整，也可以写进 ~/.openviking/ovcli.conf 的 plugin 段，环境变量优先：',
      })}</P>

      <Pre lang="bash" filename="~/.zshrc">{`# Recall
export OPENVIKING_AUTO_RECALL=true            # false keeps capture and on-demand tools only
export OPENVIKING_SCORE_THRESHOLD=0.35        # results below this relevance are not injected
export OPENVIKING_RECALL_MAX_TOKENS=1600      # token budget for the server-assembled block
export OPENVIKING_RECALL_COMPRESS=auto        # off | client | server | auto
export OPENVIKING_RECALL_PEER_SCOPE=all       # actor: current project + user-level only

# Capture
export OPENVIKING_CAPTURE_MODE=semantic       # semantic (every turn) or keyword (trigger-based)
export OPENVIKING_CAPTURE_ASSISTANT_TURNS=true  # include replies and tool I/O
export OPENVIKING_COMMIT_TOKEN_THRESHOLD=20000  # commit when pending content passes this

# Debug and bypass
export OPENVIKING_DEBUG=true                  # logs: ~/.openviking/logs/cc-hooks.log
export OPENVIKING_BYPASS_SESSION_PATTERNS='/tmp/**,**/scratch/**'  # no memory reads/writes here`}</Pre>

      <P>{T({
        en: 'A repository can carry its own settings in .openviking/config.json, which the team commits, and .openviking/config.local.json, which stays private. The example below gives a directory that is not a Git repository its own project identity and limits recall to that project plus user-level memory. Connection fields such as url, api_key, account, and user are stripped from these files, so a committed config cannot redirect your credentials.',
        zh: '仓库也可以带自己的设置：团队提交的 .openviking/config.json，以及不提交的 .openviking/config.local.json。下面的例子给一个非 Git 目录指定项目身份，并把召回范围限定为当前项目加用户级记忆。url、api_key、account、user 这类连接字段会从这些文件里剔除，提交到仓库的配置无法改写你的凭证去向。',
      })}</P>

      <Pre lang="json" filename=".openviking/config.json">{`{
  "version": 1,
  "peer": { "id": "checkout-service" },
  "recall": { "peer_scope": "actor" },
  "bypass": { "session_patterns": ["**/fixtures/**"] }
}`}</Pre>

      <H3 id="security">{T({ en: 'Security by Design', zh: '安全设计' })}</H3>

      <Ul>
        <Li><Strong>{T({ en: 'Credentials stay out of project config', zh: '凭证不进项目配置' })}</Strong>{T({
          en: ': the bundled stdio MCP proxy reads your API key at runtime from ovcli.conf or OPENVIKING_* variables, with no shell wrapper. The key is never written into .mcp.json, so it does not end up in the repository. It does live in ~/.openviking/ovcli.conf on your machine, so protect that file.',
          zh: '：插件自带的 stdio MCP 代理在运行时从 ovcli.conf 或 OPENVIKING_* 环境变量读取 API Key，不需要 shell wrapper。Key 不会写进 .mcp.json，也就不会随仓库提交。它仍然保存在本机的 ~/.openviking/ovcli.conf 里，这个文件要自己保护好。',
        })}</Li>
        <Li><Strong>{T({ en: 'Self-pollution prevention', zh: '防止记忆自污染' })}</Strong>{T({
          en: ': before a turn is captured, the plugin strips <openviking-context>, <system-reminder>, <relevant-memories>, and [Subagent Context] blocks, so answers built from recalled memory are not stored back as new knowledge. This prevents the feedback loop; it does not stop a wrong statement in a reply from being extracted, so fix or forget bad memories when you see them.',
          zh: '：每轮对话被捕获之前，插件会清洗掉 <openviking-context>、<system-reminder>、<relevant-memories> 和 [Subagent Context] 这些注入块，防止“用记忆生成的回答”又被当成新知识存进去。它切断的是回灌循环；回复里如果有错误结论，仍可能被提取，发现了就修正或 forget 掉。',
        })}</Li>
        <Li><Strong>{T({ en: 'Filter before storing', zh: '存储前过滤' })}</Strong>{T({
          en: <>: capture filters are sed-style rules applied to every turn before it is sent. A rule like <InlineCode>{'s/\\b(sk|ghp)_[A-Za-z0-9_-]+/[redacted]/g'}</InlineCode> redacts tokens; bypass patterns keep throwaway directories out of memory entirely.</>,
          zh: <>：capture filters 是 sed 风格的规则，在每轮内容发送前执行。比如 <InlineCode>{'s/\\b(sk|ghp)_[A-Za-z0-9_-]+/[redacted]/g'}</InlineCode> 可以把令牌替换掉；bypass 规则则让临时实验目录完全不读写记忆。</>,
        })}</Li>
        <Li><Strong>{T({ en: 'Sub-agent session isolation', zh: '子 Agent 会话隔离' })}</Strong>{T({
          en: ': each sub-agent writes to its own session derived from the parent\'s, and its captured messages carry the parent workspace\'s peer, so they stay in the right project without cluttering the main timeline. Separate sessions separate records; they are not separate users.',
          zh: '：每个子 Agent 写入由父会话派生的独立 Session，捕获的消息带着父工作区的 peer，既落在正确的项目里，又不会污染主干对话。独立 Session 隔开的是记录，不是用户权限。',
        })}</Li>
        <Li><Strong>{T({ en: 'One bot, many people', zh: '一个 Bot 服务多个人' })}</Strong>{T({
          en: ': if one deployment serves several real people, use the actor recall scope with an explicit peer per person. Recall for one person then draws on that person\'s peer plus the shared user-level memory. A peer is a recall scope inside one user, not an authorization boundary: people who need permissions of their own should get separate user identities and keys, or the host service has to enforce access control.',
          zh: '：如果一套部署要服务多个真实的人，用 actor 召回范围，并为每个人显式指定 peer。这样给某个人召回时，只取这个人的 peer 记忆和共享的用户级记忆。peer 是一个 user 内部的召回范围，不是鉴权边界：需要各自独立权限的人，应该使用各自的 user 身份和 Key，或者由宿主服务负责访问控制。',
        })}</Li>
      </Ul>

      <H3 id="troubleshooting">{T({ en: 'Troubleshooting', zh: '排障线索' })}</H3>

      <P>{T({
        en: <>Start with the bundled doctor. Ask Claude Code to check the plugin, which runs the <InlineCode>ov-memory-doctor</InlineCode> skill, or run the script directly. It checks the install, which config file won, the connection and auth, and recent hook activity, and prints a fix for each finding. <InlineCode>/ov</InlineCode> shows the server, identity, and last recall, and the status line under the input box reads <InlineCode>OV ✓</InlineCode>, <InlineCode>⚠ slow</InlineCode>, <InlineCode>✗ offline</InlineCode>, or <InlineCode>⚡ bypass</InlineCode>.</>,
        zh: <>先跑自带的诊断：直接让 Claude Code 检查插件（它会调用 <InlineCode>ov-memory-doctor</InlineCode> 技能），或者手动运行脚本。它会检查安装状态、生效的是哪个配置文件、连接与认证、最近的 Hook 活动，并为每个问题给出修复建议。<InlineCode>/ov</InlineCode> 可以看服务、身份和最近一次召回；输入框下方的状态栏会显示 <InlineCode>OV ✓</InlineCode>、<InlineCode>⚠ slow</InlineCode>、<InlineCode>✗ offline</InlineCode> 或 <InlineCode>⚡ bypass</InlineCode>。</>,
      })}</P>

      <Pre lang="bash" filename="terminal">{`# Claude Code
node "$(jq -r '.plugins["openviking-memory@openviking"][0].installPath' ~/.claude/plugins/installed_plugins.json)/scripts/ov-memory-doctor.mjs"

# Codex
node "$(ls -d ~/.codex/plugins/cache/openviking/openviking-memory/*/ | sort -V | tail -1)scripts/ov-memory-doctor.mjs"`}</Pre>

      <Table
        headers={[
          T({ en: 'Symptom', zh: '现象' }),
          T({ en: 'Where to look', zh: '先看哪里' }),
        ]}
        rows={[
          [T({ en: 'Plugin does nothing', zh: '插件完全没反应' }), T({ en: 'No ovcli.conf or ov.conf was found, so the plugin stays disabled. Create one, or set OPENVIKING_MEMORY_ENABLED=1 with URL and key variables.', zh: '没找到 ovcli.conf 或 ov.conf，插件处于静默关闭状态。创建配置文件，或设置 OPENVIKING_MEMORY_ENABLED=1 并提供地址和 Key。' })],
          [T({ en: 'Codex: MCP works, nothing is remembered', zh: 'Codex：MCP 能用，但什么都没记住' }), T({ en: 'Hooks are untrusted or disabled. Check /hooks and /plugins.', zh: 'Hook 没有被信任或被关闭，检查 /hooks 和 /plugins。' })],
          [T({ en: 'Recall is always empty', zh: '召回总是空的' }), T({ en: 'Server down or wrong URL: curl <url>/health. Or the threshold filters everything out.', zh: '服务没起来或地址不对：curl <url>/health；或者阈值把结果全过滤掉了。' })],
          [T({ en: 'Commits happen, no memories appear', zh: '提交了，却没有新记忆' }), T({ en: 'Read memory_diff.json in the archive; if extraction never ran, check the server\'s embedding and VLM config.', zh: '先看归档里的 memory_diff.json；如果提取根本没执行，检查服务端的 Embedding 和 VLM 配置。' })],
          [T({ en: '401 / 403', zh: '401 / 403' }), T({ en: 'API key, account, or user mismatch; a root key cannot read tenant data.', zh: 'API Key、account 或 user 不匹配；root key 不能读写租户数据。' })],
          [T({ en: 'Anything else', zh: '其他问题' }), T({ en: 'Set OPENVIKING_DEBUG=1 and read ~/.openviking/logs/cc-hooks.log.', zh: '设置 OPENVIKING_DEBUG=1，查看 ~/.openviking/logs/cc-hooks.log。' })],
        ]}
      />

      <Hr ornament />

      <H2 id="vs-native">{T({ en: 'Why Not Just Use MEMORY.md?', zh: '为什么不直接用内置的 MEMORY.md？' })}</H2>

      <P>{T({
        en: <>The OpenViking plugin complements native memory; it does not replace it. Claude Code actually has two kinds of local memory, and they behave differently. <InlineCode>CLAUDE.md</InlineCode> holds instructions you write, and Codex's <InlineCode>AGENTS.md</InlineCode> plays the same role. Auto memory is notes Claude keeps on its own: a <InlineCode>MEMORY.md</InlineCode> index plus topic files, which Claude reads and writes during the session. The <A href={CLAUDE_MEMORY_DOC}>Claude Code memory docs</A> describe both.</>,
        zh: <>OpenViking 插件的定位是补充，不是替代原生记忆。Claude Code 的本地记忆其实有两类，行为并不一样：<InlineCode>CLAUDE.md</InlineCode> 是人写的指令，Codex 的 <InlineCode>AGENTS.md</InlineCode> 也属于这一类；auto memory 是 Claude 自己记的笔记，由一个 <InlineCode>MEMORY.md</InlineCode> 索引加若干 topic 文件组成，Claude 在会话里会自己读写。两者的细节见 <A href={CLAUDE_MEMORY_DOC}>Claude Code 官方文档</A>。</>,
      })}</P>

      <Table
        headers={[
          T({ en: 'Dimension', zh: '维度' }),
          T({ en: 'CLAUDE.md / AGENTS.md (instructions)', zh: 'CLAUDE.md / AGENTS.md（指令文件）' }),
          T({ en: 'Claude Code auto memory', zh: 'Claude Code auto memory' }),
          T({ en: 'OpenViking Plugin', zh: 'OpenViking 记忆插件' }),
        ]}
        rows={[
          [
            T({ en: 'What it holds', zh: '存什么' }),
            T({ en: 'Rules and conventions a person writes', zh: '人写的规则和项目规范' }),
            T({ en: 'Notes Claude takes: a MEMORY.md index plus one topic file per memory', zh: 'Claude 记下的笔记：MEMORY.md 索引，每条记忆一个 topic 文件' }),
            T({ en: 'A file tree on the server, a vector index, and typed memory files', zh: '服务端的文件树、向量索引和分类型的记忆文件' }),
          ],
          [
            T({ en: 'How it is read', zh: '读取方式' }),
            T({ en: 'Loaded in full at session start', zh: '会话开始时整份载入' }),
            T({ en: 'Only the beginning of MEMORY.md at session start; topic files read on demand', zh: '会话开始只载入 MEMORY.md 的开头部分，topic 文件按需读取' }),
            T({ en: 'Searched by relevance every turn and injected within a token budget', zh: '每轮按语义相关性检索，在 token 预算内注入' }),
          ],
          [
            T({ en: 'Scope', zh: '作用范围' }),
            T({ en: 'Travels with the repository, or sits in your user directory', zh: '跟着仓库走，也可以放在用户目录' }),
            T({ en: 'This machine; worktrees and subdirectories of one Git repository share it, other machines do not', zh: '本机；同一个 Git 仓库的 worktree 和子目录共用一份，不跨机器' }),
            T({ en: 'Across projects, sessions, machines, and clients; project memory keyed by repository', zh: '跨项目、跨会话、跨设备、跨客户端；项目记忆按仓库归属' }),
          ],
          [
            T({ en: 'How it is written', zh: '写入方式' }),
            T({ en: 'You edit it', zh: '人来编辑' }),
            T({ en: 'Claude writes it during the session; you can edit it too', zh: 'Claude 在会话中自动写，人也可以改' }),
            T({ en: 'Extracted and merged by an LLM after each commit, or written on purpose with remember', zh: '每次提交后由 LLM 提取、合并，也可以用 remember 主动写入' }),
          ],
          [
            T({ en: 'Auditing', zh: '可审计性' }),
            T({ en: 'Reviewed like code', zh: '可以像代码一样审阅' }),
            T({ en: 'Plain local files you can open and edit', zh: '本地文件，可以直接打开查看和修改' }),
            T({ en: 'memory_diff.json records every commit\'s changes', zh: 'memory_diff.json 记录每次提交带来的变化' }),
          ],
        ]}
      />

      <P>{T({
        en: <>Keep using <InlineCode>CLAUDE.md</InlineCode> or <InlineCode>AGENTS.md</InlineCode> for rules the project must follow: they are simple, reviewable, and travel with the repository. Auto memory is good at notes that stay on one machine and one repository. For experience that grows with time and has to carry across machines, clients, and projects, such as "how did I get around that intermittent crash last Tuesday?" or "how do I usually wrap Axios?", let OpenViking do the bookkeeping.</>,
        zh: <>把 <InlineCode>CLAUDE.md</InlineCode> 或 <InlineCode>AGENTS.md</InlineCode> 用来定义“当前项目必须遵守的规范”依然是好主意：它们简单、可审阅、跟着仓库走。auto memory 适合留在本机、本仓库里的笔记。但“上周那个偶发 Crash 是怎么解决的”“我习惯用哪种风格封装 Axios”这类随时间生长、要跨机器、跨客户端、跨项目复用的经验，交给 OpenViking 自动打理更合适。</>,
      })}</P>

      <Hr ornament />

      <H2 id="conclusion">{T({ en: 'Conclusion', zh: '结语' })}</H2>

      <P>{T({
        en: 'With OpenViking, your coding agent stops being a stateless tool that forgets everything when the window closes, and becomes a pair programmer that learns your habits and accumulates your experience:',
        zh: '接入 OpenViking 后，Coding Agent 从“用完即走、过目即忘的无状态工具”，变成一个熟悉你的习惯、积累你的经验的结对伙伴：',
      })}</P>

      <Ul>
        <Li><Strong>{T({ en: 'Auto-Accumulate', zh: '自动积累' })}</Strong>{T({ en: ': debugging sessions and decisions are captured and distilled after each commit, with no manual upkeep.', zh: '——踩过的坑和做过的决定，在提交后自动沉淀，不用手动维护。' })}</Li>
        <Li><Strong>{T({ en: 'Recall on Demand', zh: '按需召回' })}</Strong>{T({ en: ': relevant history appears in context when the task calls for it, without a warm-up briefing.', zh: '——相关的历史在需要时出现在上下文里，不用每次预热。' })}</Li>
        <Li><Strong>{T({ en: 'Cross-Platform', zh: '多端共享' })}</Strong>{T({ en: ': on one server and one identity, Claude Code, Codex, and any MCP client share the same memory.', zh: '——同一个服务、同一个身份下，Claude Code、Codex 和任何 MCP 客户端共用一份记忆。' })}</Li>
        <Li><Strong>{T({ en: 'Continuous Evolution', zh: '持续进化' })}</Strong>{T({ en: ': new conversations merge into and correct old memories, and memory_diff.json records each change.', zh: '——新对话会合并、修正旧记忆，memory_diff.json 记下每一次变化。' })}</Li>
      </Ul>

      <P>{T({
        en: 'If you are tired of re-onboarding your AI assistant every time you open a new terminal, give OpenViking a try.',
        zh: '如果你已经厌倦了每次打开新窗口都要重新“调教”AI，不妨现在就试试 OpenViking。',
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
      zh: '一行命令为 Claude Code 和 Codex 接入 OpenViking：对话自动捕获、提炼成长期记忆，按需召回，并在客户端之间共享。',
      en: 'One command gives Claude Code and Codex long-term memory powered by OpenViking: conversations are captured and distilled, recalled when relevant, and shared across clients.',
    },
    cover: '/assets/covers/openviking-coding-agent.png',
    publishedAt: '2026-05-20',
    updatedAt: '2026-10-04',
    readingTime: { zh: 15, en: 19 },
    category: { zh: '工程', en: 'Engineering' },
    tags: ['openviking', 'claude-code', 'codex', 'mcp', 'memory'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [{ name: 'tosaki', github: 't0saki', role: { en: 'Engineer', zh: '工程师' } }],
  },
};
