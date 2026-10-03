import React from 'react';
import { Article, Lead, P, H2, Pre, Table, A } from '../../blog-components';
const Post = ({
  t
}) => <Article>
    <Lead>{t({
      "zh": "Agent 能读代码，不代表它知道一次接口迁移为什么还没做完。实现可能在一个仓库，迁移条件在文档里，临时决定留在上周的对话中。OpenViking 要解决的是：让这些材料有地方存、有办法找，并能在下一次任务中继续使用。",
      "en": "An agent can read code without knowing why an interface migration is unfinished. The implementation may be in one repository, migration conditions in a document, and a temporary decision in last week’s conversation. OpenViking gives these materials a place to live and a way to be found and reused."
    })}</Lead>
    <H2 id="why-context">{t({
      "zh": "先看任务缺少什么",
      "en": "Start with what the task is missing"
    })}</H2>
    <P>{t({
      "zh": "以下用一个假设的接口迁移贯穿全文。团队把订单接口迁到 v2，却为尚未升级的客户端保留 v1。新会话中的 Agent 只看到旧代码，可能建议删除兼容分支。这个建议在局部看起来合理，放回迁移计划却会出错。",
      "en": "Consider a hypothetical order-API migration. The team moves to v2 but retains v1 for clients that have not upgraded. An agent in a new conversation may see only old code and suggest deleting the compatibility branch. The local cleanup can look reasonable while breaking the migration plan."
    })}</P>
    <P>{t({
      "zh": "解决这类问题，需要同时找到当前实现、兼容条件和作出决定的原因。增加上下文窗口只能装下更多文字；资料没有接入、范围选错或版本过时，窗口再大也补不上。",
      "en": "The task needs the implementation, compatibility conditions, and the reason for the decision. A larger context window can hold more text. It cannot compensate for missing sources, wrong scope, or stale versions."
    })}</P>
    <P>{t({
      "zh": "同样的问题也出现在长期助手忘记约束、团队资料分散，以及交付标准没有对齐时。共同缺口是信息无法持续服务任务。是否需要上下文数据库，应从这些重复成本判断。",
      "en": "The same gap appears when a long-running assistant loses a constraint, team knowledge is scattered, or delivery criteria are unclear. Information is failing to carry across tasks. Recurring costs like these are the reason to consider a context database."
    })}</P>
    <H2 id="context-types">{t({
      "zh": "资源、记忆和技能，各保留什么",
      "en": "Resources, memories, and skills have different jobs"
    })}</H2>
    <Table headers={[{
    "zh": "类型",
    "en": "Type"
  }, {
    "zh": "迁移案例中保存什么",
    "en": "In the migration example"
  }, {
    "zh": "怎样更新",
    "en": "How it changes"
  }].map(t)} rows={[[{
    "zh": "Resource",
    "en": "Resource"
  }, {
    "zh": "接口文档、客户端支持矩阵、代码仓库",
    "en": "API documentation, client support matrix, repositories"
  }, {
    "zh": "重新导入，或为支持的来源配置 Watch",
    "en": "Reimport, or configure Watch for supported sources"
  }], [{
    "zh": "Memory",
    "en": "Memory"
  }, {
    "zh": "保留 v1 的决定、原因，以及后续修正",
    "en": "The decision to retain v1, its reason, and later corrections"
  }, {
    "zh": "从会话提取或主动记录；检查来源和适用条件",
    "en": "Extract from sessions or record explicitly; check source and conditions"
  }], [{
    "zh": "Skill",
    "en": "Skill"
  }, {
    "zh": "迁移检查步骤：确认客户端、执行测试、核对回滚方案",
    "en": "Migration checks: clients, tests, and rollback plan"
  }, {
    "zh": "维护 SKILL.md 与配套资料，由 Agent 读取并执行",
    "en": "Maintain SKILL.md and supporting material for the agent to read and act on"
  }]].map(row => row.map(t))} />
    <P>{t({
      "zh": "三者共用 URI 和检索接口，但生命周期不同。代码更新不能靠一条记忆自动同步；记忆里的“暂缓迁移”也不能无限期当作现行要求。技能保存流程，执行仍依赖 Agent 的工具和权限。",
      "en": "All three share URI and retrieval interfaces, but have different lifecycles. A memory does not automatically synchronize code changes. A remembered postponement cannot remain a current requirement indefinitely. Skills describe procedures; execution still depends on the agent’s tools and permissions."
    })}</P>
    <H2 id="organization">{t({
      "zh": "文件系统接口，降低的是找资料的成本",
      "en": "Paths make context easier to navigate"
    })}</H2>
    <P>{t({
      "zh": "向量检索适合从不同措辞中找相关材料。路径适合表达“这个项目”“这份手册”“这个用户的记忆”。关系型字段适合精确过滤，图关系适合追踪依赖。这些能力可以组合，不能脱离数据量、查询类型和索引实现，给四种技术排一个统一名次。",
      "en": "Vector retrieval finds related material despite different wording. Paths express a project, manual, or user memory space. Relational fields support exact filters; graph relationships can trace dependencies. These approaches can be combined. A universal ranking without a workload or implementation is not useful."
    })}</P>
    <P>{t({
      "zh": "OpenViking 的取舍是保留 Agent 熟悉的目录操作，同时提供解析、摘要、语义索引和记忆处理。VikingDB 等向量后端负责检索基础能力，内容存储保存可阅读的材料。用户少写一部分接入和加工逻辑，也要承担模型调用、存储和维护这些派生产物的成本。",
      "en": "OpenViking keeps directory operations familiar to agents while adding parsing, summaries, semantic indexes, and memory processing. Vector backends such as VikingDB supply retrieval, and content storage holds readable material. This reduces application-side processing work while adding the cost of models, storage, and maintaining derived artifacts."
    })}</P>
    <P>{t({
      "zh": "这也解释了它与 RAG 的关系：RAG 是检索后生成的工作方式，OpenViking 可以作为其中的上下文层。它把工作延伸到检索之前的组织和处理，以及任务之后的沉淀与更新。它不会替代 Prompt、Web Search 或调用业务系统的 Tools。",
      "en": "This also explains the relationship to RAG. Retrieval-augmented generation is a workflow; OpenViking can supply its context layer. It extends the work to organization before retrieval and capture and updates after a task. It does not replace prompts, web search, or tools that call business systems."
    })}</P>
    <H2 id="reading">{t({
      "zh": "从找到入口，到读到证据",
      "en": "From a search lead to evidence"
    })}</H2>
    <P>{t({
      "zh": "把迁移资料导入后，Agent 可以先限定项目范围，再找兼容要求，最后打开命中的文档。相关性分数决定哪些候选先看，目录概览帮助选择下一份材料，正文用来核对具体条件。",
      "en": "After importing migration material, the agent can scope its search to the project, find compatibility requirements, and open the matching document. Relevance scores help prioritize candidates, directory overviews guide the next read, and bodies establish the exact conditions."
    })}</P>
    <Table headers={[{
    "zh": "操作",
    "en": "Operation"
  }, {
    "zh": "回答的问题",
    "en": "Question it answers"
  }].map(t)} rows={[[{
    "zh": "ls / tree",
    "en": "ls / tree"
  }, {
    "zh": "资料分成哪些目录，我在哪个范围内？",
    "en": "How is the material organized, and what scope am I in?"
  }], [{
    "zh": "find / search",
    "en": "find / search"
  }, {
    "zh": "哪些材料可能解释 v1 为什么保留？",
    "en": "Which material may explain why v1 remains?"
  }], [{
    "zh": "abstract / overview",
    "en": "abstract / overview"
  }, {
    "zh": "这个目录讲什么，下一步值得读哪里？",
    "en": "What does this directory cover, and where should I read next?"
  }], [{
    "zh": "read",
    "en": "read"
  }, {
    "zh": "兼容条件到底是什么，是否仍然适用？",
    "en": "What are the actual compatibility conditions, and do they still apply?"
  }]].map(row => row.map(t))} />
    <P>{t({
      "zh": "L0 是目录摘要，L1 是目录概览，L2 是原始内容或解析后的正文。它们让 Agent 控制阅读量。这个分层并不强制每次查询都逐层下钻：当前服务端可以直接召回不同层级的记录，Agent 也可以按已知 URI 直接读取。",
      "en": "L0 is a directory abstract, L1 an overview, and L2 original or parsed content. These layers help control reading volume. They do not force every search to descend the tree: current retrieval can match different levels directly, and an agent can read a known URI without searching."
    })}</P>
    <P>{t({
      "zh": "长文档会按所用 Parser 和配置转换、拆分或保留结构。拆分有助于按需阅读，也可能切断条件和例外之间的联系。因此需要同时检查目录、摘要和正文；“成功解析”只是数据进入系统，不代表语义没有损失。",
      "en": "Long documents are converted, split, or preserved according to the parser and configuration. Splitting supports selective reading but can separate a condition from its exception. Inspect the structure, summaries, and bodies together. Successful parsing does not establish that meaning was preserved."
    })}</P>
    <H2 id="practice">{t({
      "zh": "用一份小资料走通读取路径",
      "en": "Try the reading path on a small resource"
    })}</H2>
    <P>{t({
      "zh": "先按快速开始配置服务端模型和存储，再让 ov CLI 连接服务。下面使用虚构数据，假设当前用户可以写入 account 共享资源，且 checkout-demo 目标尚不存在。它演示导入和阅读，不是效果评测。",
      "en": "Configure server models and storage with the quickstart, then connect the ov CLI. The fictional sample below assumes permission to write account-shared resources and an unused checkout-demo destination. It demonstrates ingestion and reading, not an effectiveness benchmark."
    })}</P>
    <Pre lang="bash" filename="prepare-demo.sh">{"mkdir -p context-demo\ncat > context-demo/decision.md <<'EOF'\n# Order API migration\nDecision: retain v1 until every supported client has migrated to v2.\nReason: client A still calls v1.\nRemoval requires a fresh check of the client support matrix.\nEOF\nov add-resource ./context-demo \\\n  --to viking://resources/checkout-demo --wait"}</Pre>
    <Pre lang="bash" filename="inspect-demo.sh">{"ov tree viking://resources/checkout-demo\nov find \"When can we remove v1?\" --uri viking://resources/checkout-demo\nov overview viking://resources/checkout-demo"}</Pre>
    <P>{t({
      "zh": "接着对 tree 或 find 返回的实际文件 URI 执行 ov read。不同 Parser 的产物路径可能不同，别把示意路径当作固定输出。成功标准是能读回保留 v1 的条件，并发现还需要检查客户端支持矩阵。模型凭旧记忆说“可以删了”，仍然是失败。",
      "en": "Next, run ov read on the actual file URI returned by tree or find. Parser output paths can differ, so do not assume a fixed generated path. Success means recovering the condition for retaining v1 and recognizing the need to check the client support matrix. An answer based on stale memory that says to delete it is still a failure."
    })}</P>
    <P>{t({
      "zh": "这个样例只导入了决定，没有导入客户端支持矩阵。Agent 应能指出证据缺口，而不是编造迁移完成状态。真实接入时，再加入那份矩阵和相关代码，并检查更新后的资料能否被检索到。",
      "en": "This sample imports the decision but not the client support matrix. The agent should identify that gap instead of inventing a migration status. In a real integration, add the matrix and relevant code, then check that updated material becomes retrievable."
    })}</P>
    <H2 id="memory">{t({
      "zh": "让下一次任务用上这次经验",
      "en": "Carry experience into the next task"
    })}</H2>
    <P>{t({
      "zh": "资源说明系统是什么，会话还会记录团队为什么这样做。通过插件或应用集成，消息进入 OpenViking Session；提交后，服务端归档并异步提取记忆。Claude Code、Codex 等宿主需要各自的捕获和召回接入，连接 MCP 本身不会自动上传全部历史。",
      "en": "Resources describe the system; conversations can explain why the team chose it. Plugins or application integrations send messages to an OpenViking Session. A commit archives them and starts asynchronous extraction. Hosts such as Claude Code and Codex need their own capture and recall integration; connecting MCP alone does not upload all history."
    })}</P>
    <P>{t({
      "zh": "在迁移案例里，值得留下的是决定、原因和重新评估的条件。完成迁移后，要让后续记录修正旧决定。记忆积累只有在减少重复查找、保留有效约束时才有价值；把错误反复提取，只会让后续任务更难纠正。",
      "en": "For the migration, retain the decision, rationale, and condition for reconsideration. Once migration finishes, later records should correct the old decision. Accumulation helps only when it reduces repeated discovery and preserves valid constraints. Repeatedly extracting a mistake makes future work harder to correct."
    })}</P>
    <P>{t({
      "zh": "Skill 可以把同类迁移的检查步骤写下来，VikingBot 或外部 Agent 可以读取并使用这些材料。上下文系统提供参考和流程，任务编排、工具执行和结果验收仍由宿主及应用负责。所谓从经验中改进，应落实为下一次行为的变化，而不是一句“系统会自进化”。",
      "en": "A skill can record checks for similar migrations, and VikingBot or an external agent can use that material. The context system supplies references and procedures; orchestration, tool execution, and acceptance remain with the host and application. Learning from experience should mean an observed improvement in a later task."
    })}</P>
    <H2 id="team-adoption">{t({
      "zh": "团队接入，先做一个可核对的切片",
      "en": "Start team adoption with a verifiable slice"
    })}</H2>
    <P>{t({
      "zh": "先接一个项目的代码和文档，挑几项已知答案的真实任务，再加入相关决策记录。为每份来源确定归属和更新方式。需要多人使用时，明确 account、user 和共享资源 ACL；不要靠目录名字或 prompt 约定权限。",
      "en": "Start with one project’s code and documentation, a few real tasks with known answers, and their decision records. Assign ownership and update rules to each source. For several users, define accounts, users, and shared-resource ACLs. Directory names and prompt conventions are not access controls."
    })}</P>
    <Table headers={[{
    "zh": "检查项",
    "en": "Check"
  }, {
    "zh": "怎么判断",
    "en": "How to judge it"
  }].map(t)} rows={[[{
    "zh": "能否找对",
    "en": "Finding the evidence"
  }, {
    "zh": "预先标出的证据是否进入候选，范围过滤是否误排除它",
    "en": "Does expected evidence enter the candidates, or does scope exclude it?"
  }], [{
    "zh": "是否读够",
    "en": "Reading enough"
  }, {
    "zh": "答案是否保留条件和例外，能否回到来源",
    "en": "Does the answer retain conditions and exceptions and cite its source?"
  }], [{
    "zh": "能否更新",
    "en": "Updating"
  }, {
    "zh": "修正来源或记忆后，后续任务是否仍引用旧结论",
    "en": "After a correction, do later tasks still use the old conclusion?"
  }], [{
    "zh": "是否值得",
    "en": "Cost and value"
  }, {
    "zh": "固定任务、模型和资料版本，比较质量、等待时间及 Token 用量",
    "en": "Hold tasks, model, and source versions fixed; compare quality, latency, and token use"
  }], [{
    "zh": "是否隔离",
    "en": "Isolation"
  }, {
    "zh": "分别验证允许与禁止的身份能否检索、读取目标资料",
    "en": "Test both allowed and denied identities for search and read"
  }]].map(row => row.map(t))} />
    <P>{t({
      "zh": "更多数据不会自动带来更高效率。重复、过时和无关内容会增加加工成本，也可能挤占召回结果。先证明这一小批资料能改善任务，再扩大来源，才能知道新接入的数据究竟帮了什么。",
      "en": "More data does not automatically improve efficiency. Duplicate, stale, and irrelevant material costs processing time and can crowd out useful results. Demonstrate value with the initial slice before adding sources so the benefit of each addition remains visible."
    })}</P>
    <H2 id="decision">{t({
      "zh": "什么时候值得引入",
      "en": "When the extra layer is worthwhile"
    })}</H2>
    <P>{t({
      "zh": "只有少量固定文档、工作集中在一个仓库时，指令文件和已有搜索往往够用。业务问题本来就是按字段查表，就保留结构化查询。跨来源、跨会话、跨客户端的背景反复丢失，且团队愿意维护来源和权限时，OpenViking 才有明确的用武之地。",
      "en": "With a few stable documents in one repository, instruction files and existing search may be enough. Keep structured queries for structured business data. OpenViking has a clearer role when context repeatedly gets lost across sources, sessions, and clients, and the team can maintain provenance and access."
    })}</P>
    <P>{t({
      "zh": "在接口迁移这个例子里，交付标准很具体：换一个会话后，Agent 仍能找到保留旧接口的原因，读到移除条件，并在资料不够时停下来补证据。先把这条路径走通，再讨论覆盖全团队的上下文。",
      "en": "For the migration example, the acceptance criterion is concrete: in a new session, the agent finds why the old interface remains, reads the removal condition, and seeks missing evidence before acting. Establish that path before expanding context coverage to the whole team."
    })}</P>
    <P><A href="https://docs.openviking.ai/en/getting-started/02-quickstart">{t({
        "zh": "快速开始：服务与模型配置",
        "en": "Quickstart: server and model configuration"
      })}</A></P>
    <P><A href="https://docs.openviking.ai/en/concepts/02-context-types">{t({
        "zh": "资源、记忆与技能",
        "en": "Resources, memories, and skills"
      })}</A></P>
    <P><A href="/post/openviking-context-database-architecture/">{t({
        "zh": "继续阅读：上下文读写的架构",
        "en": "Continue: the architecture of context reads and writes"
      })}</A></P>
    <P><A href="/post/openviking-coding-agent/">{t({
        "zh": "继续阅读：Claude Code / Codex 记忆插件",
        "en": "Continue: memory plugins for Claude Code and Codex"
      })}</A></P>
  </Article>;
export default {
  id: "openviking-context-database",
  Component: Post,
  meta: {
    "cover": "/assets/covers/openviking-context-database.png",
    "cardCover": "/assets/covers/openviking-context-database-card.png",
    "publishedAt": "2026-03-10",
    "readingTime": {"zh": 5, "en": 6},
    "category": {
      "zh": "上下文工程",
      "en": "Context Engineering"
    },
    "tags": ["openviking", "context", "agent"],
    "authors": [{
      "name": "maojia",
      "github": "MaojiaSheng"
    }],
    "title": {
      "zh": "为什么 Agent 需要上下文数据库：从一次接口迁移说起",
      "en": "Why Agents Need a Context Database: An API Migration Example"
    },
    "description": {
      "zh": "用一个跨仓库任务解释资源、记忆、技能和分层阅读，判断何时值得引入 OpenViking，并给出接入验收方法。",
      "en": "Use a cross-repository task to understand resources, memory, skills, and layered reading, and decide when OpenViking is worth introducing."
    },
    "updatedAt": "2026-10-03",
    "languages": ["en", "zh"],
    "llmPath": "/post/openviking-context-database/llm.txt"
  }
};
