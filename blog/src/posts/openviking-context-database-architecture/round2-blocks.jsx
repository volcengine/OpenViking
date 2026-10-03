import React, { useMemo, useState } from 'react';
import { H3, H4, P, Small, Tag } from '../../blog-components';

const tt = (t, value) => (typeof t === 'function' ? t(value) : value.en || value.zh || '');

const theme = {
  green: 'var(--th-tip)',
  blue: 'var(--th-accent)',
  gold: 'var(--th-accent-2)',
  violet: 'var(--th-ink)',
  red: 'var(--th-warn)',
};

function Round2Styles() {
  return (
    <style>{`
      .ovarch2 {
        --r2-radius: 8px;
        --r2-soft: color-mix(in oklab, var(--th-bg-2) 78%, transparent);
        --r2-hover: color-mix(in oklab, var(--th-accent) 10%, transparent);
        margin: 30px 0;
      }
      .ovarch2, .ovarch2 * { box-sizing: border-box; min-width: 0; }
      .ovarch2__head {
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 14px;
      }
      .ovarch2__kicker {
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 11px;
        letter-spacing: 0.12em;
        line-height: 1.4;
        text-transform: uppercase;
      }
      .ovarch2__grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 12px;
      }
      .ovarch2__card {
        border: 1px solid var(--th-line);
        border-radius: var(--r2-radius);
        background: var(--r2-soft);
        padding: 14px;
      }
      .ovarch2__button {
        border: 1px solid var(--th-line);
        border-radius: 999px;
        background: transparent;
        color: var(--th-fg);
        cursor: pointer;
        font-family: var(--th-font-mono);
        font-size: 12px;
        line-height: 1.2;
        padding: 8px 10px;
      }
      .ovarch2__button[aria-pressed="true"] {
        border-color: var(--th-accent);
        background: var(--th-accent);
        color: var(--th-bg);
      }
      .ovarch2__button:focus-visible,
      .ovarch2__range:focus-visible {
        outline: 2px solid var(--th-accent);
        outline-offset: 2px;
      }
      .ovarch2__muted { color: var(--th-mute); }
      .ovarch2__mono {
        font-family: var(--th-font-mono);
        font-size: 12px;
        line-height: 1.5;
      }
      .ovarch2-stack {
        display: grid;
        border-top: 1px solid var(--th-line);
      }
      .ovarch2-stack__row {
        display: grid;
        grid-template-columns: 44px minmax(112px, 0.72fr) minmax(0, 1.55fr) minmax(92px, 0.55fr);
        gap: 14px;
        align-items: baseline;
        border-bottom: 1px solid var(--th-line);
        padding: 14px 0;
      }
      .ovarch2-stack__index {
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 12px;
        line-height: 1.45;
      }
      .ovarch2-stack__layer {
        color: var(--th-ink);
        font-family: var(--th-font-display);
        font-size: 17px;
        font-weight: 600;
        line-height: 1.25;
      }
      .ovarch2-stack__role {
        display: block;
        color: var(--th-ink);
        font-size: 15px;
        line-height: 1.45;
      }
      .ovarch2-stack__contract {
        display: block;
        margin-top: 2px;
        color: var(--th-mute);
        font-size: 14px;
        line-height: 1.45;
      }
      .ovarch2-stack__marker {
        justify-self: end;
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 12px;
        line-height: 1.45;
        white-space: nowrap;
      }
      .ovarch2-matrix {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }
      .ovarch2-matrix__item {
        border: 1px solid var(--th-line);
        border-radius: var(--r2-radius);
        background: var(--th-bg);
        padding: 12px;
      }
      .ovarch2-matrix__title {
        color: var(--th-ink);
        font-family: var(--th-font-display);
        font-size: 16px;
        font-weight: 600;
        line-height: 1.25;
        margin-bottom: 9px;
      }
      .ovarch2-matrix__fields {
        display: grid;
        gap: 7px;
        font-size: 13.5px;
        line-height: 1.45;
      }
      .ovarch2-matrix__field {
        display: grid;
        grid-template-columns: 72px minmax(0, 1fr);
        gap: 8px;
      }
      .ovarch2-matrix__label {
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 11px;
        letter-spacing: 0.06em;
        line-height: 1.45;
        text-transform: uppercase;
      }
      .ovarch2-pipeline {
        display: grid;
        grid-template-columns: 1fr;
        gap: 10px;
        margin: 0;
        padding: 0;
        list-style: none;
      }
      .ovarch2-pipeline__stage {
        display: grid;
        grid-template-columns: 34px minmax(0, 1fr);
        gap: 10px;
        align-items: start;
        border: 1px solid var(--th-line);
        border-radius: var(--r2-radius);
        background: var(--th-bg);
        padding: 12px;
      }
      .ovarch2-pipeline__marker {
        display: grid;
        place-items: center;
        width: 14px;
        height: 14px;
        margin: 6px 0 0 7px;
        border: 1px solid color-mix(in oklab, var(--tone) 60%, var(--th-line));
        border-radius: 999px;
        background: color-mix(in oklab, var(--tone) 24%, var(--th-bg));
      }
      .ovarch2-pipeline__label {
        display: grid;
        gap: 4px;
        color: var(--th-ink);
      }
      .ovarch2-pipeline__note {
        color: var(--th-mute);
        font-size: 13.5px;
        line-height: 1.45;
      }
      .ovarch2-flow {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 92px minmax(0, 1fr);
        gap: 12px;
        align-items: stretch;
      }
      .ovarch2-flow__boundary {
        display: grid;
        place-items: center;
        min-height: 260px;
        border: 1px dashed var(--th-accent);
        border-radius: var(--r2-radius);
        color: var(--th-accent);
        font-family: var(--th-font-mono);
        font-size: 12px;
        text-align: center;
      }
      .ovarch2-flow__node {
        border: 1px solid var(--th-line);
        border-radius: var(--r2-radius);
        background: var(--th-bg);
        padding: 12px;
      }
      .ovarch2-flow__node + .ovarch2-flow__node { margin-top: 10px; }
      .ovarch2-paths {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
      }
      .ovarch2-paths__lane {
        border: 1px solid var(--th-line);
        border-radius: var(--r2-radius);
        background: var(--th-bg);
        padding: 14px;
      }
      .ovarch2-paths__title {
        color: var(--th-ink);
        font-family: var(--th-font-display);
        font-size: 16px;
        font-weight: 600;
        line-height: 1.25;
        margin-bottom: 4px;
      }
      .ovarch2-paths__steps {
        display: grid;
        gap: 0;
        margin: 10px 0 0;
        padding: 0;
        list-style: none;
      }
      .ovarch2-paths__step {
        display: grid;
        grid-template-columns: 22px minmax(0, 1fr);
        gap: 8px;
        padding: 8px 0;
        border-top: 1px solid var(--th-line);
      }
      .ovarch2-paths__n {
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 12px;
        line-height: 1.6;
      }
      .ovarch2-paths__name {
        display: block;
        color: var(--th-ink);
        font-family: var(--th-font-mono);
        font-size: 13px;
        line-height: 1.5;
      }
      .ovarch2-paths__note {
        display: block;
        color: var(--th-mute);
        font-size: 13.5px;
        line-height: 1.45;
      }
      .ovarch2-paths__tag {
        display: inline-block;
        margin-left: 6px;
        padding: 0 6px;
        border: 1px solid var(--th-line);
        border-radius: 999px;
        color: var(--th-mute);
        font-family: var(--th-font-mono);
        font-size: 11px;
        line-height: 1.6;
        vertical-align: 1px;
      }
      @media (max-width: 760px) {
        .ovarch2__head,
        .ovarch2-flow,
        .ovarch2-paths {
          grid-template-columns: 1fr;
          display: grid;
        }
        .ovarch2-stack__row {
          grid-template-columns: 34px minmax(0, 1fr);
          gap: 4px 12px;
          align-items: start;
        }
        .ovarch2-stack__body,
        .ovarch2-stack__marker {
          grid-column: 2;
        }
        .ovarch2-stack__marker {
          justify-self: start;
          margin-top: 2px;
        }
        .ovarch2-pipeline {
          grid-template-columns: 1fr;
        }
        .ovarch2-matrix {
          grid-template-columns: 1fr;
        }
        .ovarch2-matrix__field {
          grid-template-columns: 64px minmax(0, 1fr);
        }
        .ovarch2-flow__boundary {
          min-height: 54px;
        }
      }
    `}</style>
  );
}

