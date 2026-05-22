import React from 'react';
import {
  A, Article, Callout, Figure, H2, H3, Lead, P, Pre, Quote, Table,
} from '../../blog-components';

const SOURCE_URL = 'https://my.oschina.net/u/6210722/blog/19667020';
const INFOQ_URL = 'https://www.infoq.cn/article/CWa1OBVphAdE6wgxPJlA';
const BOT_README = 'https://github.com/volcengine/OpenViking/blob/main/bot/README_CN.md';
const WEREWOLF_DEMO = 'https://github.com/volcengine/OpenViking/blob/main/bot/demo/werewolf/README.md';
const CLAUDE_PLUGIN_DOC = 'https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md';
const MCP_DOC = 'https://github.com/volcengine/OpenViking/blob/main/docs/en/guides/06-mcp-integration.md';
const LLM_PATH = '/post/vikingbot-memory-game/llm.txt';
const IMAGE_DIR = '/post/vikingbot-memory-game/images';
const COVER = '/assets/covers/vikingbot-memory-game.png';

const image = (index) => `${IMAGE_DIR}/figure-${String(index).padStart(2, '0')}.jpg`;

const RETRIEVED_MEMORY = `<memory index="1" type="summary">
  <uri>viking://user/player_4/memories/events/2026/04/13/sheriff-campaign.md</uri>
  <content>Player 4 has challenged sheriff-campaign claims before and tends to mark vague role claims as suspicious.</content>
</memory>

<memory index="2" type="entity">
  <uri>viking://user/player_3/memories/entities/game-character.md</uri>
  <content>Player 3 often speaks forcefully, jumps into the sheriff race, and uses hunter identity pressure when the table is chaotic.</content>
</memory>`;

const BUSINESS_TENANCY = `hr-platform/
├── resources/                 # HR-wide documents and workflows
├── agent/
│   ├── approve/               # approval assistant memories and skills
│   └── qa/                    # HR Q&A assistant memories and skills
└── user/
    ├── bob/agent/approve/     # Bob memory inside the approval assistant
    └── rock/agent/qa/         # Rock memory inside the Q&A assistant

legal-platform/
├── resources/
├── agent/
│   ├── approve/
│   └── qa/
└── user/`;

const PERSONAL_ASSISTANT_TENANCY = `personal-assistant/
├── resources/                 # shared documents and workflows
├── user/
│   ├── bob/memories/          # Bob global personal memory
│   └── rock/memories/
└── agent/
    ├── design/user/bob/       # Bob memory for the design assistant
    ├── code/user/bob/
    └── review/user/bob/`;

const INSTALL_SNIPPET = `pip install "openviking[bot]"
openviking-server --with-bot
ov chat`;

const DEMO_SNIPPET = `python start_werewolf_demo.py --config ~/.openviking/ov.conf`;

const ReferenceFigure = ({
  T, index, alt, caption, size = 'lg', frame = 'frame',
}) => (
  <Figure
    src={image(index)}
    alt={T(alt)}
    caption={T(caption)}
    size={size}
    frame={frame}
  />
);

