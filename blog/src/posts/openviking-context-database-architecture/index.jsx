import React from 'react';
import { Article, Lead, P, H2, Pre, Table, A } from '../../blog-components';
const Post = ({
  t
}) => <Article>
    <Lead>{t({
      "zh": "把一份文档交给 Agent，难点不止是把它存下来。下一次任务要找到正确版本，读取足够的细节，还得确认调用者有权访问。OpenViking 用 URI、目录摘要和检索索引连接这些步骤。理解它的架构，先沿着一份资源走一遍。",
      "en": "Storing a document is only the start. The next task must find the right version, read enough detail, and respect the caller’s access. OpenViking connects these steps through URIs, directory summaries, and retrieval indexes. Follow one resource to see where each responsibility belongs."
    })}</Lead>
    <H2 id="system-shape">{t({
      "zh": "接口像文件系统，后面有两类存储",
      "en": "A filesystem interface over two stores"
    })}</H2>
    <P>{t({
      "zh": "Agent 可以用 ls、tree、find、overview 和 read 操作上下文。viking:// 是逻辑地址：它关联内容、索引和访问范围，不是操作系统路径，也不暴露对象存储的内部 key。精确表查询仍适合关系型数据库；这里解决的是文档、代码和交互历史怎样被找到和阅读。",
      "en": "Agents use ls, tree, find, overview, and read to work with context. A viking:// URI is a logical address linking content, indexes, and access scope. It is not an operating-system path or an exposed object-store key. Relational databases remain suited to exact table queries; this interface serves documents, code, and interaction history."
    })}</P>
    <Pre lang="text" filename="request-path.txt">{"CLI / SDK / MCP\n        |\nHTTP server: authentication and request context\n        |\nServices: resources / search / sessions / filesystem\n        |\nVikingFS: URI operations and content access\n       / \\\nRAGFS     Vector index\ncontent   vectors, URI, metadata, retrieval text"}</Pre>
    <P>{t({
      "zh": "Python 服务层编排解析、模型调用、检索和会话任务。VikingFS 提供统一的 URI 操作，Rust 实现的 RAGFS 承接内容存储；向量后端负责候选检索。这种拆分让内容读写与相关性检索各用合适的接口。",
      "en": "The Python service layer orchestrates parsing, model calls, retrieval, and session tasks. VikingFS exposes URI operations; the Rust-based RAGFS handles content storage, while the vector backend retrieves candidates. Content operations and relevance search use separate interfaces."
    })}</P>
    <P>{t({
      "zh": "内容层保存正文、多媒体文件和目录摘要。向量库保存向量、URI、元数据以及检索需要的文本，也可能包含记忆正文。因此“内容与索引分离”不等于“向量库没有明文”；备份和访问控制要覆盖两层。",
      "en": "The content layer holds text, media, and directory summaries. The vector store holds vectors, URIs, metadata, and retrieval text, which can include memory bodies. Separating content from indexes does not mean the vector store contains no readable text. Backup and access controls must cover both."
    })}</P>
    <H2 id="ingestion">{t({
      "zh": "写入：上传完成后还有派生处理",
      "en": "Ingestion continues after upload"
    })}</H2>
    <P>{t({
      "zh": "假设团队导入一份认证手册。Parser 按输入格式生成文件和目录，TreeBuilder 确定目标 URI，资源处理流程提交内容，再由语义和向量化任务生成摘要与索引。PDF 可能被解析成 Markdown，代码仓库则保留可导航的结构；L2 不保证与源文件逐字节相同。",
      "en": "Suppose a team imports an authentication manual. A parser produces files and directories, TreeBuilder determines the destination URI, and resource processing commits content. Semantic and embedding work then generates summaries and indexes. A PDF may become Markdown, while a repository retains navigable structure; L2 is not guaranteed to be a byte-identical source file."
    })}</P>
    <P>{t({
      "zh": "接收请求、内容可读、摘要可用、向量可检索，是不同的进度。异步模式返回成功后，不能马上把“搜索不到”归因于召回算法。先检查任务状态；需要写后检索的流程，应使用等待处理完成的接口或轮询对应任务。",
      "en": "Request acceptance, readable content, available summaries, and searchable vectors are distinct milestones. After an asynchronous request succeeds, an empty search is not necessarily a retrieval failure. Inspect task status first; workflows that search immediately after ingestion should wait for processing or poll the associated task."
    })}</P>
    <H2 id="progressive-disclosure">{t({
      "zh": "L0/L1/L2：控制阅读粒度",
      "en": "L0/L1/L2 control reading depth"
    })}</H2>
    <Table headers={[{
    "zh": "层级",
    "en": "Layer"
  }, {
    "zh": "内容",
    "en": "Content"
  }, {
    "zh": "用途",
    "en": "Purpose"
  }].map(t)} rows={[[{
    "zh": "L0",
    "en": "L0"
  }, {
    "zh": "目录摘要，通常存为 .abstract.md",
    "en": "Directory abstract, usually stored as .abstract.md"
  }, {
    "zh": "快速判断目录是否相关",
    "en": "Decide whether a directory is relevant"
  }], [{
    "zh": "L1",
    "en": "L1"
  }, {
    "zh": "目录概览，通常存为 .overview.md",
    "en": "Directory overview, usually stored as .overview.md"
  }, {
    "zh": "理解范围和结构，选择继续阅读的入口",
    "en": "Understand scope and structure and choose what to read"
  }], [{
    "zh": "L2",
    "en": "L2"
  }, {
    "zh": "原始内容或解析后的正文与文件",
    "en": "Original content or parsed bodies and files"
  }, {
    "zh": "核对条款、实现和细节",
    "en": "Verify clauses, implementation, and details"
  }]].map(row => row.map(t))} />
    <Pre lang="text" filename="illustrative-resource-tree.txt">{"viking://resources/auth-manual/\n├── .abstract.md\n├── .overview.md\n├── token-lifecycle.md\n└── migration.md"}</Pre>
    <P>{t({
      "zh": "这是一棵示意目录。L0/L1 是目录级 sidecar，普通文件不会各自再配一套同名摘要文件。文件摘要会参与所在目录的概览生成。两个 sidecar 也不保证同时存在，创建了目录不代表语义处理已经完成。",
      "en": "This tree is illustrative. L0/L1 are directory-level sidecars, not another pair of files beside every ordinary file. File summaries contribute to the containing directory’s overview. The two sidecars need not both exist; directory creation does not prove semantic processing has finished."
    })}</P>
    <P>{t({
      "zh": "“先摘要、再概览、最后正文”是一种阅读策略。知道文件地址时可以直接 read，需要精确事实时也应回到正文。摘要由模型生成，可能遗漏例外条件；保留 L2 的价值，是让 Agent 能继续查证。",
      "en": "Reading abstract, overview, then body is a strategy. A known file can be read directly, and exact claims should be checked against its body. Model-generated summaries may omit exceptions. Retaining L2 lets the agent continue checking the evidence."
    })}</P>
    <H2 id="retrieval">{t({
      "zh": "检索：范围过滤与排序各做什么",
      "en": "Retrieval separates scope from ranking"
    })}</H2>
    <P>{t({
      "zh": "目录的作用是表达范围。例如在认证手册中找“令牌轮换”，比对全库做同一个查询更能表达任务意图。但选错目录也会排除正确证据。路径降低了指定范围的成本，不保证检索质量必然提高。",
      "en": "Directories express scope. Searching the authentication manual for token rotation conveys more intent than searching the whole store. Choosing the wrong directory can also exclude the answer. Paths make scope easier to specify; they do not guarantee better retrieval."
    })}</P>
    <P>{t({
      "zh": "当前实现对每条查询执行一次带范围约束的全局向量检索，再按配置执行一次 rerank。权限、目标目录、上下文类型和元数据过滤限定候选集合。它不要求先命中 L0、再逐层走到 L2；文本检索可以直接命中不同层级。",
      "en": "The current implementation runs one scoped global vector search per query, with an optional rerank pass. Permissions, target directories, context type, and metadata filters constrain candidates. It does not require an L0 hit followed by recursive descent to L2; text queries can match different levels directly."
    })}</P>
    <Table headers={[{
    "zh": "入口",
    "en": "Entry point"
  }, {
    "zh": "行为",
    "en": "Behavior"
  }, {
    "zh": "取舍",
    "en": "Tradeoff"
  }].map(t)} rows={[[{
    "zh": "find",
    "en": "find"
  }, {
    "zh": "直接检索，不做会话意图分析",
    "en": "Retrieve directly without session intent analysis"
  }, {
    "zh": "适合已有明确查询；少一段模型规划路径",
    "en": "Useful for a clear query; avoids a model-planning stage"
  }], [{
    "zh": "search",
    "en": "search"
  }, {
    "zh": "可结合会话与配置分析意图；配置可用 reranker 时精排候选",
    "en": "Can analyze intent using session context and configuration; uses a configured reranker"
  }, {
    "zh": "多查询和模型调用可能增加耗时",
    "en": "Multiple queries and model calls can add latency"
  }], [{
    "zh": "read / overview",
    "en": "read / overview"
  }, {
    "zh": "按 URI 读取正文或目录概览",
    "en": "Read a body or directory overview by URI"
  }, {
    "zh": "用于验证已命中的线索，不重新做语义匹配",
    "en": "Verify a known lead without another semantic match"
  }]].map(row => row.map(t))} />
    <P>{t({
      "zh": "rerank 只能重排已召回的候选，救不回没有进入集合的证据。结果分数也不能当作事实可信度。一次失败应拆开看：查询范围错了，候选没召回，排序靠后，还是 Agent 读到后理解错了。",
      "en": "Reranking cannot recover evidence absent from its candidate set. A result score is not factual confidence either. Diagnose a failure by separating wrong scope, missing candidates, poor ranking, and misinterpretation after reading."
    })}</P>
    <P>{t({
      "zh": "多模态是另一条能力轴。图片的文字描述可以帮助文本检索；图像查询还取决于 embedding 模型是否支持多模态。把图片放进目录，并不会自动获得所有模态之间的检索能力。",
      "en": "Modality is a separate concern. A textual image description can support text retrieval; image queries also require a multimodal embedding model. Putting images into a directory does not by itself enable every cross-modal search."
    })}</P>
    <H2 id="identity">{t({
      "zh": "身份：同一个 URI 不等于同一份数据",
      "en": "Identity determines what a URI resolves to"
    })}</H2>
    <P>{t({
      "zh": "account 是租户边界，user 是租户内的用户边界，peer 是用户下面的内容范围。在 API Key 模式下，有效 account 和 user 来自 User/Admin key。Root key 用于管理，不能当日常租户数据读写凭证。",
      "en": "An account is the tenant boundary, a user is a principal within that account, and a peer scopes content within a user. In API-key mode, the User/Admin key determines the effective account and user. A Root key is for administration, not ordinary tenant data access."
    })}</P>
    <Table headers={[{
    "zh": "地址示例",
    "en": "Example URI"
  }, {
    "zh": "用途",
    "en": "Use"
  }, {
    "zh": "边界",
    "en": "Boundary"
  }].map(t)} rows={[[{
    "zh": "viking://resources/project-a/",
    "en": "viking://resources/project-a/"
  }, {
    "zh": "团队共享资料",
    "en": "Team resources"
  }, {
    "zh": "account 内默认共享，可用 ACL 细化",
    "en": "Shared within the account by default; ACL can narrow access"
  }], [{
    "zh": "viking://user/alice/memories/",
    "en": "viking://user/alice/memories/"
  }, {
    "zh": "Alice 的用户级记忆",
    "en": "Alice’s user-level memory"
  }, {
    "zh": "user",
    "en": "user"
  }], [{
    "zh": "viking://user/alice/peers/project-a/memories/",
    "en": "viking://user/alice/peers/project-a/memories/"
  }, {
    "zh": "Alice 在项目范围内的记忆",
    "en": "Alice’s project-scoped memory"
  }, {
    "zh": "user 下的 peer，不是新租户",
    "en": "A peer within the user, not a new tenant"
  }], [{
    "zh": "viking://agent/skills/",
    "en": "viking://agent/skills/"
  }, {
    "zh": "账号内共享技能",
    "en": "Account-shared skills"
  }, {
    "zh": "受访问权限约束",
    "en": "Subject to access controls"
  }]].map(row => row.map(t))} />
    <P>{t({
      "zh": "相同的资源 URI 在不同 account 中可以解析到不同内容。peer 过滤也不改变 user 身份。多个聊天用户共用一把 user key 时，不能仅靠给 session 起不同名字就宣称完成了用户隔离。",
      "en": "The same resource URI can resolve to different content in different accounts. Peer filtering does not change user identity. Several chat participants using one user key do not become isolated users merely because their sessions have different names."
    })}</P>
    <P>{t({
      "zh": "路径必须属于服务支持的命名空间。按时间、类目或地理组织资料时，可以在资源树内设计目录；不能凭一个示意图就把 viking://calendar 或 viking://geo 当作现成 API。一个对象出现在多个视图中的产品设想，也需要独立的索引和读写语义。",
      "en": "Paths must belong to supported namespaces. Time, category, or geography can organize directories inside the resource tree; a diagram does not make viking://calendar or viking://geo supported APIs. Exposing one object through several views also requires defined indexing and read/write semantics."
    })}</P>
    <H2 id="consistency">{t({
      "zh": "更新与删除：锁保护什么",
      "en": "What locks protect during updates"
    })}</H2>
    <P>{t({
      "zh": "内容、摘要和索引不是一次跨存储原子提交。资源正文可能已经更新，派生摘要和向量仍在处理。Session commit 也先完成归档准备，再由持久化队列继续摘要和提取。调用方需要跟踪处理状态，不能把收到响应当成所有视图同时更新。",
      "en": "Content, summaries, and indexes do not commit as one cross-store atomic transaction. A resource body can be updated while derived summaries and vectors are pending. Session commit similarly prepares an archive before a persistent queue continues summarization and extraction. A response does not imply that every view is current."
    })}</P>
    <P>{t({
      "zh": "路径锁协调遵守同一锁协议的冲突写入，EXACT 保护目标路径，TREE 覆盖子树。锁释放不会撤销已经写入的数据。删除流程先清理索引，再删除内容，目的是减少检索返回已不存在文件的风险；部分失败仍需要按任务状态恢复。",
      "en": "Path locks coordinate conflicting writers that participate in the same protocol. EXACT protects a path; TREE covers a subtree. Releasing a lock does not undo writes. Deletion removes indexes before content to reduce dangling search hits, but partial failures still require recovery based on operation state."
    })}</P>
    <P>{t({
      "zh": "持久化队列让重启后继续处理成为可能，但模型重试不保证生成相同文本。部署评估需要看源数据备份、任务恢复和索引重建，不能只看“有锁”或“请求返回 200”。",
      "en": "Persistent queues allow work to continue after restart, but retrying a model call need not produce identical text. Evaluate source backups, task recovery, and index rebuilding rather than treating locks or a 200 response as a durability proof."
    })}</P>
    <H2 id="operations">{t({
      "zh": "扩容与隐私：存储可替换，约束仍在",
      "en": "Scaling and privacy retain separate constraints"
    })}</H2>
    <P>{t({
      "zh": "内容存储支持本地和远端后端，向量后端也可配置。这为部署选择留下空间，实际容量仍取决于索引、模型吞吐、网络和目录分布。增加实例数之前，要检查所用存储与锁后端的多写能力；主备复制也不等于多主并发写入。",
      "en": "Content storage supports local and remote backends, and the vector backend is configurable. Capacity still depends on indexes, model throughput, network behavior, and directory distribution. Before adding writers, check storage and lock-provider support. Primary/backup replication is not multi-primary concurrency."
    })}</P>
    <P>{t({
      "zh": "写入延迟包括解析、摘要、embedding、队列等待和存储操作；读取延迟包括检索、可选 rerank 和正文读取。应分别观察首次可读时间、首次可检索时间和任务完成时间，再决定优化哪一段。",
      "en": "Write latency includes parsing, summarization, embedding, queue wait, and storage operations. Reads include retrieval, optional reranking, and content access. Measure time to readable content, searchable content, and task completion separately before choosing an optimization."
    })}</P>
    <P>{t({
      "zh": "可选的静态加密保护配置覆盖的文件存储，授权读取仍返回明文。开启后不会自动重写已有明文文件，也不意味着向量索引、日志或发送给模型的内容同时加密。Skill 隐私配置负责另一类问题：按策略提供执行时需要的秘密。两者都不能替代访问控制。",
      "en": "Optional encryption at rest protects configured file storage; authorized reads still return plaintext. Enabling it does not automatically rewrite old plaintext or encrypt vector indexes, logs, and model inputs. Skill privacy configuration separately governs secrets needed during execution. Neither replaces access control."
    })}</P>
    <P>{t({
      "zh": "架构是否适合你的工作负载，可以用一份会更新的资料来检查：导入后是否可读可搜，修改后旧摘要多久退出，撤销权限后检索和读取是否都拒绝，处理中重启后能否恢复。每个结果对应一条实际边界，比抽象的“数据库级可靠”更有用。",
      "en": "Test the architecture with a resource that changes: when does it become readable and searchable, how long do old summaries persist, do both search and read respect revoked access, and does interrupted processing recover? These checks expose operational boundaries that a general reliability claim cannot establish."
    })}</P>
    <P><A href="https://docs.openviking.ai/en/concepts/03-context-layers">{t({
        "zh": "分层上下文与当前检索机制",
        "en": "Context layers and current retrieval"
      })}</A></P>
    <P><A href="https://docs.openviking.ai/en/concepts/07-retrieval">{t({
        "zh": "检索机制",
        "en": "Retrieval mechanism"
      })}</A></P>
    <P><A href="https://docs.openviking.ai/en/concepts/09-transaction">{t({
        "zh": "路径锁与崩溃恢复",
        "en": "Path locks and crash recovery"
      })}</A></P>
    <P><A href="https://docs.openviking.ai/en/concepts/11-multi-tenant">{t({
        "zh": "多租户身份与权限",
        "en": "Multi-tenant identity and access"
      })}</A></P>
  </Article>;
export default {
  id: "openviking-context-database-architecture",
  Component: Post,
  meta: {
    "cover": "/assets/covers/openviking-context-database-architecture.png",
    "publishedAt": "2026-05-12",
    "readingTime": {"en": 6, "zh": 5},
    "category": {
      "zh": "架构",
      "en": "Arch"
    },
    "tags": ["openviking", "arch", "context", "agent"],
    "authors": [{
      "name": "maojia",
      "github": "MaojiaSheng"
    }],
    "title": {
      "zh": "OpenViking 架构：一次上下文读写经过什么",
      "en": "OpenViking Architecture: Following a Context Read and Write"
    },
    "description": {
      "zh": "沿着资源导入、分层阅读、检索和更新，解释 OpenViking 的存储、身份与一致性边界。",
      "en": "Follow ingestion, layered reading, retrieval, and updates to understand OpenViking’s storage, identity, and consistency boundaries."
    },
    "updatedAt": "2026-10-03",
    "languages": ["en", "zh"],
    "llmPath": "/post/openviking-context-database-architecture/llm.txt"
  }
};