function BlockShell({ t, kicker, title, children, aside }) {
  return (
    <section className="ovarch2">
      <Round2Styles />
      <div className="ovarch2__head">
        <div>
          <div className="ovarch2__kicker">{kicker}</div>
          <H3 toc={false}>{title}</H3>
        </div>
        {aside ? <Small>{aside}</Small> : null}
      </div>
      {children}
    </section>
  );
}

export function ArchitectureStack({ t }) {
  const layers = [
    {
      layer: tt(t, { en: 'Agent surface', zh: 'Agent 入口' }),
      role: tt(t, { en: 'CLI, SDK, MCP, Skills, VikingBot', zh: 'CLI、SDK、MCP、Skills、VikingBot' }),
      contract: tt(t, { en: 'Navigation commands and resource URIs', zh: '导航命令和资源 URI' }),
      marker: 'viking://...',
    },
    {
      layer: tt(t, { en: 'OpenViking server', zh: 'OpenViking 服务层' }),
      role: tt(t, { en: 'Python: identity, parsing, model calls, queues, retrieval, sessions', zh: 'Python：身份、解析、模型调用、队列、检索、会话' }),
      contract: tt(t, { en: 'Coordinates reads, writes, retries, and isolation', zh: '协调读写、重试和隔离' }),
      marker: tt(t, { en: 'API + jobs', zh: 'API + 任务' }),
    },
    {
      layer: tt(t, { en: 'Context filesystem', zh: '上下文文件系统' }),
      role: tt(t, { en: 'VikingFS URIs over RAGFS (Rust, embedded), path locks, L0/L1 sidecars', zh: 'VikingFS URI 层 + RAGFS（Rust，嵌入进程）、路径锁、L0/L1 摘要' }),
      contract: tt(t, { en: 'Turns context into paths agents can traverse', zh: '把上下文变成 Agent 可遍历路径' }),
      marker: 'ls/tree/read',
    },
    {
      layer: tt(t, { en: 'Storage substrate', zh: '存储底座' }),
      role: tt(t, { en: 'Vector index (embedded C++ engine or VikingDB), local or S3-compatible file storage', zh: '向量索引（内嵌 C++ 引擎或 VikingDB）、本地或 S3 兼容文件存储' }),
      contract: tt(t, { en: 'Durability, retrieval, filters, and artifacts', zh: '持久化、检索、过滤和产物保存' }),
      marker: tt(t, { en: 'index + files', zh: '索引 + 文件' }),
    },
  ];

  return (
    <BlockShell
      t={t}
      kicker={tt(t, { en: 'Arch stack', zh: '架构栈' })}
      title={tt(t, { en: 'A database-shaped stack for agent context', zh: '面向 Agent 上下文的数据库化栈' })}
      aside={tt(t, { en: 'Read top-down for request flow, bottom-up for ownership.', zh: '自上而下看请求流，自下而上看能力归属。' })}
    >
      <div className="ovarch2-stack">
        {layers.map((item, index) => (
          <div className="ovarch2-stack__row" key={item.layer}>
            <div className="ovarch2-stack__index">0{index + 1}</div>
            <div className="ovarch2-stack__layer">{item.layer}</div>
            <div className="ovarch2-stack__body">
              <span className="ovarch2-stack__role">{item.role}</span>
              <span className="ovarch2-stack__contract">{item.contract}</span>
            </div>
            <div className="ovarch2-stack__marker">{item.marker}</div>
          </div>
        ))}
      </div>
    </BlockShell>
  );
}