const VikingBotMemoryPost = ({ t }) => {
  const T = t;

  return (
    <Article>
      <Lead>{T({
        en: 'The werewolf demo is useful because it turns agent memory into something you can watch. Once VikingBot players can carry history across games, they stop acting like isolated chatbots: they remember styles, reuse incidents, hide roles with evidence, and punish old patterns.',
        zh: '狼人杀 demo 的价值不在于游戏本身，而在于它把 agent 记忆变成了可观察现象：当 VikingBot 能跨局带着历史继续玩，它们就不再像一组临时 ChatBot，而会记住玩家风格、复用历史事件、用证据伪装身份，并反制对手的旧套路。',
      })}</Lead>

      <Callout type="info" title={T({ en: 'Source and rewrite boundary', zh: '来源与改写边界' })}>
        <P>{T({
          en: 'This post is a rewritten OpenViking Blog version of the public article by the ByteDance Viking Team. It keeps the experiment, figures, and public numbers, adds English coverage, and connects the demo to Claude Code memory, case memories, and production tenancy patterns.',
          zh: '本文基于字节跳动 Viking 团队公开文章重写，保留实验、截图和公开数据，同时补上英文版，并把 demo 和 Claude Code 记忆、case memories、生产多租户落地串起来。',
        })} <A href={SOURCE_URL}>{T({ en: 'OSCHINA source', zh: 'OSCHINA 原文' })}</A>{T({ en: ' / ', zh: ' / ' })}<A href={INFOQ_URL}>InfoQ</A></P>
      </Callout>

      <H2 id="experiment-setup">{T({ en: 'The Experiment: Six Agents, Two Memory Conditions', zh: '实验：六个 Agent，两种记忆条件' })}</H2>

      <P>{T({
        en: 'The setup deliberately keeps the game small. Six VikingBot players join one werewolf table. Players 1, 2, and 3 are connected to OpenViking and keep long-term cross-game memory. Players 4, 5, and 6 only see the current game context.',
        zh: '这个实验刻意保持小规模：六个 VikingBot 坐在同一张狼人杀桌上。1、2、3 号接入 OpenViking，能保留跨局长期记忆；4、5、6 号只使用当前对局上下文。',
      })}</P>

      <Table
        headers={[T({ en: 'Group', zh: '组别' }), T({ en: 'Players', zh: '玩家' }), T({ en: 'Memory condition', zh: '记忆条件' })]}
        rows={[
          [T({ en: 'Experiment', zh: '实验组' }), T({ en: 'VikingBot players 1, 2, and 3', zh: '1、2、3 号 VikingBot' }), T({ en: 'OpenViking memory, available across games', zh: '接入 OpenViking，支持跨局长期记忆' })],
          [T({ en: 'Control', zh: '对照组' }), T({ en: 'VikingBot players 4, 5, and 6', zh: '4、5、6 号 VikingBot' }), T({ en: 'Only short-term context inside the current game', zh: '只保留当前对局短期上下文' })],
        ]}
      />

      <ReferenceFigure
        T={T}
        index={1}
        alt={{ en: 'Werewolf demo showing six player bots and the god bot control panel', zh: '狼人杀 demo 中的六个玩家 Bot 和上帝 Bot 控制台' }}
        caption={{ en: 'The god bot initializes the game, writes each player identity into private GAME.md files, and keeps public flow inside the group chat.', zh: '上帝 Bot 初始化游戏，把身份写入各玩家私有 GAME.md，并在群聊里推进公共流程。' }}
      />

      <P>{T({
        en: 'This matters because the game has two channels of truth. Public speech happens in the group chat, while hidden role and night-action information lives in each player workspace. That split is close to real agent products: a platform route delivers messages, but the agent still needs private workspace state and durable context.',
        zh: '关键在于，游戏里天然有两条信息通道：公共发言在群聊里发生，隐藏身份和夜间操作写进各玩家工作目录。这个边界很像真实 agent 产品：平台负责消息投递，agent 仍然需要私有工作区状态和可持久化上下文。',
      })}</P>

      <ReferenceFigure
        T={T}
        index={2}
        alt={{ en: 'God bot asks player 3 to complete a night action in GAME.md', zh: '上帝 Bot 要求 3 号玩家在 GAME.md 中完成夜间操作' }}
        caption={{ en: 'The god bot sends uniform public prompts, while sensitive choices such as kill targets stay in the player file.', zh: '上帝 Bot 在群里发统一口径指令，刀人目标等敏感选择只留在玩家文件中。' }}
      />

      <ReferenceFigure
        T={T}
        index={3}
        alt={{ en: 'OpenViking memory records game history and player style after a round', zh: 'OpenViking 在一局结束后记录对局历史和玩家风格' }}
        caption={{ en: 'After each game, the detailed conversation, votes, notable moves, and player styles are committed into OpenViking.', zh: '每局结束后，发言、票型、亮眼操作和玩家风格会沉淀到 OpenViking。' }}
      />

      <H2 id="rounds">{T({ en: 'What Changed Across Rounds', zh: '跨局之后，行为怎么变了' })}</H2>

      <H3>{T({ en: 'Round 1: the bots learn the table', zh: '第一局：先学会这张桌子' })}</H3>
      <P>{T({
        en: 'The first game is noisy. The god bot walks players through night actions; then the sheriff race turns into a role-claiming contest. Player 1 bluffs as hunter, player 2 hides a real hunter identity behind a prophet claim, and player 3 reveals enough prophet logic to win trust.',
        zh: '第一局很乱。上帝 Bot 先按顺序推进黑夜操作；白天警长竞选很快变成身份表演：1 号狼人悍跳猎人，2 号真猎人反穿预言家，3 号真预言家给出足够清晰的逻辑后拿到信任。',
      })}</P>

      <ReferenceFigure
        T={T}
        index={4}
        alt={{ en: 'Night flow where the god bot asks each player to read and update GAME.md', zh: '黑夜阶段，上帝 Bot 逐个要求玩家读取和更新 GAME.md' }}
        caption={{ en: 'The first night exercises the delivery and private-state mechanics before any long-term memory can help.', zh: '第一晚主要验证消息投递和私有状态机制，此时长期记忆还没来得及发挥作用。' }}
      />

      <ReferenceFigure
        T={T}
        index={5}
        alt={{ en: 'Sheriff campaign with several bots role-claiming', zh: '警长竞选中多个 Bot 进行身份表演' }}
        caption={{ en: 'Early speech is tactical but not yet historical: the bots reason from current claims rather than cross-game evidence.', zh: '早期发言已有策略，但还没有历史性：它们主要基于当前发言推理，而不是跨局证据。' }}
      />

      <ReferenceFigure
        T={T}
        index={6}
        alt={{ en: 'End of the first game where wolves win quickly', zh: '第一局狼人快速获胜的结算画面' }}
        caption={{ en: 'The first game ends quickly after key good-side roles are removed, but it gives OpenViking material to capture.', zh: '第一局在关键好人角色出局后很快结束，但它为 OpenViking 提供了可沉淀素材。' }}
      />

      <ReferenceFigure
        T={T}
        index={7}
        alt={{ en: 'Memory profile for player 1 after the first game', zh: '第一局结束后 1 号玩家的记忆画像' }}
        caption={{ en: 'The important write is not the full transcript. OpenViking distills reusable claims such as style, tendencies, incidents, and strategy outcomes.', zh: '真正重要的不是完整聊天记录，而是把风格、倾向、事件和策略结果蒸馏成后续可用的记忆。' }}
      />

      <H3>{T({ en: 'Round 2: memory becomes usable evidence', zh: '第二局：记忆开始变成证据' })}</H3>
      <P>{T({
        en: 'By the second round, the OpenViking-backed players start acting on previous-game facts. One player hides a true prophet identity because a previous early role reveal got punished. Another recognizes player 3 as the forceful hunter-style speaker from history. A werewolf even uses a previous civilian event to defend a fake prophet claim.',
        zh: '第二局开始，接入 OpenViking 的玩家开始使用上一局事实：有人因为记得过早跳身份会被集火，所以把真预言家身份藏成平民；有人认出 3 号历史上的“刚猛猎人”风格；甚至有狼人用上一局自己平民站边的事件，来给这一局的悍跳预言家身份找背书。',
      })}</P>

      <ReferenceFigure
        T={T}
        index={8}
        alt={{ en: 'Player 1 hides a real prophet identity after learning from a previous game', zh: '1 号玩家基于上一局记忆隐藏真预言家身份' }}
        caption={{ en: 'The behavior change is concrete: memory changes when to reveal, when to abstain, and how to survive a chaotic sheriff race.', zh: '行为变化很具体：记忆改变了何时跳身份、何时弃票、如何在混乱警长竞选中活下来。' }}
      />

      <ReferenceFigure
        T={T}
        index={9}
        alt={{ en: 'Player 2 references player 3 style from memory', zh: '2 号玩家根据记忆识别 3 号玩家风格' }}
        caption={{ en: 'Style memory lets a bot treat a current speech as part of a player pattern, not just as one isolated utterance.', zh: '风格记忆让 Bot 把当前发言看成一个玩家模式的一部分，而不是孤立的一句话。' }}
      />

      <ReferenceFigure
        T={T}
        index={10}
        alt={{ en: 'Player 1 memory shifts from aggressive role-claiming to cautious hiding', zh: '1 号玩家画像从冲锋跳身份变成谨慎隐藏身份' }}
        caption={{ en: 'The profile itself evolves: a failed or successful tactic becomes future steering context.', zh: '画像会更新：失败或成功策略都会变成下一局的行为约束。' }}
      />

      <ReferenceFigure
        T={T}
        index={11}
        alt={{ en: 'Player 3 uses a previous event as cover while bluffing', zh: '3 号狼人用上一局事件为悍跳身份做掩护' }}
        caption={{ en: 'This is the first dangerous part of long-term memory: it can support better reasoning, but it also supports better deception.', zh: '这是长期记忆的第一处危险能力：它能支撑更好推理，也能支撑更好伪装。' }}
      />

      <ReferenceFigure
        T={T}
        index={12}
        alt={{ en: 'Another screenshot from the round where historical events support a fake role claim', zh: '历史事件支持伪装身份的对局截图' }}
        caption={{ en: 'The current claim becomes more persuasive because it is attached to a remembered incident.', zh: '当前发言因为挂上了历史事件，可信度看起来更高。' }}
      />

      <H3>{T({ en: 'Round 3: profiles turn into strategy', zh: '第三局：画像开始变成策略' })}</H3>
      <P>{T({
        en: 'After multiple games, memory no longer looks like a note-taking feature. It becomes a strategic asset. The bots remember who pushes hard, who bluffs, who tends to trust specific lines of reasoning, and which endgame moves failed before.',
        zh: '多局之后，记忆就不再像“做笔记”功能，而变成策略资产。Bot 会记住谁喜欢强势带队、谁经常悍跳、谁容易相信某类逻辑，以及哪种残局操作之前失败过。',
      })}</P>

      <ReferenceFigure
        T={T}
        index={13}
        alt={{ en: 'A wolf almost wins by reusing the hidden-role survival strategy', zh: '狼人复用隐藏身份策略并几乎获胜' }}
        caption={{ en: 'A wolf can reuse a previous survival tactic, win trust, and still lose because a final vote hits the hunter trigger.', zh: '狼人可以复用上一局的苟活策略、拿到信任，但仍可能在最后投错猎人时被反杀。' }}
      />

      <ReferenceFigure
        T={T}
        index={14}
        alt={{ en: 'Player 3 keeps reinforcing a forceful hunter persona', zh: '3 号玩家不断强化刚猛猎人人设' }}
        caption={{ en: 'A repeated style becomes an identity signal. Other bots can learn it; the player bot can also lean into it.', zh: '重复风格会变成身份信号：其他 Bot 能学会识别，玩家 Bot 也能主动强化。' }}
      />

      <ReferenceFigure
        T={T}
        index={15}
        alt={{ en: 'Historical memory helps question player 4 role claims', zh: '历史记忆帮助质疑 4 号玩家的身份发言' }}
        caption={{ en: 'History makes suspicion more targeted: a bot can challenge a role claim because this player has made similar unstable claims before.', zh: '历史让怀疑更有靶点：Bot 能因为某玩家过去有类似乱跳记录而质疑当前身份发言。' }}
      />

      <ReferenceFigure
        T={T}
        index={16}
        alt={{ en: 'Win-rate curve during memory initialization', zh: '记忆初始化阶段的胜率曲线' }}
        caption={{ en: 'During the initial memory collection phase, win rates remain close enough that memory has not yet separated the players.', zh: '记忆初始化阶段，胜率还没有明显拉开。' }}
      />

      <ReferenceFigure
        T={T}
        index={17}
        alt={{ en: 'Win-rate curve after memory collection', zh: '记忆收集完成后的胜率曲线' }}
        caption={{ en: 'After memory accumulates, OpenViking-backed bots show a visible win-rate lift in the reference experiment.', zh: '记忆沉淀一定轮次后，接入 OpenViking 的 Bot 在参考实验里出现明显胜率提升。' }}
      />

      <Quote cite={T({ en: 'Why the demo is interesting', zh: '这个 demo 为什么有价值' })}>
        {T({
          en: 'The visible behavior is not “the bot remembers the transcript.” It is that remembered incidents start changing risk, trust, role claims, and endgame strategy.',
          zh: '可观察的现象不是“Bot 记住了聊天记录”，而是历史事件开始改变风险判断、信任分配、身份表演和残局策略。',
        })}
      </Quote>

      <H2 id="memory-architecture">{T({ en: 'How OpenViking Turns Chat Into Agent-Usable Memory', zh: 'OpenViking 如何把聊天变成 Agent 可用记忆' })}</H2>

      <P>{T({
        en: 'VikingBot works because OpenViking is not a raw transcript bucket. It gives the agent a filesystem-like context surface, a staged retrieval model, and memory types that separate player identity, user preference, incidents, cases, tools, and skills.',
        zh: 'VikingBot 能成立，是因为 OpenViking 不是原始聊天记录桶。它给 agent 暴露的是类似文件系统的上下文界面、分层检索模型，以及能区分玩家身份、用户偏好、事件、案例、工具和技能的记忆类型。',
      })}</P>

      <Table
        headers={[T({ en: 'Memory type', zh: '记忆类型' }), T({ en: 'Typical path', zh: '典型路径' }), T({ en: 'Meaning', zh: '含义' })]}
        rows={[
          ['soul', 'agent/memories/soul.md', T({ en: 'Core truths, boundaries, style, and continuity for the agent.', zh: 'Agent 的核心原则、边界、风格和连续性。' })],
          ['identity', 'agent/memories/identity.md', T({ en: 'Name, persona, role, and stable presentation details.', zh: '名字、身份、人设和稳定展示信息。' })],
          ['cases', 'agent/memories/cases/', T({ en: 'Problem-to-solution case memories. This is where repeated fixes and operational lessons become reusable.', zh: '问题到解决方案的案例记忆。重复修复和操作经验会变成可复用案例。' })],
          ['patterns', 'agent/memories/patterns/', T({ en: 'Workflow and methodology memories.', zh: '工作流和方法论记忆。' })],
          ['tools / skills', 'agent/memories/tools/', T({ en: 'Tool usage, skill execution, success rate, and best-practice hints.', zh: '工具使用、技能执行、成功率和最佳实践。' })],
          ['profile / preferences', 'user/memories/profile.md', T({ en: 'User profile, preferences, entities, and event history.', zh: '用户画像、偏好、实体和事件历史。' })],
        ]}
      />

      <P>{T({
        en: 'The werewolf demo uses the same separation in a playful setting. A player has GAME.md for private current-round state, SOUL.md for behavioral rules, and OpenViking memories for durable history. In a coding-agent product, the parallel is a CLAUDE.md or AGENTS.md style instruction file plus durable memory that can survive beyond one repository or one terminal session.',
        zh: '狼人杀 demo 用游戏方式展示了同一套拆分：玩家有 GAME.md 存当前局私有状态，有 SOUL.md 约束行为规则，再用 OpenViking 承载长期历史。放到 coding agent 产品里，对应的是 CLAUDE.md / AGENTS.md 这类项目指令文件，再加上一层能跨仓库、跨终端 session 存活的长期记忆。',
      })}</P>

      <ReferenceFigure
        T={T}
        index={18}
        alt={{ en: 'OpenViking memory extraction ReAct flow', zh: 'OpenViking 记忆提取 ReAct 流程' }}
        caption={{ en: 'OpenViking prefetches existing memory URIs, lets the model decide what to read, and then writes new or updated memory through a patch-like flow.', zh: 'OpenViking 先预取已有记忆 URI，再让模型判断是否读取完整内容，最后通过类似 patch 的流程新建或更新记忆。' }}
      />

      <Pre lang="xml" filename="retrieved-memory.xml">{RETRIEVED_MEMORY}</Pre>

      <P>{T({
        en: 'L0 / L1 / L2 is the token discipline behind this. The agent starts from summaries and URIs; only when it needs proof does it read the full L2 content. That is why the system can keep long-term memory useful without dragging an entire history into every prompt.',
        zh: 'L0 / L1 / L2 是这里的 token 纪律。Agent 先拿摘要和 URI；只有需要证据时才读取完整 L2 内容。这样长期记忆才不会变成每轮 prompt 的巨大负担。',
      })}</P>

      <ReferenceFigure
        T={T}
        index={19}
        alt={{ en: 'Semantic memory filenames inside OpenViking', zh: 'OpenViking 中语义化命名的记忆文件' }}
        caption={{ en: 'Semantic filenames are part of the interface. A file path can tell the model whether it is looking at an event, a player entity, a tool lesson, or a case.', zh: '语义化文件名本身就是接口：路径会告诉模型这是事件、玩家实体、工具经验还是案例。' }}
      />

      <H2 id="claude-and-cases">{T({ en: 'The Same Pattern Shows Up in Claude Code and Case Memories', zh: '同一模式也会出现在 Claude Code 和 case memories 里' })}</H2>

      <P>{T({
        en: 'The Claude Code memory plugin is the non-game version of the same idea. Local files such as CLAUDE.md, AGENTS.md, or MEMORY.md are still useful: they are close to the workspace and easy for a human to edit. OpenViking adds the layer those files do not solve well: semantic recall across projects, automatic capture after turns, compaction-safe handoff, and on-demand MCP tools for search, read, store, list, grep, and forget.',
        zh: 'Claude Code memory plugin 是这个模式的非游戏版本。CLAUDE.md、AGENTS.md、MEMORY.md 这类本地文件仍然有价值：它们离工作区近，方便人改。OpenViking 补的是这些文件不擅长的部分：跨项目语义召回、回合结束后的自动沉淀、compaction 前后的安全交接，以及 search/read/store/list/grep/forget 等 MCP 工具。',
      })} <A href={CLAUDE_PLUGIN_DOC}>{T({ en: 'Claude Code memory plugin', zh: 'Claude Code memory plugin' })}</A>{T({ en: ' / ', zh: ' / ' })}<A href={MCP_DOC}>{T({ en: 'MCP guide', zh: 'MCP 接入指南' })}</A></P>

      <Table
        headers={[T({ en: 'Layer', zh: '层' }), T({ en: 'What it should hold', zh: '应该存什么' }), T({ en: 'What should not happen', zh: '不应该发生什么' })]}
        rows={[
          [T({ en: 'Local instruction file', zh: '本地指令文件' }), T({ en: 'Project-specific rules, code style, commands, and team conventions.', zh: '项目规则、代码风格、常用命令和团队约定。' }), T({ en: 'Do not turn it into an unbounded diary.', zh: '不要把它写成无限增长的流水账。' })],
          [T({ en: 'OpenViking memory', zh: 'OpenViking 记忆' }), T({ en: 'Distilled facts, preferences, incidents, cases, and reusable patterns.', zh: '蒸馏后的事实、偏好、事件、案例和可复用模式。' }), T({ en: 'Do not blindly upload every raw transcript back into recall.', zh: '不要把每段原始 transcript 无脑回灌进召回。' })],
          [T({ en: 'MCP tools', zh: 'MCP 工具' }), T({ en: 'Explicit search, read, store, delete, and health operations over viking:// resources.', zh: '围绕 viking:// resource 的显式搜索、读取、存储、删除和健康检查。' }), T({ en: 'Do not leak server credentials into browser or public repo surfaces.', zh: '不要把服务端凭证泄露到浏览器或公开仓库。' })],
        ]}
      />

      <P>{T({
        en: 'Case memories are especially important. A case is not just “a fact about the user.” It captures a problem, what was tried, what finally worked, and why it worked. In the werewolf demo, a case is a failed or successful play. In software work, a case can be a production incident, a tricky API integration, or a reviewer preference that changes future PRs.',
        zh: 'case memory 尤其重要。它不是“关于用户的一条事实”，而是一次问题、尝试、最终解法和生效原因。狼人杀里，case 是一次失败或成功打法；软件开发里，case 可以是一次线上事故、一个难接的 API，或一次会影响未来 PR 的 review 偏好。',
      })}</P>

      <H2 id="evaluation">{T({ en: 'Evaluation: Accuracy Rises, Token Cost Falls', zh: '评测：准确率上升，Token 成本下降' })}</H2>

      <P>{T({
        en: 'The reference article also reports LoCoMo long-context dialogue results. Native OpenClaw reaches roughly 24% accuracy. OpenClaw with OpenViking Plugin 2.0 reaches roughly 80% with far lower token use. VikingBot reaches the same accuracy band while cutting token cost further.',
        zh: '参考文章还给出了 LoCoMo 长程对话评测。原生 OpenClaw 准确率约 24%；接入 OpenViking Plugin 2.0 后约 80%，Token 成本显著下降；VikingBot 深度集成后准确率保持在同一档，Token 成本继续降低。',
      })}</P>

      <Table
        headers={[T({ en: 'System', zh: '方案' }), T({ en: 'Accuracy', zh: '准确率' }), T({ en: 'Token cost', zh: 'Token 成本' })]}
        rows={[
          ['Native OpenClaw', T({ en: 'About 24% (+/- 3%)', zh: '约 24%（上下浮动 3%）' }), T({ en: 'About 390M', zh: '约 3.9 亿' })],
          ['OpenClaw + OpenViking Plugin 2.0', T({ en: 'About 80% (+/- 3%)', zh: '约 80%（上下浮动 3%）' }), T({ en: 'About 35M', zh: '约 3500 万' })],
          ['VikingBot', T({ en: 'About 80% (+/- 3%)', zh: '约 80%（上下浮动 3%）' }), T({ en: 'About 21M', zh: '约 2100 万' })],
        ]}
      />

      <P>{T({
        en: 'The lesson is not “remember everything.” The lesson is that retrieval must be selective, layered, and inspectable. A good memory system should make the next prompt smaller and more grounded, not larger and more mysterious.',
        zh: '这里的结论不是“记得越多越好”，而是检索必须有选择、有层级、可检查。好的记忆系统应该让下一轮 prompt 更小、更有依据，而不是更大、更难解释。',
      })}</P>

      <H2 id="production">{T({ en: 'Production Use Cases: Tenancy, Channels, and Governance', zh: '生产落地：租户、渠道和治理' })}</H2>

      <P>{T({
        en: 'The demo uses six game players, but the production version of the problem is broader. A single OpenViking-backed server may need to serve HR assistants, legal assistants, code agents, review agents, and personal assistants at the same time. Memory must be reusable inside the right boundary and isolated outside it.',
        zh: '狼人杀里是六个玩家，但真实生产问题更大：同一套 OpenViking-backed server 可能同时服务 HR 助手、法务助手、代码 agent、review agent 和个人助手。记忆要能在正确边界内复用，也要在边界外隔离。',
      })}</P>

      <H3>{T({ en: 'Case 1: one server, multiple business lines', zh: '案例一：一个 Server 服务多条业务线' })}</H3>
      <P>{T({
        en: 'The account boundary separates businesses such as HR and Legal. Resources can be shared inside one business line, while user memories stay separated per user and per agent.',
        zh: 'account 边界可以隔离 HR、法务等业务线。某条业务线内部可以共享 resources；每个用户在每个 agent 下的记忆仍然隔离。',
      })}</P>
      <Pre lang="text" filename="tenant-layout.txt">{BUSINESS_TENANCY}</Pre>

      <H3>{T({ en: 'Case 2: one personal assistant platform, many users and agents', zh: '案例二：个人助手平台服务多用户和多 Agent' })}</H3>
      <P>{T({
        en: 'A personal-assistant service can let multiple agents reuse a user-level preference memory while still keeping each agent workspace clean. That is the difference between memory sharing and memory leakage.',
        zh: '个人助手平台可以让多个 agent 复用用户级偏好记忆，同时保持每个 agent 工作空间清楚。这里的关键是区分“记忆共享”和“记忆泄漏”。',
      })}</P>
      <Pre lang="text" filename="assistant-layout.txt">{PERSONAL_ASSISTANT_TENANCY}</Pre>

      <P>{T({
        en: 'VikingBot adds the channel side of this: one Bot Server Gateway can receive messages from channels such as Feishu, Slack, Discord, Telegram, email, or OpenAPI, then map them into shared, per-channel, or per-session sandboxes.',
        zh: 'VikingBot 补上了渠道侧：一个 Bot Server Gateway 可以接收飞书、Slack、Discord、Telegram、邮件或 OpenAPI 等渠道消息，再映射到 shared、per-channel 或 per-session sandbox。',
      })}</P>

      <H2 id="try-it">{T({ en: 'Try It', zh: '如何上手' })}</H2>

      <P>{T({
        en: 'The shortest path is to install the Bot extension, start OpenViking with bot support, and enter the chat CLI.',
        zh: '最短路径是安装 Bot 扩展，启动带 Bot 的 OpenViking Server，然后进入命令行对话。',
      })}</P>

      <Pre lang="bash" filename="terminal">{INSTALL_SNIPPET}</Pre>

      <P>{T({
        en: 'To run the werewolf demo, use the demo script from the OpenViking repository after preparing your OpenViking config.',
        zh: '要复现狼人杀 demo，可以在准备好 OpenViking 配置后运行仓库里的 demo 脚本。',
      })}</P>

      <Pre lang="bash" filename="terminal">{DEMO_SNIPPET}</Pre>

      <ReferenceFigure
        T={T}
        index={20}
        alt={{ en: 'Werewolf demo interface after startup', zh: '狼人杀 demo 启动后的界面' }}
        caption={{ en: 'The demo includes the table view, memory browser, leaderboard, and replay flow.', zh: 'demo 包含对局桌面、记忆目录、排行榜和回放能力。' }}
      />

      <ReferenceFigure
        T={T}
        index={21}
        alt={{ en: 'Werewolf demo with a human participant option', zh: '支持真人参与的狼人杀 demo 界面' }}
        caption={{ en: 'The demo can also keep a human seat, which makes the memory and privacy boundaries easier to test.', zh: 'demo 也可以保留真人席位，方便测试记忆与隐私边界。' }}
      />

      <P><A href={BOT_README}>{T({ en: 'VikingBot README', zh: 'VikingBot README' })}</A>{T({ en: ' explains the full Bot setup. ', zh: '提供完整 Bot 配置。' })}<A href={WEREWOLF_DEMO}>{T({ en: 'The werewolf demo README', zh: '狼人杀 demo README' })}</A>{T({ en: ' contains the runnable game setup.', zh: '包含可运行的游戏配置。' })}</P>

      <Callout type="note" title={T({ en: 'Bottom line', zh: '一句话总结' })}>
        <P>{T({
          en: 'VikingBot shows what happens when agent memory becomes an operational substrate. It is not only recall; it is evidence, strategy, governance, and a reusable context plane.',
          zh: 'VikingBot 展示的是 agent 记忆成为运行底座之后会发生什么：它不只是召回，而是证据、策略、治理和可复用的 context plane。',
        })}</P>
      </Callout>
    </Article>
  );
};

