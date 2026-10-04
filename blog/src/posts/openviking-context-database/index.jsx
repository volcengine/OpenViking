import React from 'react';
import {
  Article, Lead, P, H2, H3, H4, Pre, Quote, Pull, Callout, Hr,
  Cols, Col, Ol, Li, Ul, Table, A, InlineCode, Strong, Tag, Mark,
} from '../../blog-components';

const LLM_PATH = '/post/openviking-context-database/llm.txt';
const GITHUB_URL = 'https://github.com/volcengine/OpenViking';
const DOCS_URL = 'https://docs.openviking.ai/';
const QUICKSTART_URL = 'https://docs.openviking.ai/zh/getting-started/02-quickstart';
const OPENCLAW_GUIDE_URL = 'https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-ZH.md';
const ARCH_POST = '/post/openviking-context-database-architecture';
const CODING_AGENT_POST = '/post/openviking-coding-agent';
// Keep table columns readable on phones; .b-table-wrap already scrolls horizontally.
const TABLE_STYLE = '.ov-readable-tables .b-table th, .ov-readable-tables .b-table td { min-width: 8em; }';

const panel = {
  border: '1px solid var(--th-line)',
  borderLeft: '3px solid var(--th-accent)',
  borderRadius: 6,
  background: 'var(--th-bg-2)',
  padding: '16px 18px',
};

const miniLabel = {
  color: 'var(--th-mute)',
  fontFamily: 'var(--th-font-mono)',
  fontSize: 11,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
  marginBottom: 4,
};

function MiniGrid({ items }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(13rem, 1fr))', gap: 12, marginTop: 12 }}>
      {items.map(([label, text]) => (
        <div key={label} style={{ borderTop: '1px solid var(--th-line)', paddingTop: 10, minWidth: 0 }}>
          <div style={miniLabel}>{label}</div>
          <div style={{ fontSize: 14, lineHeight: 1.55 }}>{text}</div>
        </div>
      ))}
    </div>
  );
}

function PrimitiveCard({ t, item }) {
  return (
    <article style={{ ...panel, margin: '14px 0' }}>
      <Tag>{item.name}</Tag>
      <H4 toc={false}>{t(item.summary)}</H4>
      <P>{t(item.description)}</P>
      <MiniGrid items={[
        [t({ en: 'Strength', zh: '优点' }), t(item.advantage)],
        [t({ en: 'Limit', zh: '缺点' }), t(item.limitation)],
        [t({ en: 'OpenViking angle', zh: 'OpenViking 视角' }), t(item.angle)],
      ]} />
    </article>
  );
}

function PainCard({ t, item }) {
  return (
    <article style={{ ...panel, margin: '14px 0' }}>
      <div style={miniLabel}>{t(item.label)}</div>
      <H4 toc={false}>{t(item.title)}</H4>
      <p style={{ fontSize: 17, lineHeight: 1.5, margin: '0 0 6px', color: 'var(--th-ink)' }}>{t(item.question)}</p>
      <MiniGrid items={[
        [t({ en: 'Scenario', zh: '场景' }), t(item.context)],
        [t({ en: 'Gap', zh: '缺口' }), t(item.gap)],
        [t({ en: 'Capability needed', zh: '需要的能力' }), t(item.desired)],
      ]} />
    </article>
  );
}

function GoalGrid({ t, goals }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(11rem, 1fr))', gap: 12, margin: '20px 0' }}>
      {goals.map(goal => (
        <div key={goal.key} style={{ border: '1px solid var(--th-line)', borderRadius: 6, padding: 14, background: 'var(--th-bg-2)', minWidth: 0 }}>
          <div style={miniLabel}>{t(goal.label)}</div>
          <H4 toc={false}>{t(goal.title)}</H4>
          <div style={{ color: 'var(--th-mute)', fontSize: 14, lineHeight: 1.5 }}>{t(goal.copy)}</div>
        </div>
      ))}
    </div>
  );
}

function FormulaLine({ t, terms }) {
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center',
      borderTop: '1px solid var(--th-line)', borderBottom: '1px solid var(--th-line)',
      padding: '10px 0', margin: '18px 0',
    }}>
      {terms.map((term, index) => (
        <React.Fragment key={term.key}>
          <span style={{
            border: '1px solid var(--th-line)', borderRadius: 999, padding: '5px 12px',
            fontFamily: 'var(--th-font-mono)', fontSize: 12, color: 'var(--th-ink)',
          }}>{t(term.label)}</span>
          {index < terms.length - 1 ? <span style={{ color: 'var(--th-mute)', fontFamily: 'var(--th-font-mono)' }}>+</span> : null}
        </React.Fragment>
      ))}
    </div>
  );
}

const agiGoals = [
  {
    key: 'lifecycle',
    label: { en: 'Long lifecycle', zh: '超长生命周期' },
    title: { en: 'Work that runs for months or years', zh: '月级到年级的持续工作' },
    copy: { en: 'Agents need long-term companionship, continuous memory, and constraints that stay valid, instead of rebuilding background for every task.', zh: '智能体需要长期陪伴、连续记忆和长期有效的约束，而不是每个任务都从零开始补充背景。' },
  },
  {
    key: 'coordination',
    label: { en: 'Beyond one mind', zh: '跨越认知边界' },
    title: { en: 'Coordination at the scale of thousands', zh: '万人规模协作和跨文化任务' },
    copy: { en: 'Agents will help people orchestrate work, keep information in sync, and judge results across languages and cultures.', zh: '未来 Agent 需要帮助人协调任务编排、信息同步和结果评价，处理不同语言和文化背景下的复杂工作。' },
  },
  {
    key: 'learning',
    label: { en: 'Continuous learning', zh: '持续学习' },
    title: { en: 'Domain understanding that keeps growing', zh: '领域认知不断加强' },
    copy: { en: 'An agent should research over long periods, track progress, and build an ever stronger model of a specific domain.', zh: '智能体要能长期研究、跟踪进展，在特定领域沉淀越来越强的认知结构。' },
  },
  {
    key: 'constraint',
    label: { en: 'Constrained action', zh: '行动约束' },
    title: { en: 'Self-checks, SLAs, and cooperation', zh: '自省、SLA/SLO 与合作能力' },
    copy: { en: 'For work with compliance, time, and quality standards, an agent must constrain its own actions and turn commitments into checkable workflows.', zh: '面对有合规、时间和质量标准的任务，Agent 必须能约束自身行动，把承诺落实到可检查的工作流。' },
  },
];