export function ConsistencyLockMatrix({ t }) {
  const rows = [
    {
      key: 'vector',
      layer: tt(t, { en: 'Vector index', zh: '向量索引' }),
      consistency: tt(t, { en: 'Derived from files; a managed store can lag behind writes', zh: '由文件派生；托管向量库写后可见可能有延迟' }),
      protection: tt(t, { en: 'Task status, then retry or rebuild from source', zh: '查任务状态，必要时从源文件重建' }),
      risk: tt(t, { en: 'Fresh resources may not appear immediately.', zh: '新资源可能暂时搜不到。' }),
    },
    {
      key: 'file',
      layer: tt(t, { en: 'File storage', zh: '文件存储' }),
      consistency: tt(t, { en: 'Source of truth; local is strong, remote depends on the backend', zh: '源数据；本地强一致，远端看后端' }),
      protection: tt(t, { en: 'EXACT path lock', zh: 'EXACT 路径锁' }),
      risk: tt(t, { en: 'Concurrent writes can expose partial files.', zh: '并发写可能暴露半成品。' }),
    },
    {
      key: 'directory',
      layer: tt(t, { en: 'Directory namespace', zh: '目录命名空间' }),
      consistency: tt(t, { en: 'Tree structure must stay valid', zh: '树结构必须有效' }),
      protection: tt(t, { en: 'TREE path lock; delete the index before the file', zh: 'TREE 路径锁；删除时先删索引再删文件' }),
      risk: tt(t, { en: 'Move/delete can race with indexing.', zh: '移动/删除可能和索引竞争。' }),
    },
    {
      key: 'metadata',
      layer: tt(t, { en: 'Tenancy and permissions', zh: '租户与权限' }),
      consistency: tt(t, { en: 'Search and read must agree on what is visible', zh: '能搜到的和能读到的必须一致' }),
      protection: tt(t, { en: 'Account/user/ACL filters on both search and read', zh: '检索和读取两侧都按 account、user、ACL 过滤' }),
      risk: tt(t, { en: 'A filter applied on one side only leaks or hides context.', zh: '只在一侧过滤，就会误放或误拦上下文。' }),
    },
  ];

  return (
    <BlockShell
      t={t}
      kicker={tt(t, { en: 'Consistency and locks', zh: '一致性与锁' })}
      title={tt(t, { en: 'Where correctness has to be explicit', zh: '需要显式保证正确性的地方' })}
      aside={tt(t, { en: 'Each layer has a different failure mode.', zh: '不同层的问题不一样。' })}
    >
      <div className="ovarch2-matrix">
        {rows.map(row => (
          <article className="ovarch2-matrix__item" key={row.key}>
            <div className="ovarch2-matrix__title">{row.layer}</div>
            <div className="ovarch2-matrix__fields">
              <div className="ovarch2-matrix__field">
                <span className="ovarch2-matrix__label">{tt(t, { en: 'Consistency', zh: '一致性' })}</span>
                <span>{row.consistency}</span>
              </div>
              <div className="ovarch2-matrix__field">
                <span className="ovarch2-matrix__label">{tt(t, { en: 'Protection', zh: '保护' })}</span>
                <span>{row.protection}</span>
              </div>
              <div className="ovarch2-matrix__field">
                <span className="ovarch2-matrix__label">{tt(t, { en: 'Risk', zh: '风险' })}</span>
                <span>{row.risk}</span>
              </div>
            </div>
          </article>
        ))}
      </div>
    </BlockShell>
  );
}