export default {
  id: 'vikingbot-memory-game',
  Component: VikingBotMemoryPost,
  meta: {
    title: {
      en: 'VikingBot: When Agent Memory Starts Changing Strategy',
      zh: 'VikingBot：当 Agent 记忆开始改变策略',
    },
    description: {
      en: 'A bilingual rewrite of the VikingBot werewolf experiment, showing how OpenViking turns multi-agent chat history into durable, inspectable, strategy-changing memory.',
      zh: '从 VikingBot 狼人杀实验看 OpenViking 如何把多 Agent 聊天历史沉淀成可追溯、可检索、会改变策略的长期记忆。',
    },
    cover: COVER,
    cardCover: COVER,
    publishedAt: '2026-05-22',
    updatedAt: '2026-05-22',
    readingTime: { en: 15, zh: 16 },
    category: { en: 'Agent Memory', zh: 'Agent 记忆' },
    tags: ['openviking', 'vikingbot', 'agent-memory', 'multi-agent'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    sourceUrl: SOURCE_URL,
    sourceTitle: '局中局！给 Agent 装上 OpenViking，它们竟然学会了“记仇”和“伪装”？',
    sourceUpdatedAt: '2026-05-13',
    authors: [{ name: 'ByteDance Viking Team' }],
  },
};
