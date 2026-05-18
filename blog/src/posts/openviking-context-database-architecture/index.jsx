import React, { useMemo, useState } from 'react';
import {
  Article, Lead, P, H2, H3, H4, Pre, Quote, Pull, Callout, Hr,
  Cols, Col, Ol, Li, Ul, Table, A, InlineCode, Tag, Small,
} from '../../blog-components';
import {
  ArchitectureStack,
  ConsistencyLockMatrix,
  PrivacyIdentityFlow,
  WritePipelineBottleneck,
} from './round2-blocks';

const LLM_PATH = '/post/openviking-context-database-architecture/llm.txt';

const card = {
  border: '1px solid var(--th-line)',
  borderRadius: 'var(--th-radius)',
  background: 'var(--th-bg-2)',
  padding: '1rem',
};

function DirectoryDepthDemo({ t }) {
  const [depth, setDepth] = useState(1);
  const rows = useMemo(() => ([
    { path: 'viking://resources/openviking', level: 0 },
    { path: 'viking://resources/openviking/docs', level: 1 },
    { path: 'viking://resources/openviking/docs/design', level: 2 },
    { path: 'viking://resources/openviking/telemetry', level: 1 },
    { path: 'viking://resources/openviking/telemetry/grafana', level: 2 },
    { path: 'viking://resources/openviking/images/20260509/upload_png', level: 2 },
  ]), []);
  const visible = rows.filter(row => depth === -1 || row.level <= depth);
  return (
    <div style={{ ...card, margin: '1.5rem 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <H4 toc={false}>{t({ en: 'Directory depth selector', zh: '目录深度选择器' })}</H4>
          <Small>{t({ en: 'The buttons change only the visualization; the rule is still visible below.', zh: '按钮只改变可视化，规则本身始终展示在下方。' })}</Small>
        </div>
        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
          {[-1, 0, 1, 2].map(value => (
            <button
              type="button"
              key={value}
              aria-pressed={depth === value}
              onClick={() => setDepth(value)}
              style={{
                border: '1px solid var(--th-line)',
                borderRadius: '999rem',
                padding: '0.45rem 0.65rem',
                background: depth === value ? 'var(--th-accent)' : 'transparent',
                color: depth === value ? 'var(--th-bg)' : 'var(--th-fg)',
                fontFamily: 'var(--th-font-mono)',
                cursor: 'pointer',
              }}
            >
              d={value}
            </button>
          ))}
        </div>
      </div>
      <div style={{ marginTop: '1rem', display: 'grid', gap: '0.45rem', minWidth: 0 }}>
        {rows.map(row => {
          const active = visible.includes(row);
          return (
            <div
              key={row.path}
              style={{
                opacity: active ? 1 : 0.38,
                padding: '0.55rem 0.75rem',
                border: '1px solid var(--th-line)',
                borderRadius: 'var(--th-radius)',
                fontFamily: 'var(--th-font-mono)',
                fontSize: '0.82rem',
                lineHeight: 1.45,
                marginLeft: `min(${row.level * 1.25}rem, 18vw)`,
                minWidth: 0,
                maxWidth: '100%',
                overflowWrap: 'anywhere',
                background: active ? 'color-mix(in oklab, var(--th-accent) 10%, transparent)' : 'transparent',
              }}
            >
              {row.path}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function IdentityEvolution({ t }) {
  const versions = [
    {
      label: 'V1',
      title: t({ en: 'Agent belongs to User', zh: 'Agent 隶属于 User' }),
      problem: t({ en: 'Simple RBAC, but one service agent cannot naturally serve many visitors with separate memory.', zh: 'RBAC 简单，但一个服务型 Agent 很难自然服务多个访客并隔离记忆。' }),
    },
    {
      label: 'V2',
      title: t({ en: 'Agent can own data', zh: 'Agent 可以拥有数据' }),
      problem: t({ en: 'More flexible, but the authorization graph becomes hard to explain and harder to secure.', zh: '更灵活，但授权关系难解释，也更难保证安全。' }),
    },
    {
      label: 'V3',
      title: t({ en: 'Human and agent are peers', zh: '人和 Agent 是对等主体' }),
      problem: t({ en: 'The target model: `user` is the only authenticated object besides root, and it may represent a human or an agent.', zh: '目标模型：root 之外只有 `user` 是认证对象，它既可以代表人，也可以代表 Agent。' }),
      target: true,
    },
  ];
  return (
    <Cols count={3}>
      {versions.map(version => (
        <Col key={version.label}>
          <div style={{
            ...card,
            height: '100%',
            borderColor: version.target ? 'var(--th-accent)' : 'var(--th-line)',
          }}>
            <Tag>{version.label}</Tag>
            <H4 toc={false}>{version.title}</H4>
            <P>{version.problem}</P>
          </div>
        </Col>
      ))}
    </Cols>
  );
}

function BottleneckGrid({ t }) {
  const items = [
    [t({ en: 'Vector database', zh: '向量数据库' }), t({ en: 'Use VikingDB DSL filters for shared pools; dedicate a vector database for large tenants.', zh: '轻量场景用 VikingDB DSL 过滤共享池；大型租户独占向量数据库。' })],
    [t({ en: 'Filesystem', zh: '文件系统' }), t({ en: 'Local FS is fast but fragile; S3/TOS scales but can slow the agent loop.', zh: '本地快但脆；S3/TOS 可扩展但可能拖慢 Agent Loop。' })],
    [t({ en: 'Write pipeline', zh: '写入链路' }), t({ en: 'Parsing, splitting, VLM calls, embeddings, summaries, and memory extraction dominate latency.', zh: '解析、切分、VLM、Embedding、摘要和记忆抽取共同决定延迟。' })],
    [t({ en: 'Locks', zh: '锁机制' }), t({ en: 'Directory/file locks protect conflicting writes; transaction semantics are still evolving.', zh: '目录锁和文件锁保护冲突写；事务语义仍在演进。' })],
  ];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(12rem, 1fr))', gap: '0.75rem', margin: '1rem 0' }}>
      {items.map(([title, detail]) => (
        <div key={title} style={card}>
          <H4 toc={false}>{title}</H4>
          <P>{detail}</P>
        </div>
      ))}
    </div>
  );
}

const OpenVikingArchitecturePost = ({ t }) => {
  const T = t;

  return (
    <Article>
      <Lead>{T({
        en: 'OpenViking is trying to make context feel less like prompt stuffing and more like database infrastructure: addressable, indexed, isolated, observable, and safe enough for agents to use repeatedly.',
        zh: 'OpenViking 试图把上下文从“塞进 prompt 的材料”推进到真正的数据基础设施：可寻址、可索引、可隔离、可观测，并且足够安全，能让 Agent 反复使用。',
      })}</Lead>

      <Quote cite="Mike Stonebraker, April 2026">
        {T({
          en: 'On one benchmark, text-to-SQL accuracy moved from 0%, to 10% with RAG-style tricks, to roughly 35% when the prompt directly supplied the actual tables and joins.',
          zh: '在一个基准测试上，text-to-SQL 准确率从 0%，到加 RAG 等技巧后的 10%，再到 prompt 直接给出实际表和连接条件后的约 35%。',
        })}
      </Quote>

      <P dropCap>{T({
        en: 'Agents need the right data substrate. They need to know where information lives, how far a search should expand, which memory belongs to whom, and whether a write is safe. OpenViking frames that substrate as a context database.',
        zh: 'Agent 需要合适的数据底座：信息在哪里、检索应该扩到多深、记忆属于谁、写入是否安全。OpenViking 把这个底座定义成上下文数据库。',
      })}</P>

      <H2>{T({ en: 'The Shape Of The System', zh: '系统的整体形态' })}</H2>
      <P>{T({
        en: 'The core is deliberately polyglot. Python owns the server because model calls, parsing, multimodal processing, and AI dependencies still live there. Rust owns distribution-sensitive surfaces such as the CLI and RAGFS. C++ carries the embedded vector database lineage from VikingDB.',
        zh: 'OpenViking 的技术栈是有意拆开的：Python 承担服务端，因为模型调用、解析、多模态处理和 AI 依赖都在这个生态里；Rust 承担分发和吞吐敏感的 CLI、RAGFS；C++ 承接 VikingDB 的单机向量库能力。',
      })}</P>
      <ArchitectureStack t={T} />
      <P>{T({
        en: 'That split is not just an implementation detail. It keeps each layer honest about its contract: agents speak in commands and URIs, the server enforces identity and jobs, AGFS/RAGFS gives context a traversable shape, and VikingDB plus file storage decide what can be retrieved or persisted.',
        zh: '这个拆分不只是实现细节，而是在约束每一层的责任：Agent 通过命令和 URI 说话，服务层负责身份和任务，AGFS/RAGFS 给上下文可遍历的形态，VikingDB 和文件存储决定什么能被检索和持久化。',
      })}</P>
      <Callout type="info">
        <P>{T({
          en: 'The public docs are the living reference for module boundaries and deployment details: ',
          zh: '模块边界和部署细节以官网文档为准：',
        })}<A href="https://docs.openviking.ai/">docs.openviking.ai</A></P>
      </Callout>

      <Hr ornament />

      <H2 id="directory-semantics">{T({ en: 'Directory Semantics Are The Addressing Layer', zh: '目录语义是寻址层' })}</H2>
      <P>{T({
        en: 'A large amount of useful context is already organized as a tree: code, calendars, wikis, books, shelves, service trees. VikingDB turned that observation into a path-aware vector index. OpenViking then uses `viking://` URIs to give agents a database namespace that feels familiar without pretending to be the local filesystem.',
        zh: '大量有用上下文本来就是树：代码、日历、Wiki、图书、货架、服务树。VikingDB 把这个观察做成了路径感知向量索引。OpenViking 再用 `viking://` URI 给 Agent 一个熟悉但不等同于本地文件系统的数据库命名空间。',
      })}</P>
      <P>{T({
        en: 'The important detail is that `path` is not stored as ordinary text. In VikingDB it is a `TYPE_PATH` index, so a query can choose a tree scope directly instead of scanning path strings as scalar metadata. Agents rarely ask for “any semantically similar thing anywhere.” They ask inside a project, a user memory space, a document subtree, or a service boundary.',
        zh: '关键在于，`path` 不是普通文本字段。在 VikingDB 里它是 `TYPE_PATH` 索引，所以查询可以直接选择树形范围，而不是把路径字符串当作标量元数据去扫。Agent 很少需要“在所有地方找语义相似内容”，它更常在项目、用户记忆空间、文档子树或服务边界内检索。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Capability', zh: '能力' }),
          T({ en: 'Why scalar filtering is not enough', zh: '为什么普通标量过滤不够' }),
        ]}
        rows={[
          [T({ en: 'Depth-aware retrieval', zh: '按深度检索' }), T({ en: 'A directory query must mean current node, one level, or the entire subtree without rewriting every path predicate.', zh: '目录查询需要表达当前节点、一层或整棵子树，而不是为每个路径重写谓词。' })],
          [T({ en: 'Multiple roots', zh: '多根目录' }), T({ en: 'Context lives under users, resources, memories, and tools; each root must remain a scope boundary.', zh: '上下文分布在用户、资源、记忆和工具下；每个根目录都应该是范围边界。' })],
          [T({ en: 'Real-time updates', zh: '实时更新' }), T({ en: 'New files, moves, and deletes must be visible to retrieval without rebuilding the whole tree.', zh: '新文件、移动和删除应该进入检索视图，而不是重建整棵树。' })],
          [T({ en: 'Per-level caches', zh: '分层缓存' }), T({ en: 'Agents often need overview first, detail later; cache boundaries should match the tree.', zh: 'Agent 往往先要概览，再要细节；缓存边界应该和树结构一致。' })],
        ]}
      />

      <DirectoryDepthDemo t={T} />

      <Pre lang="js" filename="vikingdb-path-filter.json">{`{
  "op": "must",
  "field": "path",
  "conds": ["/user/shengmaojia/memories"],
  "para": "-d=1"
}`}</Pre>

      <Ul>
        <Li><InlineCode>d=-1</InlineCode> {T({ en: 'means global retrieval under the current directory.', zh: '表示在当前目录下全局检索。' })}</Li>
        <Li><InlineCode>d=0</InlineCode> {T({ en: 'matches the current node itself.', zh: '只匹配当前节点本身。' })}</Li>
        <Li><InlineCode>d=x</InlineCode> {T({ en: 'searches downward by `x` levels.', zh: '向下检索 `x` 层。' })}</Li>
      </Ul>

      <Pull>{T({
        en: 'The path is not metadata after the fact. It is an indexable scope boundary.',
        zh: '路径不是事后挂上的元数据，而是可索引的检索边界。',
      })}</Pull>

      <H3>{T({ en: 'Progressive Disclosure For Context', zh: '上下文的渐进披露' })}</H3>
      <Table
        headers={[
          T({ en: 'Level', zh: '层级' }),
          T({ en: 'What it stores', zh: '存什么' }),
          T({ en: 'Why agents need it', zh: '为什么 Agent 需要' }),
        ]}
        rows={[
          [<InlineCode>L0</InlineCode>, T({ en: 'Short summary', zh: '短摘要' }), T({ en: 'Fast orientation before spending tokens.', zh: '先低成本判断是否值得继续读。' })],
          [<InlineCode>L1</InlineCode>, T({ en: 'Structure and fields', zh: '结构和字段' }), T({ en: 'Enough shape to plan a query or traversal.', zh: '足够规划查询或遍历。' })],
          [<InlineCode>L2</InlineCode>, T({ en: 'Detailed source content', zh: '详细源内容' }), T({ en: 'Only loaded when precision requires it.', zh: '只有需要精度时再加载。' })],
        ]}
      />

      <H2>{T({ en: 'From Uploaded Files To Context Objects', zh: '从上传文件到上下文对象' })}</H2>
      <P>{T({
        en: 'A single image upload shows why OpenViking is not just a filesystem facade. When `ov add-resource ./docs/images/grafana-demo-dashboard.png` runs, OpenViking creates a directory for that resource, stores the original image as L2, and generates L0 and L1 summaries so an agent can decide whether to inspect the full object. All three levels are embedded with multimodal models and written to the vector database.',
        zh: '一次图片上传就能说明 OpenViking 不是文件系统外壳。执行 `ov add-resource ./docs/images/grafana-demo-dashboard.png` 时，OpenViking 会为这个资源创建目录，把原图作为 L2 保存，并生成 L0、L1 摘要，让 Agent 能先判断是否值得读取完整对象。三个层级都会经过多模态模型向量化并写入向量数据库。',
      })}</P>
      <Pre lang="js" filename="add-image-resource.sh">{`ov add-resource ./docs/images/grafana-demo-dashboard.png

# creates a resource URI similar to:
viking://resources/images/20260509/upload_321e98a827a0461f8721c683d726cbec_png`}</Pre>
      <Table
        headers={[
          T({ en: 'Input', zh: '输入' }),
          T({ en: 'Stored shape', zh: '存储形态' }),
          T({ en: 'Agent value', zh: 'Agent 价值' }),
        ]}
        rows={[
          [<InlineCode>grafana-demo-dashboard.png</InlineCode>, T({ en: 'L0 summary, L1 structure, L2 image', zh: 'L0 摘要、L1 结构、L2 图片' }), T({ en: 'Searchable before the full image is loaded.', zh: '不加载完整图片也能先被检索和判断。' })],
          [T({ en: 'Code repository file', zh: '代码仓库文件' }), T({ en: 'Original relative path preserved', zh: '保留原始相对路径' }), T({ en: 'Agents can navigate like code while retrieval stays semantic.', zh: 'Agent 能像读代码一样导航，同时保留语义检索。' })],
          [T({ en: 'Wiki or document subtree', zh: 'Wiki 或文档子树' }), T({ en: '`viking://` URI hierarchy', zh: '`viking://` URI 层级' }), T({ en: 'Search can stay inside the intended knowledge scope.', zh: '检索可以停留在预期知识范围内。' })],
        ]}
      />

      <H2 id="distributed-consistency">{T({ en: 'Distributed By Decoupling Storage', zh: '通过存储解耦实现分布式' })}</H2>
      <P>{T({
        en: 'The open-source distribution starts as a single-machine service, but the architecture is pointed at managed deployment. The important move is to run OpenViking instances without data disks: vector storage, filesystem storage, logs, and telemetry are abstracted behind middleware interfaces.',
        zh: '开源版本默认以单机方式启动，但架构目标是托管化部署。关键动作是让 OpenViking 实例“无数据盘”运行：向量存储、文件系统、日志和遥测都通过中间件接口隔离。',
      })}</P>
      <P>{T({
        en: 'The open-source build also avoids nonessential dependencies such as Redis and Kafka. Account information, temporary working directories, transactions, task records, and work queues are kept behind the same filesystem abstraction. That makes the local path easy to operate, while leaving a clear place to swap in managed storage later.',
        zh: '开源版本也刻意避免 Redis、Kafka 这类非必要依赖。账号信息、临时工作目录、事务、任务记录和工作队列都收在统一的文件系统抽象后面。这样本地部署容易跑起来，也为后续切换托管存储留下清晰接口。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Mode', zh: '模式' }),
          T({ en: 'What happens', zh: '怎么工作' }),
          T({ en: 'Why it matters', zh: '价值' }),
          T({ en: 'Current caveat', zh: '当前边界' }),
        ]}
        rows={[
          [T({ en: 'Full read-write', zh: '完整读写' }), T({ en: 'Every instance accepts reads and writes.', zh: '每个实例都能接收读写请求。' }), T({ en: 'Simpler scaling model and likely default direction.', zh: '扩展模型更简单，也更可能成为默认方向。' }), T({ en: 'Heavy writes can occupy CPU in a Python single-process server.', zh: '重写入可能占用 Python 单进程服务的 CPU。' })],
          [T({ en: 'Read-write separation', zh: '读写分离' }), T({ en: 'Write and read clusters are separated.', zh: '写集群和读集群分离。' }), T({ en: 'Better isolation and availability boundaries.', zh: '隔离性和可用性边界更清楚。' }), T({ en: 'Currently manual and not the recommended default.', zh: '当前依赖手动拆分，不是推荐默认模式。' })],
        ]}
      />
      <Table
        headers={[
          T({ en: 'Layer', zh: '层' }),
          T({ en: 'Consistency expectation', zh: '一致性预期' }),
          T({ en: 'OpenViking responsibility', zh: 'OpenViking 要补的部分' }),
        ]}
        rows={[
          [T({ en: 'VikingDB', zh: 'VikingDB' }), T({ en: 'Eventual consistency in managed vector storage.', zh: '托管向量存储提供最终一致性。' }), T({ en: 'Design retrieval and retries around visibility delay.', zh: '围绕可见性延迟设计检索和重试。' })],
          [T({ en: 'Embedded vector database', zh: '内嵌向量数据库' }), T({ en: 'Strong consistency on a single machine.', zh: '单机内可提供强一致。' }), T({ en: 'Keep the local mode simple and predictable.', zh: '保持本地模式简单可预期。' })],
          [T({ en: 'Distributed filesystem', zh: '分布式文件系统' }), T({ en: 'Usually strong, still with ordering edge cases.', zh: '通常强一致，但仍有时序边界问题。' }), T({ en: 'Protect writes with file and directory locks.', zh: '用文件锁和目录锁保护写入。' })],
        ]}
      />
      <ConsistencyLockMatrix t={T} />
      <Callout type="warn">
        <P>{T({
          en: 'Locks and transactions are not finished theory here. The current implementation has pessimistic file/directory locks and basic rollback, while the long-term consistency model is still being argued through.',
          zh: '锁和事务还不是一个已经完全定型的理论。当前已有悲观文件锁、目录锁和基础回滚，但长期一致性模型仍在论证。',
        })}</P>
      </Callout>

      <H2 id="identity-permissions">{T({ en: 'Identity: Treat Agents As Database Users', zh: '身份：把 Agent 当成数据库用户' })}</H2>
      <P>{T({
        en: 'The hardest multi-tenant question is not accounts. It is whether an agent is subordinate to a human user, owns data by itself, or should be treated as a peer. OpenViking went through all three designs and is converging on the peer model.',
        zh: '多租户最难的问题不是账号，而是 Agent 到底是隶属于人、自己拥有数据，还是应该被当作平等主体。OpenViking 讨论过三版，正在收敛到 Peer 模型。',
      })}</P>
      <P>{T({
        en: 'Local multi-tenancy starts with a root API key and explicit user registration. Hosted OpenViking hides the root key and exposes user capacity through service tiers instead. The product surface changes, but the invariant stays the same: every read and write must carry a real identity before it touches private context.',
        zh: '本地多租户从 root API Key 和显式用户注册开始。托管版不会暴露 root key，而是通过服务档位体现用户容量。产品表面不一样，但不变量相同：任何读写在触碰私有上下文前，都必须带着真实身份。',
      })}</P>
      <IdentityEvolution t={T} />
      <P>{T({
        en: 'This is a privacy decision as much as a modeling decision. A customer-service agent may manage memories for visitors who are not registered OpenViking users. Forcing those visitors into the same `User` abstraction makes the authorization graph less true and less safe.',
        zh: '这不只是建模选择，也是隐私选择。客服 Agent 可能要管理未注册访客的记忆，把这些访客强行塞进同一个 `User` 抽象，会让授权关系既不真实也不安全。',
      })}</P>
      <PrivacyIdentityFlow t={T} />
      <Pre lang="js" filename="local-multitenant.sh">{`# server ov.conf: configure root_api_key before startup
# client ovcli.conf: configure the same root_api_key
ov admin register-user default <your_name>
# client ovcli.conf: use the returned api_key for normal access`}</Pre>

      <H2 id="performance-capacity">{T({ en: 'Performance Is A Pipeline Problem', zh: '性能是链路问题' })}</H2>
      <P>{T({
        en: 'Once the storage model is distributed, capacity is mostly a deployment choice. Performance is harder because write requests touch parsing, splitting, VLM calls, embedding, summarization, memory extraction, IO movement, and locks.',
        zh: '一旦存储模型能分布式，容量更多是部署选型。性能更难，因为写请求会穿过解析、切分、VLM 调用、向量化、摘要、记忆抽取、IO 搬运和锁。',
      })}</P>
      <Callout type="warn">
        <P>{T({
          en: 'The source article is explicit that OpenViking still has performance issues and should be evaluated carefully before production use. The architecture gives the system room to scale, but ingestion latency and write isolation are still active work.',
          zh: '原文明确提醒：OpenViking 仍有性能问题，生产使用前需要认真评估。架构给系统留下了扩展空间，但摄取延迟和写入隔离仍是正在推进的工作。',
        })}</P>
      </Callout>
      <BottleneckGrid t={T} />
      <Table
        headers={[
          T({ en: 'Layer', zh: '层' }),
          T({ en: 'Lightweight mode', zh: '轻量模式' }),
          T({ en: 'Heavy mode', zh: '重载模式' }),
          T({ en: 'Tradeoff', zh: '取舍' }),
        ]}
        rows={[
          [T({ en: 'Vector database', zh: '向量数据库' }), T({ en: 'Shared VikingDB pool with Account/User scalar filters.', zh: '共享 VikingDB 池，并用 Account/User 标量过滤隔离。' }), T({ en: 'Dedicated vector database per OpenViking instance.', zh: '每个 OpenViking 实例独占向量数据库。' }), T({ en: 'Shared mode saves resources; dedicated mode removes the practical index ceiling.', zh: '共享模式省资源；独占模式移除实际索引上限。' })],
          [T({ en: 'Filesystem', zh: '文件系统' }), T({ en: 'Local FS, ByteNAS, or managed shared FS.', zh: '本地 FS、ByteNAS 或托管共享 FS。' }), T({ en: 'TOS/S3 or EFS-like remote storage.', zh: 'TOS/S3 或 EFS 类远端存储。' }), T({ en: 'Local is fast; object storage scales but slows agent loops.', zh: '本地快；对象存储扩展性强，但会拖慢 Agent 循环。' })],
          [T({ en: 'Write pipeline', zh: '写入链路' }), T({ en: 'Queue model calls and embedding work.', zh: '队列化模型调用和向量化工作。' }), T({ en: 'Globally controlled parallel ingestion.', zh: '全局控制的并行摄取。' }), T({ en: 'More throughput, but lock and ordering costs become visible.', zh: '吞吐更高，但锁和时序成本会被放大。' })],
        ]}
      />
      <WritePipelineBottleneck t={T} />
      <H3>{T({ en: 'Current optimization directions', zh: '当前优化方向' })}</H3>
      <Ol>
        <Li>{T({ en: 'Queue and parallelize model calls with global concurrency control.', zh: '队列化并行模型调用，并做全局并发控制。' })}</Li>
        <Li>{T({ en: 'Replace the Go AGFS server path with embedded calls and Rust where transfer cost matters.', zh: '把 Go AGFS Server 链路改成嵌入式调用，在转发成本敏感处用 Rust。' })}</Li>
        <Li>{T({ en: 'Parallelize tree operations such as `find` and `tree`.', zh: '让 `find`、`tree` 等树操作并行化。' })}</Li>
        <Li>{T({ en: 'Reduce copies across receive, work, and visible directories during upload.', zh: '减少上传时接收目录、工作目录、可见目录之间的数据复制。' })}</Li>
      </Ol>

      <H2 id="privacy-security">{T({ en: 'Privacy: Context Is Plaintext', zh: '隐私：上下文即明文' })}</H2>
      <P>{T({
        en: 'A context database stores the material an agent uses to reason. That material is often sensitive by definition. OpenViking handles this with API-key identity, root isolation, user-scoped `viking://user` visibility, optional file encryption, and experimental Skill privacy configs.',
        zh: '上下文数据库保存的是 Agent 用来推理的材料，而这些材料天然可能敏感。OpenViking 用 API Key 身份、root 隔离、`viking://user` 可见范围、可选文件加密，以及实验性的 Skill 隐私配置来处理这个问题。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Control', zh: '控制项' }),
          T({ en: 'Purpose', zh: '目的' }),
        ]}
        rows={[
          [<InlineCode>dev</InlineCode>, T({ en: 'Local development mode without authentication.', zh: '本地开发模式，无鉴权。' })],
          [<InlineCode>api_key</InlineCode>, T({ en: 'Required when the service listens beyond localhost.', zh: '服务监听 localhost 之外地址时强制使用。' })],
          [<InlineCode>ov --sudo</InlineCode>, T({ en: 'Root identity is explicit and limited to admin actions.', zh: 'root 身份显式启用，只用于管理动作。' })],
          [<InlineCode>viking://user</InlineCode>, T({ en: 'Private data scope filtered at the index layer.', zh: '私有数据范围在索引层过滤。' })],
          [T({ en: 'Privacy configs', zh: '隐私配置' }), T({ en: 'Store Skill secrets in protected storage and restore placeholders at read time.', zh: '把 Skill 密钥放进保护区，读取时按占位符恢复。' })],
        ]}
      />
      <P>{T({
        en: 'Encryption is implemented, but it is not free. Different tenants or accounts can use different keys, which improves blast-radius control, but remote storage has to be decrypted before operations such as `grep`. For a context database, privacy controls affect latency and operator ergonomics, not only compliance posture.',
        zh: '加密已经实现，但它不是免费的。不同租户或 Account 可以使用不同密钥，这能缩小泄露半径；但远端存储在执行 `grep` 这类操作前需要先解密。对上下文数据库来说，隐私控制影响的不只是合规姿态，也会影响时延和运维手感。',
      })}</P>
      <Pre lang="js" filename="privacy-config.sh">{`openviking privacy categories
openviking privacy list skill
openviking privacy upsert skill byted-viking-search-knowledgebase \\
  --values-json '{"api_key":"secret-2","base_url":"https://example.com"}'
openviking privacy activate skill byted-viking-search-knowledgebase 2`}</Pre>

      <Hr ornament />

      <H2>{T({ en: 'What To Remember', zh: '应该记住什么' })}</H2>
      <P>{T({
        en: 'The critical architectural insight is that context is not a blob. It has paths, scopes, identities, consistency constraints, performance budgets, and privacy boundaries. OpenViking is useful because it lets agents consume those properties through an interface they can already navigate.',
        zh: '这篇架构最核心的判断是：上下文不是一个 blob。它有路径、范围、身份、一致性约束、性能预算和隐私边界。OpenViking 的价值在于，让 Agent 通过一个自己已经会导航的接口来消费这些属性。',
      })}</P>
      <P>{T({
        en: 'The architecture is still moving from concept to product construction. The open-source release has already produced enough usage, issues, and feedback to make capacity and performance the next hard priorities. The useful thing about the design is not that every consistency or latency question is closed; it is that OpenViking names the database properties context systems need to expose before agents can depend on them.',
        zh: '这套架构仍在从概念走向产品化建设。开源发布已经带来了足够多的使用、issue 和反馈，让容量与性能成为下一阶段硬问题。这个设计的价值不在于所有一致性和时延问题都已经回答完，而在于它把 Agent 依赖上下文系统前必须暴露的数据库属性命名出来。',
      })}</P>
      <P>{T({
        en: 'The source note closes by thanking more than 150 contributors and participants, over 1000 merged changes, and a community that has pushed the project past 23k stars. That matters because the remaining questions are not slideware questions; they are the questions that show up when real agents, data, and users start sharing the same context substrate.',
        zh: '原文最后感谢了 150 多位贡献者和参与者、1000 多次合入，以及把项目推到 23k+ star 的社区。这件事重要，因为剩下的问题不是 PPT 上的问题，而是真实 Agent、真实数据和真实用户开始共享同一个上下文底座时才会出现的问题。',
      })}</P>
    </Article>
  );
};

export default {
  id: 'openviking-context-database-architecture',
  Component: OpenVikingArchitecturePost,
  meta: {
    title: {
      en: 'OpenViking: Inside the Context Database Architecture',
      zh: 'OpenViking：上下文数据库架构介绍',
    },
    description: {
      en: 'How OpenViking turns directory semantics, distributed storage, identity, performance, and privacy into a context database layer for AI agents.',
      zh: 'OpenViking 如何把目录语义、分布式存储、身份权限、性能容量和隐私安全组织成面向 AI Agent 的上下文数据库。',
    },
    cover: '/assets/covers/openviking-context-database-architecture.png',
    publishedAt: '2026-05-12',
    readingTime: 20,
    category: { en: 'Arch', zh: '架构' },
    tags: ['openviking', 'arch', 'context', 'agent'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [
      { name: 'maojia', github: 'MaojiaSheng' },
    ],
  },
};