export function WritePipelineBottleneck({ t }) {
  const stages = [
    { key: 'receive', tone: theme.blue, title: tt(t, { en: 'Receive', zh: '接收' }), note: tt(t, { en: 'Upload into a temporary area; local per instance by default, shared when replicas need it.', zh: '上传到临时区；默认在本实例磁盘，多副本部署可改为共享存储。' }) },
    { key: 'parse', tone: theme.gold, title: tt(t, { en: 'Parse', zh: '解析' }), note: tt(t, { en: 'PDF, Office, Markdown, code, images, archives become files and directories.', zh: 'PDF、Office、Markdown、代码、图片、压缩包被拆成文件和目录。' }) },
    { key: 'publish', tone: theme.violet, title: tt(t, { en: 'Place', zh: '落位' }), note: tt(t, { en: 'TreeBuilder resolves the target URI; content is written to RAGFS under a path lock.', zh: 'TreeBuilder 确定目标 URI，内容在路径锁保护下写入 RAGFS。' }) },
    { key: 'model', tone: theme.red, title: tt(t, { en: 'Model calls', zh: '模型调用' }), note: tt(t, { en: 'Semantic queue builds L0/L1 bottom-up with the VLM; embeddings follow.', zh: '语义队列用 VLM 自底向上生成 L0/L1，随后做 Embedding。' }) },
    { key: 'index', tone: theme.green, title: tt(t, { en: 'Index', zh: '索引' }), note: tt(t, { en: 'Vectors, URI, level, tenant fields, and retrieval text go into the vector index.', zh: '向量、URI、层级、租户字段和检索文本写入向量索引。' }) },
    { key: 'observe', tone: theme.blue, title: tt(t, { en: 'Observe', zh: '观测' }), note: tt(t, { en: 'Task status, queue depth, model latency, /metrics.', zh: '任务状态、队列积压、模型时延、/metrics。' }) },
  ];
  return (
    <BlockShell
      t={t}
      kicker={tt(t, { en: 'Write pipeline', zh: '写入链路' })}
      title={tt(t, { en: 'The bottleneck is a chain, not one database call', zh: '瓶颈是一条链，而不是一次数据库调用' })}
      aside={tt(t, { en: 'Every write crosses several subsystems.', zh: '每次写入都会穿过多个子系统。' })}
    >
      <ol className="ovarch2-pipeline">
        {stages.map(stage => (
          <li
            key={stage.key}
            className="ovarch2-pipeline__stage"
            style={{ '--tone': stage.tone }}
          >
            <span className="ovarch2-pipeline__marker" aria-hidden="true" />
            <div className="ovarch2-pipeline__label">
              <strong>{stage.title}</strong>
              <span className="ovarch2-pipeline__note">{stage.note}</span>
            </div>
          </li>
        ))}
      </ol>
    </BlockShell>
  );
}