const primitives = [
  {
    name: 'Prompt Engineering',
    summary: { en: 'Write role, background, rules, and output goals into the prompt; every form of context engineering starts here.', zh: '用提示词把角色、背景、规则和输出目标写给模型，是所有上下文工程的起点。' },
    description: { en: 'A prefix prompt or template hands the model the role definition, task background, rules, and output goals it needs, mostly as plain text.', zh: '通过前缀提示词或提示词模板，把模型完成任务所需的角色定义、任务背景、执行规则、输出目标等，主要以纯文本形式提供给 LLM。' },
    advantage: { en: 'Generalizes and activates model capability: one general model can handle many data-processing scenarios by changing the prompt.', zh: '泛化和激活了 LLM 能力：训练好一个通用模型后，调整提示词就能解决各类场景的数据处理问题。' },
    limitation: { en: 'Prompts are hard to write and version. Once tasks stop being simple, upkeep grows fast, and it becomes hard to tell whether an improvement came from the prompt or from the rest of the system.', zh: '提示词编写和版本管理复杂。任务不再简单直接时，维护成本快速上升，也很难判断能力改进来自提示词还是系统其他部分。' },
    angle: { en: 'Good for activating capability, not for carrying team knowledge over time. It needs a steadier context data layer underneath.', zh: '提示词适合激活能力，但不能长期承载团队级的知识组织，需要更稳定的上下文数据层来支撑。' },
  },
  {
    name: 'RAG / KnowledgeBase',
    summary: { en: 'Retrieve private knowledge before generation so the model can work on non-public problems.', zh: '在生成前检索私有知识，让模型处理非公开领域的问题。' },
    description: { en: 'On top of prompting, one or more rounds of retrieval over a private knowledge base supply relevant facts, constraints, and examples as chunks.', zh: '在 Prompt Engineering 基础上做一轮或多轮私有知识库检索，通过命中的内容切片获得任务相关的知识、约束和示例。' },
    advantage: { en: 'Solves simple private-domain problems such as Q&A and grounded drafting, using knowledge that is current and private.', zh: '提供私有知识领域的简单问题解决能力，例如问答和参考生成，让模型用上最新的、私有的知识。' },
    limitation: { en: 'Narrow RAG is not a knowledge loop. It depends on people curating and injecting knowledge, and on continuous re-organization to keep hit rates up.', zh: '狭义 RAG 通常不是知识闭环，仍依赖人工整理和注入已有知识，需要持续编排知识以提高命中率和使用率。' },
    angle: { en: 'OpenViking works on what happens around retrieval: how material gets in, is parsed, split, summarized, and kept current.', zh: 'OpenViking 关注 RAG 前后的知识组织问题：资料如何进入、被解析、被拆分、被摘要、被更新。' },
  },
  {
    name: 'Web Search',
    summary: { en: 'Connect public search so the model can use current public information.', zh: '接入公域搜索，让模型访问最新的公开信息。' },
    description: { en: 'A search engine fetches current public information and converts results into a form LLMs and VLMs read easily, which speeds up research on public questions.', zh: '通过搜索引擎获取全网最新的公开信息，把搜索结果转换成 LLM/VLM 易读的形态，提升调研和公开领域问题的解决效率。' },
    advantage: { en: 'Real-time access to public knowledge.', zh: '集成实时的公开信息访问能力，让模型用上最新的公开知识。' },
    limitation: { en: 'Public information is unstable and risky: SEO, prompt injection, and polluted sources. Enterprise use needs screening and filtering.', zh: '公开信息不稳定且有安全风险，容易受到 SEO、提示词注入和信源污染影响，企业服务需要筛选和过滤。' },
    angle: { en: 'Search fills in outside information, but an enterprise agent still needs a trusted layer to keep, isolate, and reuse what passed the filter.', zh: 'Web Search 补足外部信息，但企业 Agent 仍需要一层可信的上下文数据库，来沉淀、隔离和复用筛选后的材料。' },
  },
  {
    name: 'Tools / MCP',
    summary: { en: 'Expose system interfaces, functions, and external actions to the model.', zh: '把系统接口、函数和外部动作暴露给模型。' },
    description: { en: 'Tool calling, in several styles, lets the model invoke system interfaces and functions, which gives it a first level of system integration.', zh: '工具调用通过多种范式把系统接口和函数实现暴露给大模型调用，提供初步的系统集成能力。' },
    advantage: { en: 'Validation, checks, constraints, and observability can live in the tool layer, so the model can act in real, complex environments.', zh: '可以在工具层加入校验、检查、约束和可观测能力，让模型初步具备与真实复杂环境交互的能力。' },
    limitation: { en: 'Complex to build and dependent on hand-wrapped functions. As tools multiply, controllable sub-steps do not add up to reliable overall decisions.', zh: '实现复杂，依赖人工包装调用函数。工具数量增加后，子流程可控不等于宏观决策可靠。' },
    angle: { en: 'Tools let an agent act; before acting it still has to know what to read, what to trust, and why to call. A context database supplies that decision material.', zh: '工具让 Agent 能行动，但行动之前仍要知道读什么、信什么、为什么调用。上下文数据库补的是这部分决策材料。' },
  },
  {
    name: 'Skills',
    summary: { en: 'Turn SOPs into files that give the agent layered procedures and tool entry points.', zh: '把 SOP 文件化，给 Agent 层次化的流程和工具入口。' },
    description: { en: 'Built on filesystem ideas, a skill writes procedures, rules, tool entry points, and how to expose context as readable files, much like a human SOP.', zh: '基于文件系统的概念设计，把流程、规则、工具入口和上下文暴露方式写成可读取的技能文件，接近人工 SOP。' },
    advantage: { en: 'Good for packaging medium and long procedures; compared with code, the model can explore within bounds based on actual conditions.', zh: '适合中长任务的流程封装和 SOP 描述，相比代码，允许模型根据实际条件做有边界的探索。' },
    limitation: { en: 'Mostly hand-written rules with little self-iteration. As the material grows, layered exposure can miss things, and invocation is not deterministic.', zh: '主要依赖规则编写，缺乏自我迭代。资料空间变大时，层次化暴露可能召回不足，调用的确定性也无法保证。' },
    angle: { en: 'Skills are how an agent reads procedures. OpenViking stores them as searchable resources with summaries, alongside a much larger body of material.', zh: 'Skills 是 Agent 读懂流程的方式。OpenViking 把技能当作可检索、带摘要的资源来管理，并给它配上更大的资料空间。' },
  },
  {
    name: 'Memory',
    summary: { en: 'Keep long-term experience, preferences, and knowledge as context that later tasks can reuse.', zh: '把长期经验、偏好和知识沉淀为后续任务可复用的上下文。' },
    description: { en: 'Borrows from how acquired learning and memory process information: summarize, compress, and reorganize so an agent\'s experience carries through its whole lifecycle.', zh: '借鉴后天学习和记忆的信息加工方式，通过摘要、压缩、重组，解决智能体全生命周期的信息沉淀和复用需求。' },
    advantage: { en: 'Lets context extend far beyond one window and supports personalization and self-improvement, so later tasks build on earlier experience.', zh: '让上下文延伸到单个窗口之外，支持自进化和个性化，后续任务可以基于前序经验改善结果。' },
    limitation: { en: 'Organizing and retrieving memories is hard. Poor recording or retrieval can make results worse, and tuning depends heavily on the application.', zh: '记忆信息的组织和检索极其复杂。记录或检索方式不合理时可能起反作用，需要结合应用场景深度调优。' },
    angle: { en: 'OpenViking provides the underlying organization, query, isolation, and lifecycle management for memory.', zh: 'OpenViking 为 Memory 提供底层的组织、查询、隔离和生命周期管理能力。' },
  },
];

const painPoints = [
  {
    label: 'AI Coding',
    title: { en: 'Cross-repository work breaks local context', zh: '跨仓库上下游串联困难' },
    question: { en: 'Can an agent connect several upstream and downstream repositories and cut the communication cost around requirements, protocols, and past implementations?', zh: 'Agent 能否串联团队上下游的多个代码仓库，减少需求、协议和历史实现的沟通成本？' },
    context: { en: 'Real engineering tasks rarely stay in the current directory. Requirements, interface protocols, past implementations, dependent services, and test scripts are spread across repositories and documents.', zh: '真实的研发任务很少只发生在当前目录。需求、接口协议、历史实现、依赖服务、测试脚本可能分散在多个仓库和文档里。' },
    gap: { en: 'Single-repository context produces changes that are locally correct but undeliverable, and people still have to sync information, explain background, and fix interface mistakes.', zh: '单仓库上下文会让 Agent 给出局部正确、整体不可交付的修改，最后仍然需要人去同步信息、解释背景、修正接口误判。' },
    desired: { en: 'Locate evidence across resources, keep directory structure, and let the agent expand from summaries to original material and code.', zh: '上下文系统需要跨资源定位证据，保留目录结构，并允许 Agent 从摘要逐步展开到原始材料和代码。' },
  },
  {
    label: 'OpenClaw',
    title: { en: 'A requirement stated yesterday is not a constraint today', zh: '刚说过的要求没有成为长期约束' },
    question: { en: 'Can an OpenClaw agent remember a constraint confirmed yesterday, instead of asking the user to supply the background again?', zh: 'OpenClaw Agent 能否记住昨天刚确认过的约束，而不是每次都要求用户重新补充背景？' },
    context: { en: 'An autonomous agent working on medium and long tasks must remember preferences, constraints, corrections, and failures.', zh: '自主智能体要完成中长周期的任务，必须记住偏好、约束、修正历史和失败经验。' },
    gap: { en: 'If memory is just chat history or a one-off summary, key constraints drop out in the next task, and the user repeats the same context.', zh: '如果记忆只是对话记录或一次性摘要，Agent 很容易在新任务里丢掉关键约束，用户只能反复补充同一批上下文。' },
    desired: { en: 'Memory organized as resources that can be searched, updated, and isolated, not as an ever longer chat log.', zh: '记忆应当被组织成可检索、可更新、可隔离的资源，而不是越来越长的聊天历史。' },
  },
  {
    label: { en: 'Knowledge', zh: '知识编排' },
    title: { en: 'Knowledge is scattered across too many sources', zh: '知识散落在太多来源里' },
    question: { en: 'When code, documents, chats, meeting notes, papers, and team standards are scattered everywhere, is the agent managing knowledge, or is the user managing the agent?', zh: '当代码仓库、协作文档、聊天记录、会议纪要、外部文献和团队标准散落各处，是 Agent 在管理知识，还是用户在管理 Agent？' },
    context: { en: 'Team knowledge is not one knowledge base. It spans files, tools, and organizations, with different formats, permissions, and update cadences.', zh: '团队知识不是单一的知识库。它跨文件、跨工具、跨组织边界存在，格式、权限和更新节奏都不同。' },
    gap: { en: 'If every task depends on someone handing over material, the agent just moves the orchestration burden onto the user, and complex tasks get more tiring.', zh: '如果每次任务都靠人手动提供资料，Agent 只是把信息编排的压力转移给了用户，复杂任务反而更累。' },
    desired: { en: 'One place that takes in many sources and offers search, summaries, hierarchical browsing, and reading on demand.', zh: '上下文数据库应当统一接入多种来源，并提供搜索、摘要、层级浏览和按需读取的能力。' },
  },
  {
    label: { en: 'Alignment', zh: '观点对齐' },
    title: { en: 'The real standard is understood too late', zh: '交付前没有充分理解人的真实标准' },
    question: { en: 'Can an agent understand what its owner actually cares about before delivery, and turn it into execution constraints?', zh: 'Agent 能否在交付前理解负责人真正关心的标准，并把这些标准转化为执行约束？' },
    context: { en: 'Much work fails because the model does not know the decision-maker\'s criteria, past preferences, and unspoken context.', zh: '很多工作失败，是因为模型没有掌握决策者的评价标准、历史偏好和上下文里的暗线。' },
    gap: { en: 'Output iterated on instinct can look complete yet miss the organization\'s context, quality bar, or specific preferences, and the risk only shows at delivery.', zh: '只凭感觉迭代出来的结果可能表面完整，却和组织语境、质量标准或具体偏好错位，交付时才暴露风险。' },
    desired: { en: 'Keep people\'s and teams\' views, standards, and past feedback, and surface the relevant constraints before a task starts.', zh: '上下文系统需要沉淀人和团队的观点、标准、历史反馈，并在任务开始前推荐相关的约束。' },
  },
];

