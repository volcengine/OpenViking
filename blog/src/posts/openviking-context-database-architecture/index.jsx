import React, { useMemo, useState } from 'react';
import {
  Article, Lead, P, H2, H3, H4, Pre, Pull, Callout, Hr,
  Cols, Col, Ol, Li, Ul, Table, A, InlineCode, Tag, Small,
} from '../../blog-components';
import {
  ArchitectureStack,
  ConsistencyLockMatrix,
  PrivacyIdentityFlow,
  RequestPaths,
  WritePipelineBottleneck,
} from './round2-blocks';

const LLM_PATH = '/post/openviking-context-database-architecture/llm.txt';
// Keep table columns readable on phones; .b-table-wrap already scrolls horizontally.
const TABLE_STYLE = '.ov-readable-tables .b-table th, .ov-readable-tables .b-table td { min-width: 8em; }';
const DOCS = 'https://docs.openviking.ai/';
const USER_PEER_POST = '/post/openviking-user-peer-model';
const PARADIGM_POST = '/post/openviking-context-database';
const CODING_AGENT_POST = '/post/openviking-coding-agent';

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
    { path: 'viking://resources/openviking/docs/design/diagrams', level: 3 },
    { path: 'viking://resources/openviking/telemetry', level: 1 },
    { path: 'viking://resources/openviking/telemetry/grafana', level: 2 },
  ]), []);
  const visible = rows.filter(row => depth === -1 || row.level <= depth);
  return (
    <div style={{ ...card, margin: '1.5rem 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <H4 toc={false}>{t({ en: 'Directory depth selector', zh: '目录深度选择器' })}</H4>
          <Small>{t({ en: 'Scope root: viking://resources/openviking. The buttons only change the highlight; the rule is listed below.', zh: '范围根：viking://resources/openviking。按钮只改变高亮，规则写在下方。' })}</Small>
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
      problem: t({ en: 'Account → User → Agent with simple RBAC. It fits "each employee\'s agent sees that employee\'s data", but one service agent cannot serve many visitors and keep separate memory for each.', zh: 'Account → User → Agent，配简单的 RBAC。它适合“每个员工的 Agent 只看这个员工的数据”，但一个服务型 Agent 很难同时服务多个访客、又为每个人分开记忆。' }),
    },
    {
      label: 'V2',
      title: t({ en: 'Agent can own data', zh: 'Agent 可以拥有数据' }),
      problem: t({ en: 'Agents could own private data and relate to users in either direction. Authentication still tied agents to users, so the authorization graph became hard to explain and harder to secure. A personal assistant and a digital twin fit neither shape cleanly.', zh: 'Agent 可以拥有私有数据，与 User 的关系可以反转或组合。但认证上 Agent 仍挂在 User 下，授权关系难解释，也更难保证安全。个人助理和数字分身都套不进去。' }),
    },
    {
      label: 'V3',
      title: t({ en: 'Human and agent are peers', zh: '人和 Agent 是对等主体' }),
      problem: t({ en: 'The current model. Besides root, only a user authenticates, and a user can be a person or an agent service. Whoever that user serves becomes a peer under it.', zh: '当前模型。root 之外只有 user 是认证对象，它可以代表人，也可以代表一个 Agent 服务。它所服务的对象，成为挂在它下面的 peer。' }),
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
    [t({ en: 'Vector database', zh: '向量数据库' }), t({ en: 'Light tenants can share one index separated by account and user fields; large tenants are better served by a dedicated vector store.', zh: '轻量租户可以共享一个索引，用 account、user 字段隔离；大租户更适合独占向量库。' })],
    [t({ en: 'Filesystem', zh: '文件系统' }), t({ en: 'Local disk is fast but easy to lose; S3-compatible storage scales but adds latency to every agent read.', zh: '本地盘快但容易丢；S3 兼容存储能扩展，却会给 Agent 的每次读取加上时延。' })],
    [t({ en: 'Write pipeline', zh: '写入链路' }), t({ en: 'Parsing, splitting, VLM calls, embeddings, summaries, and memory extraction dominate latency.', zh: '解析、切分、VLM、Embedding、摘要和记忆抽取共同决定延迟。' })],
    [t({ en: 'Locks', zh: '锁机制' }), t({ en: 'Path locks serialize conflicting writes. Moves and deletes on large trees hold TREE locks for longer.', zh: '路径锁让冲突写入串行。大目录上的移动和删除会更久地持有 TREE 锁。' })],
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
    <Article className="ov-readable-tables">
      <style>{TABLE_STYLE}</style>
      <Lead>{T({
        en: 'OpenViking starts from a plain problem: useful data exists, but agents still struggle to use it. A model also needs a surface it can act through and a substrate that keeps context; otherwise every task falls back to stuffing material into the prompt.',
        zh: 'OpenViking 从一个朴素的问题出发：数据明明存在，Agent 却很难真正用起来。有了模型，还需要一个 Agent 能操作的入口和一个能存住上下文的底座；否则每个任务都会退回到临时往 prompt 里塞资料。',
      })}</Lead>

      <P dropCap>{T({
        en: 'A model that writes SQL fluently still has to know which tables to query and how they join. Many agent failures have the same shape. Reasoning is not the weak point; the access plan is: where to look, how far to search, which memory belongs to whom, and whether a write is safe. OpenViking calls the system that answers those questions a context database.',
        zh: '模型会写 SQL，不代表它知道该查哪几张表、表之间怎么连接。很多 Agent 任务的失败也是这个形状：推理本身不是短板，短板在访问计划——去哪里找、检索扩到多深、哪条记忆属于谁、这次写入是否安全。OpenViking 把回答这些问题的底座叫作上下文数据库。',
      })}</P>

      <H2 id="why-filesystem">{T({ en: 'Why A Filesystem-Shaped Interface', zh: '为什么接口更像文件系统' })}</H2>
      <P>{T({
        en: 'Most agent context is not born as clean relational records. It is code, documents, PDFs, images, tickets, meetings, chat logs, and memories. Using it is closer to search and recommendation than to transaction processing: first shrink a noisy corpus into a plausible scope, then rank, read, and refine.',
        zh: 'Agent 要用的上下文，大多不是干净的关系型记录，而是代码、文档、PDF、图片、工单、会议、聊天记录和记忆。用这些数据更像搜索和推荐：先把一大堆有噪声的材料压到一个可信的范围，再排序、阅读、细化。',
      })}</P>
      <P>{T({
        en: 'Relational databases remain the right tool for metadata, billing, jobs, and structured state. They are a poor primary interface for agents, because the agent must discover schemas, tables, joins, and valid predicates before it can ask for anything. A path is a cheaper control primitive: pick this project, this user\'s memory space, this document subtree, then search inside it.',
        zh: '关系型数据库仍然适合元数据、计费、任务和结构化状态。但它不适合当 Agent 读取上下文的主入口：Agent 得先弄清 schema、表、join 和合法谓词，才有机会开始找材料。路径是成本更低的控制原语——先限定这个项目、这个用户的记忆空间、这棵文档子树，再在里面检索。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Approach', zh: '范式' }),
          T({ en: 'What it solves', zh: '解决什么' }),
          T({ en: 'Where it breaks for agents', zh: 'Agent 使用时的断点' }),
        ]}
        rows={[
          [T({ en: 'Relational schema', zh: '关系型 schema' }), T({ en: 'Precise operations over typed records.', zh: '对结构化记录做精确操作。' }), T({ en: 'The model must infer tables, joins, columns, and filters before retrieval starts.', zh: '模型要先推断表、连接、字段和过滤条件，检索还没开始就已经很重。' })],
          [T({ en: 'Vector-only RAG', zh: '纯向量 RAG' }), T({ en: 'Semantic entry points over unstructured content.', zh: '为非结构化内容提供语义入口。' }), T({ en: 'As the corpus grows, more near-duplicates compete for a small topK, and one missed recall fails the task.', zh: '资料越多，相近的内容越多，都在争一个很小的 topK；漏召回一次，任务就失败。' })],
          [T({ en: 'Scalar filters and rerankers', zh: '标量过滤和 rerank' }), T({ en: 'Useful narrowing and second-stage ordering.', zh: '提供有用的范围收敛和二阶段排序。' }), T({ en: 'They still depend on candidate generation. A reranker cannot rescue evidence that never entered the candidate set, and it adds latency and cost.', zh: '它们仍依赖候选集质量。没有进入候选集的证据，rerank 救不回来；同时还会增加时延和成本。' })],
          [T({ en: 'Directory semantics', zh: '目录语义' }), T({ en: 'One compact scope parameter before vector search and rerank.', zh: '在向量检索和 rerank 之前，用一个紧凑的参数限定范围。' }), T({ en: 'If the scope is right, the candidate set is smaller and cleaner. If it is wrong, the evidence is excluded, so the scope has to stay cheap to widen.', zh: '范围选对了，候选集更小也更干净；选错了，证据会被排除在外，所以放宽范围也必须便宜。' })],
        ]}
      />
      <P>{T({
        en: 'These are layers, not rivals. OpenViking stacks them in one query: tenant fields and a path narrow the scope, vector search runs inside it, and an optional rerank orders the result. The rest of the architecture follows from that order.',
        zh: '这几种手段不是互相替代的关系。OpenViking 把它们叠在一次查询里：租户字段和路径先限定范围，向量检索在范围内找候选，可选的 rerank 再排一次序。后面的架构都从这个顺序展开。',
      })}</P>

      <H2 id="system-shape">{T({ en: 'The Shape Of The System', zh: '系统的整体形态' })}</H2>
      <P>{T({
        en: 'The implementation is deliberately polyglot. Python owns the server because parsing, document processing, multimodal understanding, model SDKs, and AI dependencies live in that ecosystem, and OpenViking is IO- and pipeline-heavy long before it is CPU-bound. Rust owns the surfaces where startup time, binary delivery, and IO throughput matter: the ov CLI and RAGFS, the content filesystem. C++ carries the embedded vector engine that came from VikingDB, so the project reuses mature indexing code instead of rewriting the hardest part.',
        zh: 'OpenViking 的技术栈是有意拆开的。Python 承担服务端，因为解析、文档处理、多模态理解、模型 SDK 和各种 AI 依赖都在这个生态里；OpenViking 先是 IO 和数据链路密集，CPU 不是最先出现的瓶颈。Rust 承担对启动速度、二进制分发和 IO 吞吐敏感的部分：ov CLI 和内容文件系统 RAGFS。C++ 承接来自 VikingDB 的内嵌向量引擎，复用成熟的索引实现，而不是重写最难的那部分。',
      })}</P>
      <P>{T({
        en: 'RAGFS started life as AGFS, a separate Go server the Python process talked to. It has since been rewritten in Rust and is loaded into the server process as a Python extension, which removes a hop from every file operation.',
        zh: 'RAGFS 的前身是 AGFS，一个由 Python 进程远程调用的独立 Go 服务。它后来用 Rust 重写，并以 Python 扩展的形式加载进服务进程，每次文件操作少了一次进程间转发。',
      })}</P>
      <ArchitectureStack t={T} />
      <P>{T({
        en: 'The split defines each layer\'s contract. Agents speak in commands and URIs. The server enforces identity and runs jobs. VikingFS and RAGFS give context a traversable shape. The vector index and file storage decide what can be retrieved and what persists. Clients reach the server over HTTP; the CLI, SDKs, MCP endpoint, and Skills all use the same API, and VikingBot, the reference agent shipped in the repository, reads and writes through it too.',
        zh: '这个拆分定义了每一层的责任：Agent 用命令和 URI 说话；服务层负责身份和任务；VikingFS 和 RAGFS 给上下文一个可遍历的形状；向量索引和文件存储决定什么能被检索、什么能被持久化。客户端统一通过 HTTP API 访问服务端，CLI、SDK、MCP 端点和 Skills 都走这一套；仓库里随附的参考 Agent VikingBot 也用它读写上下文。',
      })}</P>
      <RequestPaths t={T} />
      <P>{T({
        en: 'The two paths explain a common surprise. A plain add-resource returns once content is parsed and placed, while summaries and vectors are still in the queue; a Git repository import returns even earlier, before the clone finishes. The file becomes readable before it becomes searchable. Workflows that search right after writing should pass --wait or poll the returned task.',
        zh: '这两条路径解释了一个常见的意外：普通的 add-resource 在内容解析、落位之后就返回，摘要和向量还在队列里；导入 Git 仓库返回得更早，克隆还没完成就有响应。文件先变得可读，之后才变得可检索。写完马上要搜的流程，应该加 --wait，或者轮询返回的任务。',
      })}</P>
      <Callout type="info">
        <P>{T({
          en: 'The public docs are the living reference for module boundaries and deployment details: ',
          zh: '模块边界和部署细节以官网文档为准：',
        })}<A href={DOCS}>docs.openviking.ai</A></P>
      </Callout>

      <Hr ornament />

      <H2 id="directory-semantics">{T({ en: 'Directory Semantics Are The Addressing Layer', zh: '目录语义是寻址层' })}</H2>
      <P>{T({
        en: 'Vector search has a scaling problem that hurts RAG more than recommendation. A recommender can recall thousands of candidates through several channels and rely on coarse and fine ranking. An agent cannot pass thousands of chunks downstream. The final context window may hold only tens of chunks, and filling it too full weakens the model before it starts reasoning.',
        zh: '向量检索的规模问题，在 RAG 里比在推荐里更尖锐。推荐系统可以多路召回成千上万条候选，再做粗排和精排；Agent 没法把成千上万段内容交给下游。最终的上下文窗口可能只容得下几十段，而且窗口填得太满，模型还没开始推理就已经变弱。',
      })}</P>
      <P>{T({
        en: 'Scalar filters are the first answer: tenant, owner, time, level, and source type should prune the search space. Directory retrieval is the more general answer. A lot of useful context is already a tree: code, calendars, wikis, books, service trees, category taxonomies, geographies. VikingDB turned that observation into a path-aware index, and OpenViking exposes it through viking:// URIs.',
        zh: '第一层答案是标量过滤：租户、归属人、时间、层级、来源类型，都应该先把检索范围压下来。更通用的答案是目录检索。大量有用的上下文本来就是树：代码、日历、Wiki、图书、服务树、类目体系、地理位置。VikingDB 把这个观察做成了路径感知的索引，OpenViking 再通过 viking:// URI 把它暴露出来。',
      })}</P>
      <P>{T({
        en: 'The detail that matters: the path is not a text field. Every record OpenViking writes to the vector index has a uri field of type path. The engine keeps directory bitmaps for it, so a query can take a whole subtree, or exactly one level of it, by prefix and depth instead of matching path strings row by row. That is why directory semantics lower the cost of generating filters: one path plus a depth is far easier for an agent to produce than a hand-built predicate over an unknown schema.',
        zh: '关键在于，路径不是普通的文本字段。OpenViking 写进向量索引的每条记录都有一个 uri 字段，字段类型就是 path。底层引擎为它维护目录位图，查询可以按前缀和深度直接取出整棵子树或其中某一层，不用把路径当字符串一条条匹配。这才是目录语义降低过滤条件生成成本的原因：一个路径加一个深度，远比在未知 schema 上手写谓词容易。',
      })}</P>
      <Pull>{T({
        en: 'Directory semantics turn filtering into scope selection: pick a logical directory and a depth, then search inside it. SQL-style filters make the agent assemble schema, fields, joins, and predicates, which leaves far more room for invalid conditions.',
        zh: '目录语义把检索过滤变成选范围：选定一个逻辑目录和深度，再在里面检索。SQL 式的过滤要求 Agent 自己拼 schema、字段、join 和谓词，生成无效条件的空间大得多。',
      })}</Pull>
      <Table
        headers={[
          T({ en: 'Directory feature', zh: '目录特性' }),
          T({ en: 'Why prefix matching is not enough', zh: '为什么前缀匹配不够' }),
        ]}
        rows={[
          [T({ en: 'Depth-aware retrieval', zh: '按深度检索' }), T({ en: 'A query must mean the node itself, its direct children, or the whole subtree without rewriting string predicates.', zh: '查询要能表达当前节点、直接子节点或整棵子树，而不是一遍遍改写字符串谓词。' })],
          [T({ en: 'Directory nodes carry content', zh: '目录节点本身有内容' }), T({ en: 'A wiki page can have its own body and child pages. In OpenViking every processed directory has its own abstract and overview. Treating directories as empty prefixes loses both cases.', zh: 'Wiki 页面可以既有正文又有子页面；OpenViking 里每个处理完的目录也有自己的摘要和概览。把目录当成空前缀，这两种情况都会丢。' })],
          [T({ en: 'Multiple roots and facets', zh: '多根目录和多切面' }), T({ en: 'The same corpus may need project, time, category, or geography views, and each root is a search boundary. This is a design direction; see below.', zh: '同一批数据可能需要按项目、时间、类目或地理来看，每个根都是一条检索边界。这是设计方向，见下文。' })],
          [T({ en: 'Index and permission boundary', zh: '索引和权限边界' }), T({ en: 'The path takes part in retrieval, update, and authorization. Tenant and ACL filters are also expressed as path scopes. It is not only a display string.', zh: '路径参与检索、更新和鉴权，租户与 ACL 过滤本身也用路径范围表达。它不只是一个展示用的字符串。' })],
        ]}
      />

      <H3 id="multiple-roots">{T({ en: 'Multiple Roots Mean Multiple Logical Views', zh: '多根树意味着多个逻辑视图' })}</H3>
      <P>{T({
        en: 'A multi-root tree is not several physical copies of one file. The same object would be indexed under several logical trees, and each tree is a different way to narrow retrieval before vector search. A document could live in the project resource tree, appear in a calendar tree by creation time, and be reachable through a category or geography tree when the domain needs it.',
        zh: '多根树不是把同一个文件复制到几个真实目录里，而是让同一个对象被索引到几棵逻辑树下，每棵树都是向量检索之前的一种范围压缩方式。一份文档可以在项目资源树里，也可以按创建时间出现在日历树里；业务需要的话，还可以通过类目树或地理树访问到。',
      })}</P>
      <P>{T({
        en: 'This is where the architecture is headed, not what the API exposes today. The public top-level namespaces are resources, user, and agent. Time is organized as directories inside the resource tree: an image added without an explicit target lands under viking://resources/images/YYYY/MM/DD/. Independent roots such as calendar, geo, or category are not callable yet.',
        zh: '这是架构要去的方向，不是今天接口里已有的东西。当前公开的顶层命名空间只有 resources、user 和 agent。按时间组织，目前是在资源树里建日期目录：不指定目标就导入的图片，会落在 viking://resources/images/年/月/日/ 下面。calendar、geo、category 这样的独立根还不能调用。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Root', zh: '根' }),
          T({ en: 'What it organizes', zh: '按什么组织' }),
          T({ en: 'Query it simplifies', zh: '它简化了什么查询' }),
          T({ en: 'Status', zh: '现状' }),
        ]}
        rows={[
          [<InlineCode>viking://resources/...</InlineCode>, T({ en: 'Project, repository, document, or uploaded resource structure.', zh: '项目、仓库、文档或上传资源的结构。' }), T({ en: 'Search inside this product, repo, folder, or knowledge base.', zh: '在这个产品、仓库、文件夹或知识库里找。' }), T({ en: 'Available', zh: '已有' })],
          [<InlineCode>viking://user/&#123;user&#125;/...</InlineCode>, T({ en: 'One user\'s memories, sessions, private resources, and skills.', zh: '一个用户的记忆、会话、私有资源和技能。' }), T({ en: 'Recall only what this user, or this peer, should see.', zh: '只召回这个用户或这个 peer 该看到的内容。' }), T({ en: 'Available', zh: '已有' })],
          [<InlineCode>viking://resources/images/2026/05/...</InlineCode>, T({ en: 'Date directories inside the resource tree.', zh: '资源树内的日期目录。' }), T({ en: 'Search material added on a known day or month.', zh: '找某天、某月导入的材料。' }), T({ en: 'Available as directories', zh: '以目录形式已有' })],
          [<InlineCode>viking://calendar/...</InlineCode>, T({ en: 'Time buckets as an independent view over all objects.', zh: '覆盖所有对象的独立时间视图。' }), T({ en: 'Memories or material from last week, or around an incident date.', zh: '找上周、或某个事故日期前后的记忆和材料。' }), T({ en: 'Design direction', zh: '设计方向' })],
          [<InlineCode>viking://category/...</InlineCode>, T({ en: 'Domain category, taxonomy, or service tree.', zh: '业务类目、分类体系或服务树。' }), T({ en: 'Search within a topic without the model inferring category fields.', zh: '在某个主题内找，不让模型去推断分类字段。' }), T({ en: 'Design direction', zh: '设计方向' })],
        ]}
      />

      <DirectoryDepthDemo t={T} />

      <Pre lang="json" filename="path-scope-filter.json">{`{
  "op": "must",
  "field": "uri",
  "conds": ["/user/alice/memories"],
  "para": "-d=1"
}`}</Pre>

      <Ul>
        <Li><InlineCode>d=-1</InlineCode> {T({ en: 'searches the whole subtree under the directory.', zh: '在当前目录下整棵子树里检索。' })}</Li>
        <Li><InlineCode>d=0</InlineCode> {T({ en: 'matches the node itself.', zh: '只匹配当前节点本身。' })}</Li>
        <Li><InlineCode>d=x</InlineCode> {T({ en: 'searches downward by x levels.', zh: '向下检索 x 层。' })}</Li>
      </Ul>
      <P>{T({
        en: 'Agents never write this DSL by hand. The target directory passed to find or search is compiled on the server into a d=-1 path scope and merged with account, user, ACL, context-type, and level conditions into a single vector query. Exact URI lookups use d=0.',
        zh: 'Agent 不需要手写这段 DSL。调用 find 或 search 时传入的目标目录，会在服务端编译成 d=-1 的路径范围，再和 account、user、ACL、上下文类型、层级条件合并成一次向量查询。按 URI 精确定位时用的是 d=0。',
      })}</P>

      <Pull>{T({
        en: 'The path is not metadata attached after the fact. It is an indexed scope boundary that applies before vector search, rerank, and reading.',
        zh: '路径不是事后挂上去的元数据，而是在向量检索、rerank 和阅读之前就生效的可索引范围边界。',
      })}</Pull>

      <H3 id="progressive-disclosure">{T({ en: 'Progressive Disclosure For Context', zh: '上下文的渐进披露' })}</H3>
      <Table
        headers={[
          T({ en: 'Level', zh: '层级' }),
          T({ en: 'What it stores', zh: '存什么' }),
          T({ en: 'Why agents need it', zh: '为什么 Agent 需要' }),
        ]}
        rows={[
          [<InlineCode>L0</InlineCode>, T({ en: 'Directory abstract in .abstract.md, 256 characters by default', zh: '目录摘要，存为 .abstract.md，默认 256 字符以内' }), T({ en: 'Vector recall and a cheap first judgment.', zh: '用于向量召回，先低成本判断值不值得往下读。' })],
          [<InlineCode>L1</InlineCode>, T({ en: 'Directory overview in .overview.md, 4,000 characters by default', zh: '目录概览，存为 .overview.md，默认 4000 字符以内' }), T({ en: 'Rerank input and navigation: what is in here, what to open next.', zh: '用于 rerank 和导航：这里有什么，下一步打开哪份。' })],
          [<InlineCode>L2</InlineCode>, T({ en: 'Original files or parsed bodies', zh: '原始文件或解析后的正文' }), T({ en: 'Loaded only when precision requires it.', zh: '需要精确时才加载。' })],
        ]}
      />
      <P>{T({
        en: 'L0 and L1 are directory-level sidecars. File summaries roll up into the directory\'s overview, and the abstract is taken from the overview. A parent summarizes its children, but large directories use a stable sample of at most 32 direct children by default, and an overview can lag behind recent changes. Absence from a summary does not mean the source is absent. The layers control how much an agent reads; they do not force a search to descend level by level. One query can hit a record at any level directly.',
        zh: 'L0 和 L1 是目录级的 sidecar。文件的摘要汇总进所在目录的概览，摘要再从概览里提取。父目录概括子项内容；大目录默认对最多 32 个直接子项做稳定采样，概览也可能尚未反映最近的更新。因此，摘要中没有提及，不代表资料不存在。分层控制的是 Agent 读多少，并不要求检索逐层下钻：一次查询可以直接命中任何一层的记录。',
      })}</P>

      <H2 id="uri-multimodal">{T({ en: 'Files, Virtual URIs, And Multimodal Objects', zh: '文件、虚拟 URI 和多模态对象' })}</H2>
      <P>{T({
        en: 'viking:// is a logical database namespace, not a physical storage path. The source path can be kept as provenance, while the physical key stays internal: viking://resources/docs/auth is stored under /local/{account_id}/resources/docs/auth, with the account prefix added by the server. The visible URI comes from the upload command, a parent the user names, or OpenViking defaults, and it links the stored object to its rows in the vector index.',
        zh: 'viking:// 是逻辑上的数据库命名空间，不是后端真实的存储路径。原始来源路径可以作为来源信息保留，真实的存储 key 留在系统内部：viking://resources/docs/auth 实际存放在 /local/{account_id}/resources/docs/auth，account 前缀由服务端加上。展示给 Agent 的 URI 由上传命令、用户指定的父目录或默认规则决定，并用它把存储对象和向量索引里的记录关联起来。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Path type', zh: '路径类型' }),
          T({ en: 'Example', zh: '示例' }),
          T({ en: 'Purpose', zh: '用途' }),
        ]}
        rows={[
          [T({ en: 'Source path', zh: '来源路径' }), <InlineCode>./docs/images/demo.png</InlineCode>, T({ en: 'Provenance: where the content came from.', zh: '来源追踪：内容最初从哪里来。' })],
          [T({ en: 'Physical storage key', zh: '真实存储路径' }), T({ en: 'Internal only', zh: '只在系统内部使用' }), T({ en: 'Placement on local disk, S3-compatible storage, or a backup backend.', zh: '在本地盘、S3 兼容存储或备份后端里的真实落位。' })],
          [T({ en: 'Canonical URI', zh: '规范 URI' }), <InlineCode>viking://resources/images/2026/05/09/...</InlineCode>, T({ en: 'Stable identity for read, cite, permission, update, and delete.', zh: '读取、引用、鉴权、更新和删除用的稳定身份。' })],
          [T({ en: 'Matched view URI (planned with multiple roots)', zh: '命中视图 URI（多根树的设想）' }), <InlineCode>viking://calendar/2026/05/09/...</InlineCode>, T({ en: 'Would explain which logical root made a result relevant; it may differ from the canonical URI.', zh: '说明结果是从哪棵逻辑树命中的，可以不同于规范 URI。' })],
        ]}
      />
      <P>{T({
        en: 'With one logical view, the canonical URI and the matched URI are the same. Once multiple roots exist, retrieval should show the matched view so the agent knows why an item appeared, while reads and writes still target the canonical URI.',
        zh: '只有一个逻辑视图时，规范 URI 和命中 URI 是同一个。等多根树落地后，检索结果应该展示命中视图，让 Agent 知道结果为什么出现；读写操作仍然落到规范 URI 上。',
      })}</P>
      <P>{T({
        en: 'Multimodality is a separate axis from directory semantics. Text, code, PDFs, and images all benefit from path-scoped retrieval. Images make the difference easy to see: a query may hit the text summary the VLM wrote for the image, the image embedding itself, or both. The directory decides where to search; the embeddings decide what is similar inside that scope. Image-to-image search needs the embedding model configured as multimodal; a text-only model still indexes the image\'s summary but cannot take an image as the query.',
        zh: '多模态和目录语义是两条轴，不应该混在一起。文本、代码、PDF 和图片都需要按路径限定范围；图片只是更容易看出差别：一次查询可能命中 VLM 为图片写的文字摘要，也可能命中图片本身的向量，或者两者都命中。目录决定在哪里搜，向量决定范围内什么相似。以图搜图要求 Embedding 模型配置成 multimodal；纯文本模型仍会索引图片的文字摘要，但不能接收图片作为查询。',
      })}</P>
      <Pre lang="bash" filename="add-image-resource.sh">{`ov add-resource ./docs/images/demo.png --wait
# Without --to, images land in date directories:
# viking://resources/images/2026/05/09/...

ov find "architecture diagram with three storage layers" --uri viking://resources/images
ov find --image ./query.png --uri viking://resources/images`}</Pre>
      <Table
        headers={[
          T({ en: 'Input', zh: '输入' }),
          T({ en: 'Stored shape', zh: '存储形态' }),
          T({ en: 'Agent value', zh: 'Agent 价值' }),
        ]}
        rows={[
          [<InlineCode>demo.png</InlineCode>, T({ en: 'VLM-written summary plus the image as L2; image vector when the embedding is multimodal', zh: 'VLM 写的文字摘要，原图作为 L2；Embedding 为 multimodal 时还有图片向量' }), T({ en: 'Found through the summary text or through image similarity.', zh: '既可以通过文字摘要命中，也可以通过图片相似度命中。' })],
          [T({ en: 'Code repository', zh: '代码仓库' }), T({ en: 'Repository layout kept as directories', zh: '仓库结构保留为目录' }), T({ en: 'Agents navigate it like code while retrieval stays semantic.', zh: 'Agent 能像读代码一样导航，同时保留语义检索。' })],
          [T({ en: 'PDF or Office document', zh: 'PDF 或 Office 文档' }), T({ en: 'Parsed into Markdown, split by structure', zh: '解析为 Markdown，按结构拆分' }), T({ en: 'Sections can be read one at a time; L2 is the parsed text, not the original bytes.', zh: '可以按章节逐段阅读；L2 是解析后的正文，不是原文件的逐字节副本。' })],
        ]}
      />

      <H2 id="distributed-consistency">{T({ en: 'Distributed By Decoupling Storage', zh: '通过存储解耦实现分布式' })}</H2>
      <P>{T({
        en: 'The open-source distribution starts as a single-machine service, but the architecture is pointed at managed deployment. The key move is to separate the instance from the data. The vector index can be the in-process engine, a remote HTTP service, or Volcengine VikingDB; file storage can be local disk or S3-compatible storage. With both on remote services, an instance holds no authoritative data of its own.',
        zh: '开源版本默认以单机方式启动，但架构是朝托管化部署设计的。关键动作是把实例和数据分开：向量索引可以是进程内的本地引擎，也可以是远端 HTTP 服务或火山引擎 VikingDB；文件存储可以是本地盘，也可以是 S3 兼容存储。两者都放到远端服务上时，实例本身不保存权威数据。',
      })}</P>
      <P>{T({
        en: 'The open-source build also avoids mandatory dependencies such as Redis and Kafka. Temporary working directories, task records, work queues, and path locks sit behind the same filesystem abstraction: queues persist in SQLite on top of it, and locks are lock files by default. When several processes must coordinate locks, the lock provider can switch to Redis. That is an option, not a prerequisite for starting the server.',
        zh: '开源版本也刻意不引入 Redis、Kafka 这类必需依赖。临时工作目录、任务记录、工作队列和路径锁都收在同一个文件系统抽象后面：队列在这一层之上用 SQLite 持久化，路径锁默认就是锁文件。多个进程需要协调锁时，可以把锁换成 Redis 实现。这是可选项，不是启动服务的前提。',
      })}</P>
      <P>{T({
        en: 'Running several instances adds two concerns. Uploaded files land on the instance that received the request by default, so replicas need the shared upload mode. Path locks need a provider every process can see. Primary/backup storage can replicate writes to backup backends, synchronously or asynchronously, but it never promotes a backup on its own.',
        zh: '多实例部署要多处理两件事。上传的临时文件默认落在接收请求的那台实例上，多副本时要切到共享上传模式；路径锁要换成所有进程都能看到的实现。主备存储可以把写入同步或异步复制到备份后端，但不会自动把备份提升为主。',
      })}</P>
      <P>{T({
        en: 'Beyond that, there are two ways to lay out instances. Both are architectural tradeoffs to design for, not deployment modes that ship ready to use:',
        zh: '在此之上，实例怎么排布有两种思路。它们是需要自己设计的架构取舍，不是开箱即用的部署模式：',
      })}</P>
      <Table
        headers={[
          T({ en: 'Approach', zh: '思路' }),
          T({ en: 'What happens', zh: '怎么工作' }),
          T({ en: 'Why it matters', zh: '价值' }),
          T({ en: 'Caveat', zh: '边界' }),
        ]}
        rows={[
          [T({ en: 'Full read-write', zh: '完整读写' }), T({ en: 'Every instance accepts reads and writes.', zh: '每个实例都能接收读写请求。' }), T({ en: 'Simpler scaling model.', zh: '扩展模型更简单。' }), T({ en: 'Needs the shared upload area and cross-process locks above. A heavy write can occupy the CPU of a Python server process and slow reads on the same instance.', zh: '需要上面说的共享上传区和跨进程路径锁。一次重写入可能占满 Python 服务进程的 CPU，拖慢同一实例上的读取。' })],
          [T({ en: 'Read-write separation', zh: '读写分离' }), T({ en: 'Writes and reads go to separate groups of instances.', zh: '写请求和读请求分到不同的实例组。' }), T({ en: 'Clearer load isolation and availability boundaries.', zh: '负载隔离和可用性边界更清楚。' }), T({ en: 'Request routing, how soon a write becomes readable, and failover all need their own design, and the setup has to be validated against your workload.', zh: '请求路由、写入后多久能读到、故障切换都要另行设计，并用自己的负载验证。' })],
        ]}
      />
      <Table
        headers={[
          T({ en: 'Layer', zh: '层' }),
          T({ en: 'Consistency expectation', zh: '一致性预期' }),
          T({ en: 'OpenViking responsibility', zh: 'OpenViking 要补的部分' }),
        ]}
        rows={[
          [T({ en: 'VikingDB', zh: 'VikingDB' }), T({ en: 'Eventual consistency in the managed vector store.', zh: '托管向量存储提供最终一致性。' }), T({ en: 'Design retrieval and retries around visibility delay.', zh: '围绕写后可见的延迟设计检索和重试。' })],
          [T({ en: 'Embedded vector engine', zh: '内嵌向量引擎' }), T({ en: 'Strong consistency on a single machine.', zh: '单机内可提供强一致。' }), T({ en: 'Keep the local mode simple and predictable.', zh: '保持本地模式简单、可预期。' })],
          [T({ en: 'Distributed or object storage', zh: '分布式或对象存储' }), T({ en: 'Usually strong, still with ordering edge cases.', zh: '通常强一致，但仍有时序上的边界问题。' }), T({ en: 'Protect writes with path locks.', zh: '用路径锁保护写入。' })],
        ]}
      />
      <P>{T({
        en: 'One principle orders everything else: files are the source of truth and the vector index is derived from them. An index can be rebuilt from retained files; lost files can only come back from a backup. So OpenViking prefers a missing search result to a wrong one. Deleting removes index records first and files second, so search never returns a file that is gone; if the second step fails, the file is still there and a retry finishes the job. Moving copies the content, updates the index, and then removes the source.',
        zh: '有一条原则决定了其余的设计：文件是源数据，向量索引是从它派生的。索引可以从保留的文件重建，文件丢了只能靠备份找回。所以 OpenViking 的取舍是宁可搜不到，也不要搜到坏结果。删除时先删索引、再删文件，检索就不会返回已经不存在的文件；第二步失败了，文件还在，重试即可补完。移动时先复制内容、更新索引，再清理原路径。',
      })}</P>
      <ConsistencyLockMatrix t={T} />
      <Callout type="warn">
        <P>{T({
          en: 'There is no cross-store atomic transaction here. Path locks, implemented in Rust inside RAGFS, keep conflicting writers apart: EXACT covers one path and TREE a subtree. Releasing a lock does not undo what was written. The background half of a session commit resumes from a persistent queue after a restart, and a retried model call need not produce the same text. A stronger consistency model is still under discussion.',
          zh: '这里没有跨存储的原子事务。路径锁由 RAGFS 里的 Rust 代码实现，让冲突的写入互斥：EXACT 锁一个路径，TREE 锁一棵子树。锁释放不会撤销已经写入的数据。会话提交的后台阶段在重启后从持久化队列续跑，而模型重试不保证生成同样的文字。更强的一致性模型仍在讨论中。',
        })}</P>
      </Callout>

      <H2 id="identity-permissions">{T({ en: 'Identity: Treat Agents As Database Users', zh: '身份：把 Agent 当成数据库用户' })}</H2>
      <P>{T({
        en: 'The hardest multi-tenant question is not accounts. It is whether an agent is subordinate to a human user, owns data by itself, or should be treated as a peer. OpenViking went through all three designs and settled on the third.',
        zh: '多租户最难的问题不是账号，而是 Agent 到底隶属于人、自己拥有数据，还是应该被当作对等的主体。OpenViking 讨论过三版，最后落在第三种。',
      })}</P>
      <P>{T({
        en: 'Local multi-tenancy starts with a root API key and explicit user registration. The hosted service does not expose a root key; the console issues user keys directly. The product surface differs, but the invariant holds: every read and write carries a real identity before it touches private context, and the root key manages accounts and users but cannot read tenant data in API-key mode.',
        zh: '本地多租户从 root API Key 和显式注册用户开始。托管版不暴露 root key，控制台直接发放用户 Key。产品表面不一样，不变量相同：任何读写在碰到私有上下文之前，都必须带着真实身份；root key 只管理 account 和 user，在 API Key 模式下不能读写租户数据。',
      })}</P>
      <IdentityEvolution t={T} />
      <P>{T({
        en: 'This is a privacy decision as much as a modeling one. A customer-service agent may keep memories about visitors who are not registered users and hold no API keys. Forcing those visitors into the User abstraction would make the authorization graph less true and less safe. Separating agents into their own identity type had a similar appeal, letting any agent reach a user\'s global memory, and it fails the same case.',
        zh: '这不只是建模上的选择，也是隐私上的选择。客服 Agent 可能要为未注册、也没有 API Key 的访客保存记忆。把这些访客硬塞进 User 抽象，授权关系既不真实也不安全。把 Agent 单独做成一种身份，初衷是让任何 Agent 都能访问用户的全局记忆，听起来方便，但在同一个场景下同样站不住。',
      })}</P>
      <P>{T({
        en: <>What landed separates the data owner from the interaction object. The user is the data owner, a person or an agent service holding its own key. The objects that user serves, such as a visitor, a group member, or a code repository, become peers under it: <InlineCode>viking://user/support-bot/peers/customer-alice/memories</InlineCode>. A peer narrows retrieval and reads inside one user; it gets no key and does not create a tenant. The <A href={USER_PEER_POST}>User / Peer post</A> walks through the model, and the <A href={CODING_AGENT_POST}>coding agent plugins</A> use it to keep one memory per repository.</>,
        zh: <>落地的做法是把数据主体和交互对象分开：user 是数据主体，可以是一个人，也可以是一个持有自己 Key 的 Agent 服务；这个 user 所服务的对象——访客、群成员、某个代码仓库——作为 peer 挂在它下面：<InlineCode>viking://user/support-bot/peers/customer-alice/memories</InlineCode>。peer 只在一个 user 内部收窄检索和读取范围，不发 Key，也不产生新的租户。<A href={USER_PEER_POST}>User / Peer 那篇文章</A>完整介绍了这个模型，<A href={CODING_AGENT_POST}>Coding Agent 插件</A>就用它为每个代码仓库保留一份项目记忆。</>,
      })}</P>
      <PrivacyIdentityFlow t={T} />
      <Pre lang="bash" filename="local-multitenant.sh">{`# server ov.conf: set server.root_api_key before startup
# client ovcli.conf: use the same root key for admin commands
ov admin register-user default alice
# write the returned user key back to ovcli.conf for everyday reads and writes`}</Pre>

      <H2 id="performance-capacity">{T({ en: 'Performance Is A Pipeline Problem', zh: '性能是链路问题' })}</H2>
      <P>{T({
        en: 'Once storage can be distributed, capacity is mostly a deployment choice. Performance is harder. Reads, such as RAG queries and memory recall, scale mainly by adding instances. Writes cross parsing, splitting, VLM calls, embedding, summarization, memory extraction, IO movement, and locks.',
        zh: '一旦存储能分布式，容量更多是部署选型。性能更难。读请求——RAG 查询、记忆召回——主要靠多实例横向扩展；写请求要穿过解析、切分、VLM 调用、向量化、摘要、记忆抽取、IO 搬运和锁。',
      })}</P>
      <Callout type="warn">
        <P>{T({
          en: 'The write path still carries real performance cost. Evaluate OpenViking against your own data and concurrency before production use. The architecture leaves room to scale, but ingestion latency and write isolation are active work.',
          zh: '写入链路仍有明显的性能成本，生产使用前要用自己的数据和并发认真评估。架构给系统留下了扩展空间，但摄取延迟和写入隔离仍是正在推进的工作。',
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
          [T({ en: 'Vector database', zh: '向量数据库' }), T({ en: 'One shared index, isolated by account and user fields.', zh: '共享一个索引，用 account、user 字段隔离。' }), T({ en: 'A dedicated vector store per large tenant or deployment.', zh: '大租户或大型部署独占向量库。' }), T({ en: 'Sharing saves resources; dedicated stores scale and fail independently.', zh: '共享省资源；独占的扩展和故障互不影响。' })],
          [T({ en: 'Filesystem', zh: '文件系统' }), T({ en: 'Local disk, or a shared filesystem.', zh: '本地盘或共享文件系统。' }), T({ en: 'S3-compatible object storage, optionally with backups.', zh: 'S3 兼容对象存储，可加备份后端。' }), T({ en: 'Local is fast; object storage scales but slows agent loops.', zh: '本地快；对象存储扩展性强，但会拖慢 Agent 循环。' })],
          [T({ en: 'Write pipeline', zh: '写入链路' }), T({ en: 'Queue model calls and embedding work.', zh: '队列化模型调用和向量化工作。' }), T({ en: 'Higher concurrency for VLM and embedding calls.', zh: '提高 VLM 和 Embedding 的并发。' }), T({ en: 'More throughput, but lock and ordering costs become visible.', zh: '吞吐更高，但锁和时序成本会被放大。' })],
        ]}
      />
      <WritePipelineBottleneck t={T} />
      <H3 id="optimization">{T({ en: 'Optimization directions and progress', zh: '优化方向与进展' })}</H3>
      <Ol>
        <Li>{T({ en: 'Queue model calls under concurrency limits. This is now configuration: embedding.max_concurrent defaults to 10 and vlm.max_concurrent to 32.', zh: '模型调用队列化，并控制并发。这一项已经落成配置：embedding.max_concurrent 默认 10，vlm.max_concurrent 默认 32。' })}</Li>
        <Li>{T({ en: 'Replace the Go AGFS server with embedded Rust. Done: RAGFS runs inside the server process.', zh: '把 Go 写的 AGFS 服务换成嵌入式的 Rust 实现。已完成：RAGFS 运行在服务进程内。' })}</Li>
        <Li>{T({ en: 'Parallelize tree operations such as find and tree, which recurse over many directories.', zh: '让 find、tree 这类要递归很多目录的树操作并行化。' })}</Li>
        <Li>{T({ en: 'Reduce copies between the receiving, working, and visible directories during upload. On object storage a move is a copy, so this cost shows up directly in end-to-end latency.', zh: '减少上传时接收目录、工作目录、可见目录之间的复制。在对象存储上，移动就是复制，这部分开销会直接反映到端到端时延上。' })}</Li>
      </Ol>
      <P>{T({
        en: 'For observation, ov status gives a summary, ov observer breaks it down by queue, models, retrieval, and filesystem, and the server exposes /metrics for Prometheus-compatible collectors.',
        zh: '观测上，ov status 给出总览，ov observer 按队列、模型、检索和文件系统分别查看，服务端还提供 /metrics，可接入 Prometheus 一类的采集器。',
      })}</P>

      <H2 id="privacy-security">{T({ en: 'Privacy: Context Is Plaintext', zh: '隐私：上下文即明文' })}</H2>
      <P>{T({
        en: 'A context database stores the material an agent reasons with, and that material is often sensitive by definition. OpenViking handles this with key-based identity, a root key limited to administration, user-scoped visibility enforced in the index, optional file encryption, and privacy configs for Skill secrets.',
        zh: '上下文数据库保存的是 Agent 用来推理的材料，而这些材料天然可能敏感。OpenViking 用基于 Key 的身份、只做管理的 root key、在索引层执行的用户可见范围、可选的文件加密，以及 Skill 密钥的隐私配置来处理这个问题。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Control', zh: '控制项' }),
          T({ en: 'Purpose', zh: '目的' }),
        ]}
        rows={[
          [<InlineCode>dev</InlineCode>, T({ en: 'Local development without authentication; allowed only on localhost.', zh: '本地开发模式，无鉴权，只允许绑定在 localhost。' })],
          [<InlineCode>api_key</InlineCode>, T({ en: 'Keys carry account, user, and role; the server decodes them before any user-scoped access.', zh: 'Key 里带着 account、user 和角色，服务端在访问用户数据前先校验解析。' })],
          [<InlineCode>trusted / oidc / ldap</InlineCode>, T({ en: 'Identity asserted by a trusted gateway or an enterprise identity provider.', zh: '由受信网关或企业身份系统提供身份。' })],
          [<InlineCode>ov --sudo</InlineCode>, T({ en: 'The root key is used only when a command asks for it, and only for admin actions.', zh: 'root key 只在命令显式要求时使用，只用于管理动作。' })],
          [<InlineCode>viking://user</InlineCode>, T({ en: 'For non-root requests the server filters retrieval by account and user space, plus ACLs when enabled, so what search returns matches what the caller may read.', zh: '对非 root 请求，服务端在检索时按 account 和 user 空间过滤（开启 ACL 时再按 ACL），让检索返回的范围和调用方能读取的范围一致。' })],
          [T({ en: 'Privacy configs', zh: '隐私配置' }), T({ en: 'Move Skill secrets into protected storage and restore placeholders at read time.', zh: '把 Skill 里的密钥放进保护区，读取时再按占位符还原。' })],
        ]}
      />
      <P>{T({
        en: 'Encryption is implemented, and it is not free. It uses envelope encryption with a root key, a key per account, and a key per file, so tenants are cryptographically separated and the blast radius of a leak shrinks. The root key can stay in a local file, in Vault, or behind Volcengine KMS. Authorized reads still return plaintext, enabling encryption does not rewrite existing files, and it covers file storage: retrieval text in the vector index needs its own protection. Remote storage has to be decrypted before operations such as grep, so privacy controls affect latency and operator ergonomics, not only compliance.',
        zh: '加密已经实现，但它不是免费的。OpenViking 用信封加密：一个根密钥、每个 account 一个账户密钥、每个文件一个文件密钥，租户在密码学上隔开，泄露的影响范围也更小。根密钥可以放在本地文件、Vault 或火山引擎 KMS 后面。授权读取仍然返回明文；开启加密不会重写已有文件；加密覆盖的是文件存储，向量索引里的检索文本要另外保护。远端存储在执行 grep 这类操作前要先解密，所以隐私控制影响的不只是合规，也影响时延和运维手感。',
      })}</P>
      <Pre lang="bash" filename="privacy-config.sh">{`ov privacy categories
ov privacy list skill
ov privacy skill search-web
ov privacy upsert skill search-web \\
  --values-json '{"api_key":"secret-2","base_url":"https://example.com"}'
ov privacy activate skill search-web 2`}</Pre>
      <P>{T({
        en: 'Secret extraction from a Skill relies on a model and only replaces values it recognizes and matches. It reduces plaintext secrets in shared Skills; it does not guarantee that every secret is found.',
        zh: '从 Skill 里抽取密钥依赖模型，只替换识别并匹配成功的值。它能减少共享 Skill 里的明文密钥，但不保证找出所有密钥。',
      })}</P>

      <Hr ornament />

      <H2 id="takeaways">{T({ en: 'What To Remember', zh: '应该记住什么' })}</H2>
      <P>{T({
        en: 'The core architectural judgment is that context is not a blob. It has paths, scopes, identities, consistency constraints, performance budgets, and privacy boundaries. OpenViking is useful because it lets agents consume those properties through an interface they already know how to navigate.',
        zh: '这套架构最核心的判断是：上下文不是一个 blob。它有路径、范围、身份、一致性约束、性能预算和隐私边界。OpenViking 的价值在于，让 Agent 通过一个自己已经会导航的接口来使用这些属性。',
      })}</P>
      <P>{T({
        en: <>The architecture is still moving from concept to product construction. Open source has brought enough usage, issues, and feedback to make capacity and performance the next hard problems, and some items from earlier plans, such as the Rust filesystem and the peer identity model, have already landed. The design is useful because it names the database properties a context system must expose before agents can depend on it, and it keeps the unfinished consistency and latency work in plain view. For why context engineering becomes a database problem in the first place, read <A href={PARADIGM_POST}>the database paradigm post</A>.</>,
        zh: <>这套架构仍在从概念走向产品化。开源带来了足够多的使用、issue 和反馈，让容量与性能成为下一阶段的硬问题；早先计划里的一些事项，比如 Rust 文件系统和 peer 身份模型，已经落地。这个设计的价值，在于把 Agent 依赖上下文系统之前必须暴露的数据库属性一一命名出来，同时把一致性、时延这些还没做完的问题留在明面上。至于上下文工程为什么会变成数据库问题，可以读<A href={PARADIGM_POST}>数据库范式那一篇</A>。</>,
      })}</P>
      <P>{T({
        en: 'Thanks to everyone who has contributed code, ideas, data, and use cases. The remaining questions are not slideware questions; they are the ones that appear when real agents, real data, and real users start sharing one context substrate.',
        zh: '感谢每一位贡献代码、想法、数据和用例的开发者与参与者。剩下的问题不是 PPT 上的问题，而是真实的 Agent、数据和用户开始共享同一个上下文底座时才会出现的问题。',
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
      en: 'How OpenViking turns directory semantics, decoupled storage, identity, performance, and privacy into a context database layer for AI agents.',
      zh: 'OpenViking 如何把目录语义、存储解耦、身份权限、性能容量和隐私安全组织成面向 AI Agent 的上下文数据库。',
    },
    cover: '/assets/covers/openviking-context-database-architecture.png',
    publishedAt: '2026-05-12',
    updatedAt: '2026-10-04',
    readingTime: { zh: 16, en: 19 },
    category: { en: 'Arch', zh: '架构' },
    tags: ['openviking', 'arch', 'context', 'agent'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [
      { name: 'maojia', github: 'MaojiaSheng' },
    ],
  },
};