export function PrivacyIdentityFlow({ t }) {
  const [mode, setMode] = useState('peer');
  const copy = {
    subordinate: {
      title: tt(t, { en: 'Agent under human user', zh: 'Agent 隶属于人类用户' }),
      note: tt(t, { en: 'Simple to explain, but weak for service agents with many visitors.', zh: '容易解释，但不适合服务多个访客的服务型 Agent。' }),
    },
    owner: {
      title: tt(t, { en: 'Agent owns data directly', zh: 'Agent 直接拥有数据' }),
      note: tt(t, { en: 'Flexible, but makes the permission graph harder to audit.', zh: '灵活，但权限图更难审计。' }),
    },
    peer: {
      title: tt(t, { en: 'Human and agent as peer users', zh: '人和 Agent 是对等用户' }),
      note: tt(t, { en: 'Root is admin-only. A human or an agent authenticates as a user with its own key; visitors it serves become peers under that user, without keys of their own.', zh: 'root 只做管理。人或 Agent 都以 user 身份、用自己的 Key 认证；它服务的访客作为 peer 挂在这个 user 下，不需要单独的 Key。' }),
    },
  };

  return (
    <BlockShell
      t={t}
      kicker={tt(t, { en: 'Privacy boundary', zh: '隐私边界' })}
      title={tt(t, { en: 'Identity flow decides what context can cross', zh: '身份流决定哪些上下文可以越界' })}
      aside={tt(t, { en: 'Switch models to compare privacy pressure.', zh: '切换模型对比隐私压力。' })}
    >
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        {Object.entries(copy).map(([key, value]) => (
          <button
            type="button"
            key={key}
            className="ovarch2__button"
            aria-pressed={mode === key}
            onClick={() => setMode(key)}
          >
            {value.title}
          </button>
        ))}
      </div>
      <div className="ovarch2-flow">
        <div>
          <div className="ovarch2-flow__node">
            <Tag>root</Tag>
            <H4 toc={false}>{tt(t, { en: 'Admin authority', zh: '管理权限' })}</H4>
            <P>{tt(t, { en: 'Register users, rotate API keys, configure global policy.', zh: '注册用户、轮换 API Key、配置全局策略。' })}</P>
          </div>
          <div className="ovarch2-flow__node">
            <Tag>{mode === 'peer' ? 'user:agent' : 'agent'}</Tag>
            <H4 toc={false}>{copy[mode].title}</H4>
            <P>{copy[mode].note}</P>
          </div>
        </div>
        <div className="ovarch2-flow__boundary">
          {tt(t, { en: 'API key + namespace boundary', zh: 'API Key + 命名空间边界' })}
        </div>
        <div>
          <div className="ovarch2-flow__node">
            <Tag>viking://user/&#123;user&#125;</Tag>
            <H4 toc={false}>{tt(t, { en: 'Private scope', zh: '私有范围' })}</H4>
            <P>{tt(t, { en: 'Memories, sessions, and private resources. Index filters and read APIs apply the same boundary.', zh: '记忆、会话和私有资源。索引过滤和读取接口执行同一条边界。' })}</P>
          </div>
          <div className="ovarch2-flow__node">
            <Tag>peers/&#123;peer&#125;</Tag>
            <H4 toc={false}>{tt(t, { en: 'Interaction scope', zh: '交互对象范围' })}</H4>
            <P>{tt(t, { en: 'Narrows content inside one user, such as one visitor or one repository. It does not create a new tenant.', zh: '在一个 user 内部再收窄范围，例如某个访客、某个代码仓库。它不产生新的租户。' })}</P>
          </div>
        </div>
      </div>
    </BlockShell>
  );
}