const formulaTerms = [
  {
    key: 'constraint',
    label: { en: 'Constraints', zh: '约束' },
    title: { en: 'Reliable reasoning constraints', zh: '可靠的推理流程约束' },
    copy: { en: 'Long tasks need procedures to follow, checkpoints, and failure boundaries. Prompts, tools, and skills all contribute constraints, and constraints need to be versioned and reused.', zh: '让 Agent 在长任务里有可遵循的流程、校验点和失败边界。Prompt、Tools 和 Skills 都能贡献约束，但约束需要被版本化和复用。' },
    openviking: { en: 'OpenViking does not replace the reasoning framework. It supplies citable background, rules, cases, and checklists, and stores skills as resources.', zh: 'OpenViking 不直接替代推理框架，它为流程约束提供可被引用的背景、规则、案例和检查材料，并把技能当作资源存放。' },
  },
  {
    key: 'organization',
    label: { en: 'Organization', zh: '组织' },
    title: { en: 'Complete information organization', zh: '完整的信息组织' },
    copy: { en: 'Turn context from scattered text into data that can be added, deleted, queried, and updated, while keeping semantic relevance, hierarchy, and source boundaries.', zh: '把上下文从散乱的文本变成可以增加、删除、查询、更新的数据。组织方式要同时保留语义相关性、层级结构和来源边界。' },
    openviking: { en: 'This is OpenViking\'s main field: code, documents, images, PDFs, archives, and conversations in one resource space.', zh: '这是 OpenViking 的主战场：把代码、文档、图片、PDF、压缩包和对话历史纳入统一的资源空间。' },
  },
  {
    key: 'recommendation',
    label: { en: 'Recommendation', zh: '推荐' },
    title: { en: 'Effective context recommendation', zh: '有效的上下文推荐' },
    copy: { en: 'An agent should not swallow everything at once. At each stage it needs the most likely relevant context, then the details step by step.', zh: 'Agent 不应该一次性吞下所有材料。它需要在不同的任务阶段拿到最可能相关的上下文，再逐步展开细节。' },
    openviking: { en: 'Indexes, summaries, and directory structure give recommendation a candidate space: direction first, evidence second.', zh: 'OpenViking 的索引、摘要和目录结构为推荐提供候选空间，让 Agent 先看方向，再看证据。' },
  },
  {
    key: 'memory',
    label: { en: 'Memory', zh: '记忆' },
    title: { en: 'Full-lifecycle memory', zh: '全生命周期记忆' },
    copy: { en: 'Memory turns experience, preferences, constraints, and conclusions into resources that later tasks can find, understand, and update.', zh: '记忆要把经验、偏好、约束和结论，加工成后续任务能找到、能理解、能更新的资源。' },
    openviking: { en: 'Session commits extract typed memories that merge with existing ones, and recall brings them back within a budget.', zh: '会话提交后提取出分类型的记忆，与已有记忆合并；召回时再在预算内把它们取回来。' },
  },
  {
    key: 'learning',
    label: { en: 'Learning', zh: '学习' },
    title: { en: 'Traceable self-evolving learning', zh: '可跟踪的自进化学习' },
    copy: { en: 'The system should explain why it recommended something, and let the team correct how knowledge is organized, so the next task benefits from feedback.', zh: '系统要能解释为什么推荐这些上下文，也要允许团队修正知识的组织方式，让下一次任务从反馈中受益。' },
    openviking: { en: 'Each commit leaves memory_diff.json describing what changed. With Agent Evolution on, tasks become cases and trajectories, and reusable experience is distilled from their outcomes.', zh: '每次提交都会留下 memory_diff.json，说明记忆改了什么。开启 Agent Evolution 后，任务被组织成 case 和 trajectory，再从结果里提炼出可复用的经验。' },
  },
];

