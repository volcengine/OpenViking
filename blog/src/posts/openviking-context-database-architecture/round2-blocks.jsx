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
      @media (max-width: 760px) {
        .ovarch2__head,
        .ovarch2-flow,
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
      role: tt(t, { en: 'Identity, jobs, parsers, metadata, telemetry', zh: '身份、任务、解析、元数据、遥测' }),
      contract: tt(t, { en: 'Coordinates reads, writes, retries, and isolation', zh: '协调读写、重试和隔离' }),
      marker: tt(t, { en: 'API + jobs', zh: 'API + 任务' }),
    },
    {
      layer: tt(t, { en: 'Context filesystem', zh: '上下文文件系统' }),
      role: tt(t, { en: 'AGFS/RAGFS, tree operations, summaries', zh: 'AGFS/RAGFS、树操作、摘要' }),
      contract: tt(t, { en: 'Turns context into paths agents can traverse', zh: '把上下文变成 Agent 可遍历路径' }),
      marker: 'ls/find/read',
    },
    {
      layer: tt(t, { en: 'Storage substrate', zh: '存储底座' }),
      role: tt(t, { en: 'VikingDB, embedded vectors, object/file storage', zh: 'VikingDB、内嵌向量、对象/文件存储' }),
      contract: tt(t, { en: 'Durability, retrieval, filters, and artifacts', zh: '持久化、检索、过滤和产物保存' }),
      marker: tt(t, { en: 'index + blob', zh: '索引 + blob' }),
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
      layer: tt(t, { en: 'Managed vector store', zh: '托管向量存储' }),
      consistency: tt(t, { en: 'Visible after a delay', zh: '写后延迟可见' }),
      protection: tt(t, { en: 'Retry after visibility delay', zh: '等待可见后重试' }),
      risk: tt(t, { en: 'Fresh resources may not appear immediately.', zh: '新资源可能暂时搜不到。' }),
    },
    {
      key: 'file',
      layer: tt(t, { en: 'File artifact store', zh: '文件产物存储' }),
      consistency: tt(t, { en: 'Local is strong; remote depends on provider', zh: '本地强一致；远端看存储' }),
      protection: tt(t, { en: 'File lock', zh: '文件锁' }),
      risk: tt(t, { en: 'Concurrent writes can expose partial files.', zh: '并发写可能暴露半成品。' }),
    },
    {
      key: 'directory',
      layer: tt(t, { en: 'Directory namespace', zh: '目录命名空间' }),
      consistency: tt(t, { en: 'Tree structure must stay valid', zh: '树结构必须有效' }),
      protection: tt(t, { en: 'Directory lock', zh: '目录锁' }),
      risk: tt(t, { en: 'Move/delete can race with indexing.', zh: '移动/删除可能和索引竞争。' }),
    },
    {
      key: 'metadata',
      layer: tt(t, { en: 'Metadata and permissions', zh: '元数据和权限' }),
      consistency: tt(t, { en: 'Policy changes should affect reads immediately', zh: '权限变更应立刻影响读取' }),
      protection: tt(t, { en: 'Transaction boundary', zh: '事务边界' }),
      risk: tt(t, { en: 'Policy drift leaks or hides context.', zh: '权限漂移会误放或误拦上下文。' }),
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
    { key: 'receive', tone: theme.blue, title: tt(t, { en: 'Receive', zh: '接收' }), note: tt(t, { en: 'Upload, dedupe, place in work area.', zh: '上传、去重、放入工作区。' }) },
    { key: 'parse', tone: theme.gold, title: tt(t, { en: 'Parse', zh: '解析' }), note: tt(t, { en: 'PDF, Office, code, images, archives.', zh: 'PDF、Office、代码、图片、压缩包。' }) },
    { key: 'model', tone: theme.red, title: tt(t, { en: 'Model calls', zh: '模型调用' }), note: tt(t, { en: 'VLM, embedding, summary, memory extraction.', zh: 'VLM、向量化、摘要、记忆抽取。' }) },
    { key: 'index', tone: theme.green, title: tt(t, { en: 'Index', zh: '索引' }), note: tt(t, { en: 'Vector write and scalar filters.', zh: '向量写入和标量过滤。' }) },
    { key: 'publish', tone: theme.violet, title: tt(t, { en: 'Publish', zh: '发布' }), note: tt(t, { en: 'Move artifacts into visible namespace.', zh: '把产物移动到可见命名空间。' }) },
    { key: 'observe', tone: theme.blue, title: tt(t, { en: 'Observe', zh: '观测' }), note: tt(t, { en: 'Logs, metrics, traces, retries.', zh: '日志、指标、链路、重试。' }) },
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
      note: tt(t, { en: 'Root is admin-only; every actor gets a scoped API key and a visible namespace.', zh: 'root 只做管理；每个主体获得限定 API Key 和可见命名空间。' }),
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
            <Tag>viking://user</Tag>
            <H4 toc={false}>{tt(t, { en: 'Private scope', zh: '私有范围' })}</H4>
            <P>{tt(t, { en: 'Index filters and read APIs enforce visible context.', zh: '索引过滤和读取 API 强制执行可见上下文。' })}</P>
          </div>
          <div className="ovarch2-flow__node">
            <Tag>{tt(t, { en: 'privacy config', zh: '隐私配置' })}</Tag>
            <H4 toc={false}>{tt(t, { en: 'Secrets stay protected', zh: '密钥留在保护区' })}</H4>
            <P>{tt(t, { en: 'Skills receive restored placeholders only when policy allows.', zh: '只有策略允许时，Skill 才拿到恢复后的占位值。' })}</P>
          </div>
        </div>
      </div>
    </BlockShell>
  );
}