export function RequestPaths({ t }) {
  const lanes = [
    {
      key: 'write',
      title: tt(t, { en: 'Write path: add-resource', zh: '写入路径：add-resource' }),
      note: tt(t, { en: 'A plain import returns after step 3; summaries and vectors keep running in queues.', zh: '普通导入在第 3 步后返回；摘要和向量在队列里继续处理。' }),
      steps: [
        { name: 'Parser', note: tt(t, { en: 'Source becomes files and directories.', zh: '把源文件拆成文件和目录。' }) },
        { name: 'TreeBuilder', note: tt(t, { en: 'Resolves the target viking:// URI.', zh: '确定目标 viking:// URI。' }) },
        { name: 'ResourceProcessor → RAGFS', note: tt(t, { en: 'Writes content under a path lock, then enqueues semantic work.', zh: '在路径锁保护下写入内容，再把语义处理入队。' }) },
        { name: 'SemanticQueue', async: true, note: tt(t, { en: 'Generates L0/L1 bottom-up, from files to parent directories.', zh: '自底向上生成 L0/L1，从文件到父目录。' }) },
        { name: tt(t, { en: 'Vector index', zh: '向量索引' }), async: true, note: tt(t, { en: 'Embeds and stores URI, level, tenant fields, retrieval text.', zh: '写入向量以及 URI、层级、租户字段和检索文本。' }) },
      ],
    },
    {
      key: 'read',
      title: tt(t, { en: 'Read path: find / search, then read', zh: '读取路径：find / search，然后 read' }),
      note: tt(t, { en: 'One scoped vector query per planned query; reading goes back to files.', zh: '每条查询一次带范围的向量检索；读正文回到文件层。' }),
      steps: [
        { name: tt(t, { en: 'Intent analysis', zh: '意图分析' }), optional: true, note: tt(t, { en: 'search only: turns the session and query into 0–5 typed queries.', zh: '仅 search：结合会话把查询改写成 0–5 条带类型的查询。' }) },
        { name: tt(t, { en: 'Scope filter', zh: '范围过滤' }), note: tt(t, { en: 'Account, user, ACL, target directory, context type, level.', zh: 'account、user、ACL、目标目录、上下文类型、层级。' }) },
        { name: tt(t, { en: 'Global vector search', zh: '全局向量检索' }), note: tt(t, { en: 'Can hit L0, L1, or L2 records directly.', zh: '可以直接命中 L0、L1 或 L2 记录。' }) },
        { name: 'Rerank', optional: true, note: tt(t, { en: 'Once, over 2× candidates, when a reranker is configured.', zh: '配置了 Rerank 时，对 2 倍候选统一重排一次。' }) },
        { name: 'abstract / overview / read', note: tt(t, { en: 'The agent opens the URIs it decides to trust.', zh: 'Agent 打开它决定采信的 URI。' }) },
      ],
    },
  ];

  return (
    <BlockShell
      t={t}
      kicker={tt(t, { en: 'Request paths', zh: '请求路径' })}
      title={tt(t, { en: 'What one write and one read pass through', zh: '一次写入、一次读取分别经过什么' })}
      aside={tt(t, { en: 'Tagged steps run asynchronously or only when configured.', zh: '标注的步骤为异步或按配置启用。' })}
    >
      <div className="ovarch2-paths">
        {lanes.map(lane => (
          <div className="ovarch2-paths__lane" key={lane.key}>
            <div className="ovarch2-paths__title">{lane.title}</div>
            <div className="ovarch2__muted" style={{ fontSize: 13.5, lineHeight: 1.45 }}>{lane.note}</div>
            <ol className="ovarch2-paths__steps">
              {lane.steps.map((step, index) => (
                <li className="ovarch2-paths__step" key={`${lane.key}-${index}`}>
                  <span className="ovarch2-paths__n">{index + 1}</span>
                  <span>
                    <span className="ovarch2-paths__name">
                      {step.name}
                      {step.async ? <span className="ovarch2-paths__tag">{tt(t, { en: 'async', zh: '异步' })}</span> : null}
                      {step.optional ? <span className="ovarch2-paths__tag">{tt(t, { en: 'optional', zh: '可选' })}</span> : null}
                    </span>
                    <span className="ovarch2-paths__note">{step.note}</span>
                  </span>
                </li>
              ))}
            </ol>
          </div>
        ))}
      </div>
    </BlockShell>
  );
}