const OpenVikingContextDatabase = ({ t }) => {
  const T = t;

  return (
    <Article className="ov-readable-tables">
      <style>{TABLE_STYLE}</style>
      <Lead>{T({
        en: 'OpenViking moves context engineering beyond a loose mix of Prompt, RAG, Tools, Skills, and Memory toward a database paradigm for agents: material should be organized, indexed, summarized, recommended, remembered, and kept current, instead of a person stuffing background into the window every time.',
        zh: 'OpenViking 把上下文工程从 Prompt、RAG、Tools、Skills 和 Memory 的松散组合，推进到一个面向 Agent 的数据库范式：资料要能被组织、索引、摘要、推荐、记忆和持续更新，而不是每次都靠人把背景塞进窗口。',
      })}</Lead>

      <Quote cite={T({ en: 'A working formula for context engineering', zh: '上下文工程工作公式' })}>
        {T({
          en: 'Context engineering = reliable reasoning constraints + complete information organization + effective context recommendation + full-lifecycle memory + traceable self-evolving learning.',
          zh: '上下文工程 = 可靠的推理流程约束 + 完整的信息组织 + 有效的上下文推荐 + 全生命周期记忆 + 可跟踪的自进化学习。',
        })}
      </Quote>

      <H2 id="why-database">{T({ en: 'Why Context Engineering Becomes a Database Problem', zh: '上下文工程为什么变成数据库问题' })}</H2>

      <H3 id="status">{T({ en: 'Where context engineering stands', zh: '上下文工程发展现状与痛点背景' })}</H3>
      <P>{T({
        en: 'Context engineering has existed since the first LLM: put the information a model can read in the right place, and use it to shape the output. Prompt engineering is its earliest and lightest form. Once tasks get longer, material grows, and agents start calling tools and keeping memories, the question is no longer how to write the prompt.',
        zh: '上下文工程从 LLM 诞生时就存在：把模型能读取的信息放到合适的位置，用它影响生成结果。Prompt Engineering 是最早、最轻的形态；当任务变长、资料变多、Agent 开始调用工具并沉淀记忆，问题就不再只是提示词怎么写。',
      })}</P>
      <Quote cite={T({ en: 'OpenViking\'s view of context engineering', zh: 'OpenViking 对上下文工程的判断' })}>
        {T({
          en: 'Context engineering gives a model command of its eyes, hands, and feet: observing information, recording experience, taking action.',
          zh: '上下文工程的本质，是让大模型具备调度眼、手、脚的能力：观察信息、记录经验、采取行动。',
        })}
      </Quote>
      <Cols count={2}>
        <Col>
          <H4 toc={false}>{T({ en: 'Today', zh: '现状' })}</H4>
          <P>{T({
            en: 'Prompt, RAG, Web Search, Tools/MCP, Skills, and Memory look like separate engineering modules. From an agent\'s point of view they are all the same question: what to read next, what to believe, what to call, what to remember.',
            zh: 'Prompt、RAG、Web Search、Tools/MCP、Skills 和 Memory 看似属于不同的工程模块，但在 Agent 视角里都变成了同一个问题：下一步应该读什么、相信什么、调用什么、记住什么。',
          })}</P>
        </Col>
        <Col>
          <H4 toc={false}>{T({ en: 'The pain', zh: '痛点' })}</H4>
          <P>{T({
            en: 'When team knowledge is spread across repositories, documents, chats, meeting notes, papers, and team standards, people are forced to act as context routers. OpenViking starts by turning that routing into a database job.',
            zh: '当团队知识散落在代码仓库、协作文档、聊天记录、会议纪要、外部文献和团队标准里，人会被迫充当上下文路由器。OpenViking 的切入点，就是把这件事数据库化。',
          })}</P>
        </Col>
      </Cols>
      <P>{T({
        en: 'The long-term goals pull in the same direction. Each of them assumes context that outlives a single window:',
        zh: '长期目标也指向同一个方向。下面每一项，都默认上下文能活过单个窗口：',
      })}</P>
      <GoalGrid t={T} goals={agiGoals} />
      <Callout type="note" title={T({ en: 'From the end goal back to today', zh: '从终局目标回到近期问题' })}>
        <P>{T({
          en: 'The goals are large, but the problem already shows up in four near-term scenarios: cross-repository coding, long-term memory for OpenClaw, orchestrating knowledge from many sources, and aligning an agent with the people it works for.',
          zh: '长期目标很宏大，但问题已经落在四类近期场景里：跨仓库编码、OpenClaw 的长期记忆、多来源的知识编排，以及人与 Agent 的观点对齐。',
        })}</P>
      </Callout>

      <H3 id="primitives">{T({ en: 'Prompt, RAG, Web Search, Tools, Skills, and Memory', zh: 'Prompt / RAG / Web Search / Tools / Skills / Memory 对比' })}</H3>
      <P>{T({
        en: 'These six capabilities do not replace one another layer by layer. They are different entrances into an agent\'s context system. Together they hand information, rules, actions, and experience to the model, and each has its own boundary and failure mode.',
        zh: '这六类能力并不是逐层替代的关系。它们是 Agent 上下文系统里的不同入口，共同把信息、规则、动作和经验交给模型，但各自的边界和失败模式不同。',
      })}</P>
      {primitives.map(item => <PrimitiveCard key={item.name} t={T} item={item} />)}

      <H3 id="pain-points">{T({ en: 'Four near-term pain points', zh: '四个中短期痛点：上下文能力不足怎样发生' })}</H3>
      <P>{T({
        en: 'Coming back from long-term goals to daily work, insufficient context already shows up in AI coding, OpenClaw, team knowledge, and management communication.',
        zh: '从 AGI 的长期目标回到日常工作，上下文能力不足已经出现在 AI Coding、OpenClaw、团队知识和管理沟通里。',
      })}</P>
      {painPoints.map(item => <PainCard key={item.title.en} t={T} item={item} />)}
      <Ul marker="check">
        <Li>{T({ en: 'What these share: context sources, structure, recall, and memory cannot reliably serve the task.', zh: '这些问题的共同点在于：上下文的来源、结构、召回和记忆无法稳定地服务任务。' })}</Li>
        <Li>{T({ en: 'When people must explain background again and again, the cost of arranging information eats the gains of automation.', zh: '当人需要反复解释背景时，Agent 带来的自动化收益会被信息编排成本抵消。' })}</Li>
        <Li>{T({ en: 'OpenViking\'s answer: turn context into data first, then let agents read and update it through a stable interface.', zh: 'OpenViking 的答案是先把上下文变成数据，再让 Agent 通过稳定的接口读取和更新。' })}</Li>
      </Ul>

      <H3 id="formula">{T({ en: 'The formula: five conditions that form one system', zh: '上下文工程公式：五个条件组成一个系统' })}</H3>
      <P>{T({
        en: 'Context engineering can be reduced to a working formula. To finish long tasks reliably, an agent needs procedural constraints, information organization, context recommendation, lifecycle memory, and traceable learning at the same time.',
        zh: '上下文工程可以收敛为一个工作公式。Agent 要稳定完成长任务，需要同时具备流程约束、信息组织、上下文推荐、生命周期记忆和可跟踪的学习。',
      })}</P>
      <FormulaLine t={T} terms={formulaTerms} />
      <Table
        headers={[
          T({ en: 'Condition', zh: '条件' }),
          T({ en: 'What it requires', zh: '它要求什么' }),
          T({ en: 'Where OpenViking sits', zh: 'OpenViking 在公式中的位置' }),
        ]}
        rows={formulaTerms.map(term => [<Strong>{T(term.title)}</Strong>, T(term.copy), T(term.openviking)])}
      />
      <P>{T({
        en: 'Read the five terms together and the database shape appears. Any one of them can be patched together with a script or a vector store. Recommendation, though, needs an index over whatever organization exists; memory writes back into the same store that recommendation reads; learning needs updates and deletes that leave a record; and once several people and agents share the store, every read and write needs an identity. Adding, querying, indexing, isolating by identity, and tracking change is what databases are for.',
        zh: '把五项放在一起看，数据库的形状就出来了。其中任何一项单独做，都可以用一个脚本或一个向量库凑出来。但推荐要建立在组织之上的索引；记忆要写回推荐读取的同一个存储；学习要求更新和删除留下记录；一旦多个人、多个 Agent 共用这份存储，每次读写都得带着身份。增删查改、建索引、按身份隔离、追踪变更，正是数据库要解决的事。',
      })}</P>
      <Callout type="tip" title={T({ en: 'Where OpenViking fits', zh: 'OpenViking 的定位' })}>
        <P>{T({
          en: <>OpenViking mainly provides <Mark>complete information organization</Mark>, and serves as the infrastructure for <Mark>effective context recommendation</Mark> and <Mark>full-lifecycle memory</Mark>. The formula turns "why OpenViking" from a single feature request into an infrastructure question: does your team's agent rely on people stuffing in material, or on a context layer that can be queried, updated, and traced?</>,
          zh: <>OpenViking 主要提供<Mark>完整的信息组织</Mark>方案，并作为<Mark>有效的上下文推荐</Mark>与<Mark>全生命周期记忆</Mark>的基础设施。这个公式把“为什么需要 OpenViking”从单点功能需求，提升为基础设施问题：团队的 Agent 是靠人临时塞资料，还是已经有一层可查询、可更新、可追踪的上下文？</>,
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="design">{T({ en: 'OpenViking\'s Design Philosophy and Technical Model', zh: 'OpenViking 的设计理念与技术原理' })}</H2>

      <H3 id="organization">{T({ en: 'Information organization: context is not object storage', zh: '信息组织形态：上下文不是普通对象存储' })}</H3>
      <P>{T({
        en: 'Context comes from code, documents, images, meetings, and conversations, and cannot be stored only by object attributes. OpenViking uses a semantic index to find an entry point, then directories, metadata, and links to support exploration.',
        zh: '上下文来自代码、文档、图片、会议和对话，不能只按对象属性存。OpenViking 先用语义索引找到入口，再用目录、元信息和链接支持 Agent 探索。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Form', zh: '组织形态' }),
          T({ en: 'How it expresses context', zh: '表达方式' }),
          T({ en: 'Strength', zh: '优势' }),
          T({ en: 'Limit', zh: '限制' }),
        ]}
        rows={[
          [<Strong>Vector Index</Strong>, T({ en: 'Maps text, images, code, PDFs, and conversations into one semantic space and finds related context by similarity.', zh: '把文本、图片、代码、PDF 和对话映射到同一个语义空间，用相似度找相关上下文。' }), T({ en: 'Works without a fixed schema; finds an entry point when the agent does not know the keywords.', zh: '适合没有固定 Schema 的资料，能让 Agent 在不知道关键词时先找到入口。' }), T({ en: 'Weak at hard filters, exact enumeration, and explaining relationships; needs directories, metadata, or links alongside.', zh: '不擅长强过滤、精确枚举和关系解释，需要目录、元数据或关系层配合。' })],
          [<Strong>Graph</Strong>, T({ en: 'People, projects, documents, repositories, concepts, and events as nodes; references, dependencies, and ownership as edges.', zh: '把人、项目、文档、仓库、概念和事件建成节点，把引用、依赖和归属建成边。' }), T({ en: 'Explains relationships and follows leads; complements semantic retrieval.', zh: '适合解释关系、追踪线索，可以补充语义检索。' }), T({ en: 'Costly to model and maintain; multimodal content is hard to turn into a stable graph. Better as a supporting layer.', zh: '建模和维护成本高，多模态内容难以稳定抽图，更适合作为辅助层。' })],
          [<Strong>File System</Strong>, T({ en: 'Directories, paths, file names, and globs express ownership, hierarchy, and reading order.', zh: '用目录、路径、文件名和 glob 表达归属、层级和阅读顺序。' }), T({ en: 'Low learning cost; fits agentic reading with ls, tree, read, and overview.', zh: '学习门槛低，适合 ls、tree、read、overview 这类 Agentic 阅读路径。' }), T({ en: 'Directories alone do not solve semantic recall or large-scale relevance ranking.', zh: '目录本身不解决语义召回，也不适合大规模的相关性排序。' })],
          [<Strong>Table</Strong>, T({ en: 'Rows, columns, fields, DSLs, and indexes organize enumerable attributes.', zh: '用行列、字段、DSL 和索引组织可枚举的属性。' }), T({ en: 'Strong filtering on definite conditions such as time, author, type, and permission.', zh: '筛选能力强，适合时间、作者、类型、权限这类确定条件。' }), T({ en: 'Fields must be defined up front; the messier the material, the more the modeling cost falls back on users.', zh: '需要预先定义字段；资料越杂，建表成本越容易回到用户身上。' })],
        ]}
      />

      <H3 id="tradeoffs">{T({ en: 'No silver bullet across dimensions', zh: '不同维度下没有银弹' })}</H3>
      <P>{T({
        en: 'Vectors, graphs, file systems, and tables each win somewhere. The table below records OpenViking\'s design tradeoffs and the conditions behind them. It is not a performance benchmark: which form wins on a given dimension depends on data volume, query type, and index implementation.',
        zh: '向量、图、文件系统和表格各有所长。下表记录的是 OpenViking 的设计取舍和背后的条件，不是性能评测：某个维度上谁更强，要看数据量、查询类型和索引实现。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Dimension', zh: '维度' }),
          T({ en: 'Usually stronger', zh: '通常更擅长' }),
          T({ en: 'Condition', zh: '前提' }),
          T({ en: 'OpenViking\'s choice', zh: 'OpenViking 的取舍' }),
        ]}
        rows={[
          [T({ en: 'Semantic relevance', zh: '语义相关性' }), T({ en: 'Vector index', zh: '向量索引' }), T({ en: 'Wording differs while meaning is close; quality depends on the embedding model.', zh: '表达不同、意思接近的场景；效果取决于 Embedding 模型。' }), T({ en: 'Vector indexing is automatic for everything that enters.', zh: '所有进入系统的资料都自动建向量索引。' })],
          [T({ en: 'Scale', zh: '规模适应性' }), T({ en: 'Vector index, table', zh: '向量索引、表格' }), T({ en: 'Both scale on mature index structures; graphs and directories depend more on modeling quality.', zh: '两者都靠成熟的索引结构扩展；图和目录更依赖建模质量。' }), T({ en: 'Built directly on VikingDB\'s indexing.', zh: '直接建立在 VikingDB 的索引能力之上。' })],
          [T({ en: 'Agent fit', zh: '智能体适应性' }), T({ en: 'File system, with a vector entry point', zh: '文件系统，配合向量入口' }), T({ en: 'Agents already know paths and commands; the directory tree must be meaningful.', zh: 'Agent 天然熟悉路径和命令；前提是目录本身组织得有意义。' }), T({ en: 'Vectors make things findable; the file system makes them readable and navigable.', zh: '向量负责“找得到”，文件系统负责“读得懂、走得动”。' })],
          [T({ en: 'Automatic modeling', zh: '自动化建模' }), T({ en: 'Vector index', zh: '向量索引' }), T({ en: 'Little manual labeling; the price is weaker explainability.', zh: '少人工标注；代价是可解释性较弱。' }), T({ en: 'Parsing, summaries, and indexing run in the system pipeline.', zh: '解析、摘要和索引放进系统流水线。' })],
          [T({ en: 'Modality coverage', zh: '模态通用性' }), T({ en: 'Vector index, file system', zh: '向量索引、文件系统' }), T({ en: 'Image search needs a multimodal embedding model; files hold any format.', zh: '以图搜图需要多模态 Embedding；文件可以装下任何格式。' }), T({ en: 'Convert every modality into readable context units with summaries.', zh: '把各种模态转成带摘要、Agent 可读的上下文单元。' })],
          [T({ en: 'Filtering', zh: '查询筛选能力' }), T({ en: 'Table, then file system', zh: '表格，其次是文件系统' }), T({ en: 'Fields known in advance; paths cover hierarchy and ownership.', zh: '字段事先确定；路径覆盖层级和归属。' }), T({ en: 'Paths as an indexed scope plus a small fixed set of scalar fields.', zh: '把路径做成可索引的范围，加上少量预设的标量字段。' })],
          [T({ en: 'Adding dimensions', zh: '维度扩展能力' }), T({ en: 'Table', zh: '表格' }), T({ en: 'Adding a column is easy; deciding which columns messy material needs is not.', zh: '加一列容易；为杂乱的资料决定该加哪些列不容易。' }), T({ en: 'A limited preset schema balances extensibility against usage cost.', zh: '用有限的预设 Schema 平衡扩展能力和使用成本。' })],
        ]}
      />
      <P>{T({
        en: 'OpenViking\'s compromise: vectors underneath to cover semantics and modalities, a file system on top to keep the agent\'s learning cost low, and a limited schema, URIs, and links to fill in governance.',
        zh: 'OpenViking 的折中是：底层用向量覆盖语义和模态，对外用文件系统降低 Agent 的学习成本，再用有限的 Schema、URI 和链接补齐治理。',
      })}</P>

      <H3 id="vikingdb">{T({ en: 'From VikingDB to a context database', zh: 'VikingDB 的演进背景：从向量到上下文数据库' })}</H3>
      <P>{T({
        en: 'OpenViking inherits VikingDB\'s work on semantic retrieval, scalar filtering, graph exploration, and filesystem semantics, and folds those capabilities into a data interface agents can use. The capabilities arrived in roughly this order:',
        zh: 'OpenViking 继承了 VikingDB 在语义检索、标量过滤、图谱探索和文件系统语义上的积累，并把这些能力收束成 Agent 可用的数据接口。这些能力大致按下面的顺序出现：',
      })}</P>
      <Table
        headers={[
          T({ en: 'Paradigm', zh: '组织范式' }),
          T({ en: 'Value', zh: '价值' }),
          T({ en: 'What it added', zh: '能力介绍' }),
        ]}
        rows={[
          [T({ en: 'Vectors', zh: '向量' }), T({ en: 'Semantics and relevance ranking', zh: '语义和相关性排序' }), T({ en: 'Dense, sparse, and hybrid retrieval over unstructured data; the base for semantic entry points and multimodal indexes.', zh: '从非结构化检索出发，沉淀稠密、稀疏和混合检索，为语义入口和多模态索引打底。' })],
          [T({ en: 'Tables', zh: '表格' }), T({ en: 'Efficient filtering', zh: '高效筛选过滤' }), T({ en: 'DSLs, UDFs, forward and inverted indexes, and spatial indexes add scalar filters, so semantic retrieval can combine with exact conditions.', zh: '用 DSL、UDF、正倒排和空间索引补齐标量过滤，让语义检索能组合确定条件。' })],
          [T({ en: 'Graphs', zh: '图谱' }), T({ en: 'Relationship discovery', zh: '辅助关系发现' }), T({ en: 'Entities and relations as graphs; automatic modeling is costly, so it works best as a supporting layer.', zh: '用图谱表达实体和关系，但自动建模成本较高，更适合作为关系辅助层。' })],
          [T({ en: 'File systems', zh: '文件系统' }), T({ en: 'An effective way to organize information', zh: '有效的信息组织方法' }), T({ en: 'Directory semantics, paths, and tree traversal become an agent interface, so context can be expanded, summarized, and read level by level.', zh: '把目录语义、路径和树形遍历变成 Agent 接口，让上下文可以逐级展开、摘要和阅读。' })],
        ]}
      />
      <P>{T({
        en: <>The path-aware index is the piece that makes the last row work: the path itself is an indexed type, so a query can take a whole subtree in one step. The <A href={ARCH_POST}>architecture post</A> explains how that index and the rest of the stack fit together.</>,
        zh: <>让最后一行成立的是路径感知索引：路径本身是一种被索引的类型，一次查询就能取出整棵子树。<A href={ARCH_POST}>架构那一篇</A>详细讲了这个索引和整套系统怎样拼在一起。</>,
      })}</P>

      <H3 id="principles">{T({ en: 'Design constraints: move complexity from the user to the system', zh: 'OpenViking 的设计约束：把复杂性从用户侧移到系统侧' })}</H3>
      <P>{T({
        en: 'OpenViking has five priorities: multimodal semantics, ease of use, AI friendliness, token savings, and relationship discovery. The aim is an interface agents learn easily and teams adopt easily, without giving up semantic retrieval, summaries, and relationships.',
        zh: 'OpenViking 有五个优先级：全模态语义、使用简单、AI 友好、节省 Token、关系发现。目标是让 Agent 容易学、团队容易接入，同时保留语义检索、摘要和关系表达。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Constraint', zh: '约束' }),
          T({ en: 'Consideration', zh: '考虑' }),
          T({ en: 'Approach', zh: '做法' }),
          T({ en: 'Effect', zh: '效果' }),
        ]}
        rows={[
          [<><Strong>{T({ en: 'Multimodal semantics', zh: '全模态语义' })}</Strong> <Tag>P0</Tag></>, T({ en: 'Agent context includes code, images, PDFs, web pages, meeting notes, and conversations.', zh: 'Agent 的上下文不只是文本，还包括代码、图片、PDF、网页、会议纪要和对话历史。' }), T({ en: 'Automatic vector indexing for every input; a VLM writes summaries for non-text content.', zh: '所有输入自动建向量索引；非文本内容由 VLM 写摘要。' }), T({ en: 'No modality or schema decision up front; the agent finds a starting point in natural language.', zh: '用户不必先选模态、先建表，Agent 可以用自然语言找到起点。' })],
          [<><Strong>{T({ en: 'Ease of use', zh: '使用简单' })}</Strong> <Tag>P1</Tag></>, T({ en: 'A complex schema drags a context database back into data governance.', zh: '复杂的 Schema 会把上下文数据库拉回传统的数据治理。' }), T({ en: 'A limited preset schema; parsing, summaries, and indexing in the pipeline.', zh: '保留有限的预设 Schema，把解析、摘要和索引放进系统流水线。' }), T({ en: 'Adding material is like dropping it into a resource space, not starting a data warehouse project.', zh: '添加资料更接近放进一个资源空间，而不是启动一个数据仓库项目。' })],
          [<><Strong>{T({ en: 'AI friendly', zh: 'AI 友好' })}</Strong> <Tag>P1</Tag></>, T({ en: 'Agents know paths, commands, trees, and files.', zh: 'Agent 熟悉路径、命令、目录树和文件。' }), T({ en: 'CLI, URIs, and data representation follow the filesystem paradigm; the core verbs are ls, find, tree, abstract, overview, read.', zh: 'CLI、URI 和数据表征遵循文件系统范式，核心动作收敛到 ls、find、tree、abstract、overview、read。' }), T({ en: 'The agent looks at the whole, finds an entry, expands a directory, then reads the original.', zh: 'Agent 先看全局，再定位入口，再展开目录，最后读取原始内容。' })],
          [<><Strong>{T({ en: 'Token savings', zh: '节省 Token' })}</Strong> <Tag>P2</Tag></>, T({ en: 'Long documents, repositories, and image sets cannot go into the window at once.', zh: '长文档、代码仓库和图片集合不能一次性塞进窗口。' }), T({ en: 'Preprocessing, modality conversion, and three summary levels: abstract, overview, full content.', zh: '用预处理、模态转换和三级摘要，形成摘要、概览、原始内容的展开路径。' }), T({ en: 'The model reads summaries and structure first, and long content only when needed.', zh: '模型先读摘要和结构，必要时才读长内容。' })],
          [<><Strong>{T({ en: 'Relationship discovery', zh: '关系发现' })}</Strong> <Tag>P2</Tag></>, T({ en: 'Context has references, dependencies, ownership, and links, but a full graph costs too much.', zh: '上下文有引用、依赖、归属和跳转关系，但完整图谱成本过高。' }), T({ en: 'Express the necessary relations as URI references and links inside content; a memory can cite the resource it came from.', zh: '用内容里的 URI 引用和链接表达必要的关系；记忆可以注明它引用的资源。' }), T({ en: 'Cross-resource discovery stays possible without graph extraction becoming the bottleneck.', zh: '保留跨资源发现能力，同时避免复杂抽图成为瓶颈。' })],
        ]}
      />

      <H3 id="cli">{T({ en: 'The CLI path for data, queries, skills, memory, and the bot', zh: '数据添加、查询、技能、记忆和 Bot 的 CLI 路径' })}</H3>
      <P>{T({
        en: 'The CLI is the main interface agents use to learn and call the context database. The path has five steps: ingest material with add-resource; locate entry points with ls and find; control reading depth with tree, glob, abstract, and overview; keep procedures and experience as skills and memories; and talk to the built-in bot, which reuses the same context. The paths below are examples; resource locations depend on the --to target and on the parser.',
        zh: 'CLI 是 Agent 学习和调用上下文数据库的主要界面。路径分五步：用 add-resource 接入资料；用 ls 和 find 建立资源地图和语义入口；用 tree、glob、abstract、overview 控制阅读粒度；用技能和记忆沉淀流程与经验；最后通过内置的 Bot 对话，复用同一套上下文能力。下面的路径是示例，资源实际落在哪里取决于 --to 指定的目标和所用的 Parser。',
      })}</P>
      <Pre lang="bash" filename="ingest.sh">{`# Add repositories, documents, images, folders, and archives
ov add-resource https://github.com/volcengine/OpenViking --to viking://resources/openviking
ov add-resource ./design-notes.pdf
ov add-resource ./project.docx
ov add-resource ./workshop-photo.jpg
ov add-resource ./research-photos/ --include "*.jpg,*.jpeg,*.png"
ov add-resource ./context-notes.zip

# Move, rename, delete by URI
ov mv viking://resources/notes/draft.md viking://resources/notes/2026-q3/
ov rm -r viking://resources/notes/obsolete/`}</Pre>
      <Pre lang="bash" filename="discover.sh">{`# Look at the map, then find an entry point
ov ls
ov find "How does OpenViking use VikingDB?" --uri viking://resources/openviking

# Expand structure and read summaries before full text
ov tree viking://resources/openviking/docs -L 2
ov glob "*context*" --uri viking://resources/openviking -n 10
ov abstract viking://resources/openviking
ov overview viking://resources/openviking/docs
ov read viking://resources/openviking/README.md`}</Pre>
      <Pre lang="bash" filename="skills-memory-bot.sh">{`# Skills and memories are context assets too
ov add-skill ./skills/search-web
ov find "search the web for recent papers" --uri viking://~/skills
ov add-memory "Prefer small PRs with a test plan"

# Built-in bot and observability
openviking-server --with-bot
ov chat -m "Which design notes explain OpenViking memory?"
ov status
ov observer models`}</Pre>
      <Callout type="tip" title={T({ en: 'Reading strategy for agents', zh: 'Agent 读取策略' })}>
        <P>{T({
          en: <>Find the entry with <InlineCode>ov ls</InlineCode> and <InlineCode>ov find</InlineCode>, control depth with <InlineCode>ov tree</InlineCode>, <InlineCode>ov abstract</InlineCode>, and <InlineCode>ov overview</InlineCode>, then read the original with <InlineCode>ov read</InlineCode>. The levels are a reading strategy, not a required route: retrieval can hit any level directly, and a known URI can be read straight away.</>,
          zh: <>先用 <InlineCode>ov ls</InlineCode> 和 <InlineCode>ov find</InlineCode> 找入口，再用 <InlineCode>ov tree</InlineCode>、<InlineCode>ov abstract</InlineCode>、<InlineCode>ov overview</InlineCode> 控制粒度，最后用 <InlineCode>ov read</InlineCode> 读取原始内容。分层是一种阅读策略，不是必经之路：检索可以直接命中任何一层，已知 URI 也可以直接读。</>,
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="practice">{T({ en: 'Product Boundaries and Team Adoption', zh: '从产品边界到团队落地' })}</H2>

      <H3 id="boundary">{T({ en: 'How OpenViking differs from vector databases and file systems', zh: '与向量库、文件系统的区别与联系' })}</H3>
      <P>{T({
        en: 'OpenViking is a context database for AI agents: it stores, manages, and retrieves context under one paradigm, and parses, summarizes, and indexes it automatically. It uses vector retrieval and borrows the filesystem paradigm, but exposes a fuller data-management interface to agents than either.',
        zh: 'OpenViking 是面向 AI Agent 的上下文数据库：按统一的范式存储、管理、检索上下文，并自动解析、摘要和索引。它用到向量检索，也借鉴文件系统范式，但对 Agent 暴露的是比两者都更完整的数据管理接口。',
      })}</P>
      <Cols count={3}>
        <Col>
          <H4 toc={false}>OpenViking</H4>
          <P>{T({ en: 'Manages files, text, links, conversations, and derived summaries, with CRUD, semantic retrieval, hierarchy, automatic parsing and summaries, isolation, memory extraction, and a built-in bot.', zh: '管理文件、文本、链接、对话历史和派生摘要；能力覆盖增删查改、语义检索、层次保留、自动解析和摘要、数据隔离、记忆提取和内置 Bot。' })}</P>
        </Col>
        <Col>
          <H4 toc={false}>{T({ en: 'VikingDB / vector DB', zh: 'VikingDB / 向量库' })}</H4>
          <P>{T({ en: 'Manages vectors, scalars, and content that is easy to vectorize. Strong at semantic and sparse retrieval, index scale, and managed service. A key layer underneath OpenViking, but it does not keep document hierarchy or offer reading, summaries, or memory.', zh: '管理向量、标量和容易向量化的内容，擅长语义检索、稀疏检索、规模化索引和云上托管。它是 OpenViking 的关键底层能力之一，但不会保留文档层次，也不提供阅读、摘要和记忆。' })}</P>
        </Col>
        <Col>
          <H4 toc={false}>{T({ en: 'LocalFS / object storage', zh: 'LocalFS / 对象存储' })}</H4>
          <P>{T({ en: 'Manages files with natural hierarchy: traversal, moves, renames, permissions, and original bytes. Queries rely on grep or other tools. OpenViking borrows this shape and adds semantics, summaries, parsing, and memory.', zh: '管理文件，天然保留目录层次，适合遍历、移动、重命名、权限隔离和保留原始文件，查询依赖 grep 或其他工具。OpenViking 借用了这个范式，补上了语义检索、摘要、解析和记忆能力。' })}</P>
        </Col>
      </Cols>
      <Table
        caption={T({ en: 'Product boundaries of OpenViking, a vector database, and a file system.', zh: 'OpenViking、向量库、文件系统的产品边界。' })}
        headers={[
          T({ en: 'Feature', zh: '产品特性' }),
          T({ en: 'OpenViking', zh: 'OpenViking 上下文数据库' }),
          T({ en: 'VikingDB', zh: 'VikingDB 向量库' }),
          T({ en: 'LocalFS / object storage', zh: 'LocalFS / 对象存储' }),
        ]}
        rows={[
          [T({ en: 'Operations', zh: '数据操作' }), T({ en: 'CRUD', zh: '增删查改' }), T({ en: 'CRUD', zh: '增删查改' }), T({ en: 'Create/update/delete; queries need other tools', zh: '增删改，查询依赖其他应用' })],
          [T({ en: 'Inputs', zh: '输入格式' }), T({ en: 'Files, text, links, conversations', zh: '文件、文本内容、链接、对话历史' }), T({ en: 'Vectors, scalars, easily vectorized content', zh: '向量、标量、易向量化内容' }), T({ en: 'Files', zh: '文件' })],
          [T({ en: 'Semantic retrieval', zh: '语义检索' }), T({ en: 'Yes, vector-based', zh: '是，基于向量' }), T({ en: 'Yes, vector-based', zh: '是，基于向量' }), T({ en: 'No', zh: '否' })],
          [T({ en: 'Keyword retrieval', zh: '关键词检索' }), T({ en: 'Sparse vectors (backend-dependent) or grep', zh: '稀疏向量（视后端而定）或 grep' }), T({ en: 'Sparse vectors and keyword indexes', zh: '稀疏向量和关键词索引' }), T({ en: 'grep', zh: 'grep' })],
          [T({ en: 'Hierarchy', zh: '层次结构' }), T({ en: 'Kept and traversable by agents', zh: '保留，可被 Agent 遍历' }), T({ en: 'Usually not kept', zh: '通常不保留' }), T({ en: 'Native', zh: '天然保留' })],
          [T({ en: 'Parsing and summaries', zh: '自动解析和摘要' }), T({ en: 'Automatic parsing, L0 abstracts, L1 overviews', zh: '自动解析、L0 摘要、L1 概览' }), T({ en: 'Not built in', zh: '不内置' }), T({ en: 'Not built in', zh: '不内置' })],
          [T({ en: 'Agentic reading', zh: 'Agentic 阅读' }), T({ en: 'ls / tree / find / abstract / overview / read', zh: '支持 ls/tree/find/abstract/overview/read' }), T({ en: 'Not directly', zh: '不直接支持' }), T({ en: 'Traversal without semantic processing', zh: '支持遍历，但缺少语义加工' })],
          [T({ en: 'Isolation', zh: '数据隔离' }), T({ en: 'Account, user, peer; ACLs on shared resources', zh: 'account、user、peer，共享资源可加 ACL' }), T({ en: 'Scalar fields', zh: '基于标量' }), T({ en: 'Partly, by user/group', zh: '部分基于 user/group' })],
          [T({ en: 'Built in', zh: '内置能力' }), T({ en: 'Memory extraction, agent plugins, VikingBot', zh: '记忆提取、Agent 插件、VikingBot' }), T({ en: 'None', zh: '不内置' }), T({ en: 'None', zh: '不内置' })],
          [T({ en: 'Deployment', zh: '部署形态' }), T({ en: 'Local or self-hosted; hosted service on Volcengine', zh: '本地或自托管；火山引擎提供托管服务' }), T({ en: 'Managed cloud service', zh: '云上托管' }), T({ en: 'Local or object storage', zh: '本地或对象存储' })],
          [T({ en: 'Original files', zh: '原始文件' }), T({ en: 'L2 keeps the original or the parsed body; PDFs and Office files are usually kept as parsed Markdown', zh: 'L2 保存原始文件或解析后的正文；PDF、Office 等通常以解析后的 Markdown 保存' }), T({ en: 'Not kept', zh: '不保留' }), T({ en: 'Kept', zh: '保留' })],
        ]}
      />
      <Pull>{T({
        en: 'A vector database answers "how to rank by meaning," a file system answers "how to walk the structure," and OpenViking answers "how an agent uses context as data."',
        zh: '向量库解决“语义怎么排”，文件系统解决“结构怎么走”，OpenViking 解决“Agent 如何把上下文当作数据使用”。',
      })}</Pull>

      <H3 id="documents">{T({ en: 'How a long document becomes context', zh: '长文档拆解和重组为上下文的例子' })}</H3>
      <P>{T({
        en: 'OpenViking does not have to keep a long document as one file. Depending on the parser and its settings, it splits, regroups, and summarizes the document so an agent can read it level by level.',
        zh: 'OpenViking 不必把长文档固定成一个文件。根据所用的 Parser 和配置，它会拆解、重组并建立摘要层级，让 Agent 能逐级阅读。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Stage', zh: '阶段' }),
          T({ en: 'What happens', zh: '发生了什么' }),
          T({ en: 'What the agent does', zh: 'Agent 动作' }),
        ]}
        rows={[
          [<Strong>{T({ en: 'Source document', zh: '原始长文档' })}</Strong>, T({ en: 'In a plain file system a long document is one file: read it all at once or slice it with outside tools, with no control over window use or semantic boundaries. Inputs can be docx, pdf, markdown, web pages, archives, or team folders.', zh: '在普通文件系统中，一个长文档就是单个文件：要么一次读完，要么靠外部工具切片，窗口占用和语义边界都不可控。输入可以是 docx、pdf、markdown、网页、压缩包或团队目录。' }), T({ en: 'Write it in with ov add-resource instead of pasting the text into a prompt.', zh: '先用 ov add-resource 写入资源，而不是把全文粘进提示词。' })],
          [<Strong>{T({ en: 'Chapter directories', zh: '章节子目录' })}</Strong>, T({ en: 'Chapters become subdirectories that keep order and nesting, e.g. viking://resources/docs/project/03-design/.', zh: '按章节组织成子目录，保留顺序和上下级关系，例如 viking://resources/docs/project/03-design/。' }), T({ en: 'Look at the tree with ov tree or ov ls before reading blind.', zh: '先用 ov tree 或 ov ls 理解结构，减少盲读。' })],
          [<Strong>{T({ en: 'Content modules', zh: '内容模块' })}</Strong>, T({ en: 'Chapters split into modules small enough to embed, each carrying one idea, procedure, interface, case, or decision.', zh: '章节继续拆成能直接向量化的小模块，每个模块承载一个观点、流程、接口说明、案例或决策。' }), T({ en: 'Locate with ov find, then decide with ov overview whether the full body is needed.', zh: '用 ov find 定位入口，再用 ov overview 判断要不要读完整正文。' })],
          [<Strong>{T({ en: 'Modal elements', zh: '模态元素' })}</Strong>, T({ en: 'Images can be extracted as separate resources. Tables, code blocks, and links typically remain in the parsed body and are retrieved and cited through their containing chapter or file. The exact form depends on the parser.', zh: '图片可提取为独立资源；表格、代码块和链接通常保留在解析正文中，通过所在章节或文件参与检索与引用。具体形态取决于 Parser。' }), T({ en: 'Follow the URI to the image resource or to the chapter or file containing the element.', zh: '按 URI 追到图片资源，或承载该元素的章节、文件。' })],
          [<Strong>{T({ en: 'Summary levels', zh: '摘要层级' })}</Strong>, T({ en: 'Each directory gets an abstract and an overview; read returns the full body.', zh: '每个目录都有摘要和概览，read 返回完整正文。' }), T({ en: 'Abstract first, overview next, read last; expand the full text only when evidence is insufficient.', zh: '先摘要，后概览，最后 read。证据不足时才展开全文。' })],
        ]}
      />
      <Callout type="tip" title={T({ en: 'Reading-window strategy', zh: '阅读窗口策略' })}>
        <P>{T({
          en: <>Splitting has three goals: <Strong>embeddable</Strong>, <Strong>semantically self-contained</Strong>, and <Strong>cheap on the window</Strong>. It also has a cost: a condition and its exception can land in different modules. When a decision hinges on the exact wording, read the relevant L2 body and check neighboring modules for conditions and exceptions.</>,
          zh: <>拆解的目标很明确：<Strong>可向量化</Strong>、<Strong>语义独立</Strong>、<Strong>少占窗口</Strong>。它也有代价：一个条件和它的例外可能被拆进不同的模块。决定取决于原话怎么写时，就去读相关的 L2 正文，并检查相邻模块中的条件和例外。</>,
        })}</P>
      </Callout>

      <H3 id="team">{T({ en: 'Using OpenViking to improve team AI capability', zh: '用 OpenViking 改善团队 AI 能力' })}</H3>
      <P>{T({
        en: <>How efficiently a team handles context sets the ceiling on how much it gets from AI. OpenViking connects scattered material to one context layer. Start the server in service mode and connect with the CLI; the <A href={QUICKSTART_URL}>quickstart</A> covers model setup and the hosted option, which needs no local server.</>,
        zh: <>上下文处理效率决定了团队使用 AI 的上限。OpenViking 把分散的资料接入同一个上下文层。先用服务模式启动，再用 CLI 连接；模型配置和不需要本地服务端的托管方式，见<A href={QUICKSTART_URL}>快速开始</A>。</>,
      })}</P>
      <Pre lang="bash" filename="deploy.sh">{`# Server machine
uv tool install openviking --upgrade
openviking-server init      # writes model settings to ~/.openviking/ov.conf
openviking-server doctor
openviking-server

# Client machine
npm install -g @openviking/cli
ov config                   # choose OpenViking Service or a custom URL
ov health`}</Pre>
      <Ol>
        <Li>{T({ en: 'Connect the core code repositories and stable documents first. Check point: ov health is green and an imported document is readable and searchable.', zh: '先接入核心代码仓库和稳定的文档。检查点：ov health 正常，导入的文档能读到，也能搜到。' })}</Li>
        <Li>{T({ en: 'Then add meeting notes, chat records, project write-ups, and outside material. Private repositories need server-side access and credentials first.', zh: '再接入会议纪要、聊天记录、项目沉淀和外部材料。私有仓库需要先在服务端配置访问权限和凭证。' })}</Li>
        <Li>{T({ en: 'Finally turn common SOPs into skills and repeated preferences into memories, and connect coding agents through the plugins so their sessions feed memory automatically.', zh: '最后把常用 SOP 做成 Skills，把反复出现的偏好沉淀为 Memory，并通过插件接入 Coding Agent，让它们的会话自动沉淀成记忆。' })}</Li>
        <Li>{T({ en: 'When several people share a deployment, give each a user and use ACLs on shared resources. Directory names and prompt conventions are not access control.', zh: '多人共用一套部署时，为每个人建 user，共享资源用 ACL 控制。目录命名和 prompt 约定都不是访问控制。' })}</Li>
      </Ol>
      <H4 toc={false}>{T({ en: 'Demo A: a business question that spans repositories', zh: '综合案例 A：多仓库业务技术问题' })}</H4>
      <P>{T({
        en: 'Take a question whose answer lives in several places: why does the order interface still keep its v1 path? The implementation is in one repository, the migration conditions are in a design document, and the decision to postpone was made in a conversation last month. With all three in OpenViking, the agent can search across the resources, read the design note\'s overview, open the relevant section, and find the memory that recorded the postponement and its reason. Its answer can then explain the migration plan and why v1 stays. Looking at the code alone, the obvious suggestion is "delete the compatibility branch": reasonable locally, wrong once you put it back into the plan.',
        zh: '拿一个答案分散在几处的问题来说：订单接口为什么还保留 v1 路径？实现在一个仓库里，迁移条件写在设计文档里，暂缓迁移的决定是上个月在一次对话里做的。三者都接入 OpenViking 之后，Agent 可以跨资源检索，先读设计文档的概览，再打开相关章节，同时找到记下暂缓决定和原因的那条记忆。这样它讲出来的是迁移计划和保留 v1 的原因。只看实现代码时，最顺手的建议往往是“删掉兼容分支”，局部看合理，放回计划里就错了。',
      })}</P>

      <H3 id="openclaw">{T({ en: 'OpenViking and OpenClaw memory', zh: 'OpenViking 与 OpenClaw 的最佳实践' })}</H3>
      <P>{T({
        en: 'The longer an OpenClaw task runs, the more memory matters. OpenViking turns long-term memory into context that can be managed, searched, and updated.',
        zh: 'OpenClaw 的任务周期越长，记忆问题越明显。OpenViking 把长期记忆变成可管理、可检索、可更新的上下文。',
      })}</P>
      <Table
        headers={[
          T({ en: 'OpenClaw pain point', zh: 'OpenClaw 场景痛点' }),
          T({ en: 'How OpenViking helps', zh: 'OpenViking 实践方式' }),
        ]}
        rows={[
          [T({ en: 'Preferences explained again and again', zh: '重复解释偏好' }), T({ en: 'User requirements, team norms, and task preferences become searchable memories.', zh: '把用户要求、团队规范、任务偏好沉淀为可检索的记忆。' })],
          [T({ en: 'Retries are expensive', zh: '任务重试成本高' }), T({ en: 'Session summaries and extracted memories keep what worked, so the next attempt does not start from zero.', zh: '用会话摘要和提取出的记忆保留有效经验，减少下一次从零开始。' })],
          [T({ en: 'Macro tasks keep getting longer', zh: 'OpenClaw 宏观任务变长' }), T({ en: 'OpenClaw reads long-term context from OpenViking instead of relying only on the current conversation.', zh: '让 OpenClaw 通过 OpenViking 读取长期上下文，而不是只依赖当前对话。' })],
          [T({ en: 'Team knowledge is scattered', zh: '团队知识分散' }), T({ en: 'Code, documents, meetings, chats, and outside material live in one context database.', zh: '把代码、文档、会议、聊天和外部材料放进统一的上下文数据库。' })],
        ]}
      />
      <P>{T({
        en: <>OpenViking connects to OpenClaw as a plugin, <InlineCode>@openviking/openclaw-plugin</InlineCode>, pointed at a running OpenViking server. The <A href={OPENCLAW_GUIDE_URL}>OpenClaw install guide</A> lists the supported versions and steps, and warns against a common mix-up: the skill named openviking on ClawHub is not the plugin.</>,
        zh: <>OpenViking 以插件 <InlineCode>@openviking/openclaw-plugin</InlineCode> 的形式接入 OpenClaw，连接一个已经运行的 OpenViking 服务。支持的版本和安装步骤见 <A href={OPENCLAW_GUIDE_URL}>OpenClaw 安装指南</A>；指南里也提醒了一个常见的混淆：ClawHub 上名为 openviking 的 Skill 不是这个插件。</>,
      })}</P>
      <Pre lang="bash" filename="openclaw-openviking-memory.sh">{`openclaw plugins install clawhub:@openviking/openclaw-plugin
# then point the plugin's baseUrl at your OpenViking server,
# following the install guide`}</Pre>
      <H4 toc={false}>{T({ en: 'Demo B: better memory for OpenClaw', zh: '演示 B：让 OpenClaw 具备更好的记忆' })}</H4>
      <Cols count={3}>
        <Col>
          <H4 toc={false}>{T({ en: 'Memory input', zh: '记忆输入' })}</H4>
          <P>{T({ en: 'Written explicitly with add-memory, or extracted from session summaries after a commit.', zh: '既可以用 add-memory 显式写入，也可以在会话提交后从摘要里提取。' })}</P>
        </Col>
        <Col>
          <H4 toc={false}>{T({ en: 'Memory reads', zh: '记忆读取' })}</H4>
          <P>{T({ en: 'Query memory like any other context: OpenClaw retrieves the memories relevant to the task instead of stuffing in the whole history.', zh: '像查上下文一样查记忆：OpenClaw 不需要把历史对话全塞进窗口，而是按任务检索相关的记忆。' })}</P>
        </Col>
        <Col>
          <H4 toc={false}>{T({ en: 'Practical limit', zh: '实践边界' })}</H4>
          <P>{T({ en: 'This is not unlimited chat storage. Useful memory is summarized, compressed, and reorganized, and it should be clear why it was recalled.', zh: '这不是无限保存对话。有效的记忆应该被摘要、压缩、重组，并能解释为什么被召回。' })}</P>
        </Col>
      </Cols>

      <H3 id="vikingbot">{T({ en: 'VikingBot: check context capability by talking to it', zh: 'VikingBot：用对话检查上下文能力' })}</H3>
      <P>{T({
        en: <>VikingBot is the agent embedded with OpenViking. Start the server with <InlineCode>--with-bot</InlineCode>, and <InlineCode>ov chat</InlineCode> talks to an agent that already uses the material, skills, summaries, and retrieval in OpenViking. For a team, that is the quickest way to test ingestion, retrieval quality, summary quality, and how well the context is organized, in plain language. This is Demo C.</>,
        zh: <>VikingBot 是基于 OpenViking 的内嵌智能体。用 <InlineCode>--with-bot</InlineCode> 启动服务后，<InlineCode>ov chat</InlineCode> 就能直接和一个已经用上 OpenViking 资料、技能、摘要和检索能力的 Agent 对话。对团队来说，这是用自然语言检查资料接入、检索质量、摘要质量和上下文组织效果最快的办法，也就是演示 C。</>,
      })}</P>

      <H3 id="judgments">{T({ en: 'Core judgments and roadmap', zh: '核心观点和后续规划' })}</H3>
      <H4 toc={false}>{T({ en: 'Five core judgments', zh: '五个核心判断' })}</H4>
      <Ul marker="check">
        <Li>{T({ en: 'The more complete the context an agent can reach, the higher the ceiling on what it can automate. Organization and retrieval are the precondition; without them, more data only adds noise.', zh: '接入的上下文越完整，Agent 能自动完成的工作上限越高。前提是数据被组织好、能被检索到；否则数据越多，噪声越大。' })}</Li>
        <Li>{T({ en: 'Once a team works across repositories, sessions, and tools, it should have its own context database that integrates all of its information.', zh: '一个团队一旦需要跨仓库、跨会话、跨工具协作，就应该拥有自己的上下文数据库，实现全域信息集成。' })}</Li>
        <Li>{T({ en: 'Vectors, file systems, knowledge graphs, and tables are only forms; agents need a data interface that fits the hand.', zh: '向量、文件系统、知识图谱、表格都只是形式；Agent 需要的是趁手的数据交互接口。' })}</Li>
        <Li>{T({ en: 'OpenViking is a context database designed for agents handling complex information, not just a memory component.', zh: 'OpenViking 的定位是上下文数据库，面向 Agent 处理复杂信息的场景设计，不只是一个记忆组件。' })}</Li>
        <Li>{T({ en: 'The core of future agent capability is context capability: knowledge, memory, tools, and the way they are organized.', zh: '未来智能体能力的核心是上下文能力，包括知识、记忆、工具和组织方式。' })}</Li>
      </Ul>
      <H4 toc={false}>{T({ en: 'From local validation to managed and distributed deployment', zh: '从本地验证走向托管和分布式部署' })}</H4>
      <P>{T({
        en: 'OpenViking began as something to validate locally or self-host. Since then a hosted service has launched on Volcengine, and storage can be split across remote backends with primary/backup replication. The goal is to move the context database from a personal tool to team infrastructure: clear deployment, explicit permissions, and a foundation that retrieval and recommendation can build on.',
        zh: 'OpenViking 最初适合本地或自托管验证。此后火山引擎上线了托管服务，存储也可以拆到远端后端并做主备复制。目标是把上下文数据库从个人工具推进到团队基础设施：部署清晰、权限明确、能承接检索和推荐的底座。',
      })}</P>
      <H4 toc={false}>{T({ en: 'Roadmap', zh: '后续规划' })}</H4>
      <Ol>
        <Li>{T({ en: 'Community building, and promoting standards and protocols.', zh: '社区生态建设、标准和协议推广。' })}</Li>
        <Li>{T({ en: 'Stronger single-node operations, stable releases, and smooth upgrades.', zh: '增强单机运维能力，推出稳定版本，支持平滑升级。' })}</Li>
        <Li>{T({ en: 'Better multimodal, memory, and skill retrieval, and a more complete content-understanding interface.', zh: '增强多模态、记忆和技能检索能力，打通更完整的内容理解接口。' })}</Li>
        <Li>{T({ en: 'Distributed capability with public-cloud integration and more reliable distributed consistency.', zh: '建设分布式能力，对接公有云，实现更可靠的分布式一致性。' })}</Li>
      </Ol>
      <Quote cite="OpenViking">
        {T({
          en: 'OpenViking\'s mission is to help agent technology flourish.',
          zh: 'OpenViking 的核心使命是推动智能体技术蓬勃发展。',
        })}
      </Quote>

      <Hr ornament />

      <P>{T({
        en: <>Resources: <A href={GITHUB_URL}>OpenViking on GitHub</A>, <A href={DOCS_URL}>docs.openviking.ai</A>, <A href={ARCH_POST}>the architecture deep dive</A>, and <A href={CODING_AGENT_POST}>memory plugins for Claude Code and Codex</A>.</>,
        zh: <>相关资源：<A href={GITHUB_URL}>OpenViking GitHub</A>、<A href={DOCS_URL}>文档站</A>、<A href={ARCH_POST}>架构详解</A>，以及 <A href={CODING_AGENT_POST}>Claude Code / Codex 记忆插件</A>。</>,
      })}</P>
    </Article>
  );
};

export default {
  id: 'openviking-context-database',
  Component: OpenVikingContextDatabase,
  meta: {
    title: { zh: 'OpenViking：上下文工程的数据库范式', en: 'OpenViking: The Database Paradigm for Context Engineering' },
    description: {
      zh: '从 Prompt、RAG、Tools、Skills 到 Memory，OpenViking 如何把上下文工程推进到面向 Agent 的数据库范式。',
      en: 'How OpenViking turns context engineering into a database-shaped interface for agents.',
    },
    cover: '/assets/covers/openviking-context-database.png',
    cardCover: '/assets/covers/openviking-context-database-card.png',
    publishedAt: '2026-03-10',
    updatedAt: '2026-10-04',
    readingTime: { zh: 18, en: 21 },
    category: { zh: '上下文工程', en: 'Context Engineering' },
    tags: ['openviking', 'context', 'agent'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [
      { name: 'maojia', github: 'MaojiaSheng' },
    ],
  },
};
