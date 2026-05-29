import React from 'react';
import {
  Article, Lead, P, H2, H3, Callout, Hr, Table, A, Strong,
} from '../../blog-components';

const LLM_PATH = '/post/openviking-benchmark-results/llm.txt';
const PAPER = 'https://arxiv.org/abs/2605.29640';

const card = {
  border: '1px solid var(--th-line)',
  borderRadius: 8,
  background: 'var(--th-bg-2)',
  padding: '1rem',
};

function MetricGrid({ items }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fit, minmax(10.5rem, 1fr))',
      gap: '0.75rem',
      margin: '1.25rem 0',
    }}>
      {items.map(item => (
        <div key={item.label} style={card}>
          <div style={{
            fontFamily: 'var(--th-font-mono)',
            fontSize: '0.78rem',
            color: 'var(--th-mute)',
            marginBottom: '0.45rem',
          }}>{item.label}</div>
          <div style={{
            fontFamily: 'var(--th-font-display)',
            fontSize: 'clamp(1.45rem, 2.6vw, 2.15rem)',
            lineHeight: 1.05,
            fontWeight: 700,
            color: 'var(--th-accent)',
          }}>{item.value}</div>
          <div style={{ color: 'var(--th-mute)', fontSize: '0.9rem', lineHeight: 1.45, marginTop: '0.45rem' }}>
            {item.detail}
          </div>
        </div>
      ))}
    </div>
  );
}

function CompactTable({ headers, rows }) {
  return <Table headers={headers} rows={rows} />;
}

const OpenVikingBenchmarkResults = ({ t }) => {
  const T = t;

  return (
    <Article>
      <Lead>{T({
        en: 'The latest OpenViking benchmark update answers a narrow product question: when OpenViking is added as the context layer for agents and RAG workflows, do accuracy, latency, and token cost move in the right direction at the same time?',
        zh: '这次 OpenViking 评测更新回答一个直接的产品问题：把 OpenViking 接到 Agent 和 RAG 工作流里之后，准确率、时延和 Token 成本能不能同时变好？',
      })}</Lead>

      <MetricGrid items={[
        {
          label: T({ en: 'User Memory', zh: '用户记忆' }),
          value: '80%+',
          detail: T({ en: 'LoCoMo accuracy across OpenClaw, Hermes, and Claude Code integrations.', zh: 'OpenClaw、Hermes、Claude Code 接入后在 LoCoMo 上都达到 80%+ 准确率。' }),
        },
        {
          label: T({ en: 'Agent Memory', zh: 'Agent 经验记忆' }),
          value: '+11.87pp',
          detail: T({ en: 'Airline task accuracy lift on tau2-bench after adding experience memory.', zh: '在 tau2-bench Airline 场景中，加入经验记忆后准确率提升 11.87 个百分点。' }),
        },
        {
          label: T({ en: 'Knowledge Base QA', zh: '知识库问答' }),
          value: '91.00%',
          detail: T({ en: 'HotpotQA accuracy with OpenViking top-20 retrieval at 0.23s retrieval latency.', zh: 'OpenViking top-20 检索在 HotpotQA 上达到 91.00% 准确率，检索耗时 0.23s。' }),
        },
      ]} />

      <Callout type="info" title={T({ en: 'What changed', zh: '这次更新看什么' })}>
        <P>{T({
          en: 'This update covers three benchmark groups: long-conversation user memory, reusable agent experience memory, and knowledge-base QA. This post keeps the focus on those numbers and the product implication behind them.',
          zh: '这次更新覆盖三组评测：长对话用户记忆、可复用 Agent 经验记忆、知识库问答。本文只聚焦这些数字，以及它们对产品判断的含义。',
        })}</P>
      </Callout>

      <Hr ornament />

      <H2 id="user-memory">{T({ en: 'I: User Memory', zh: 'I: 用户记忆' })}</H2>
      <P>{T({
        en: 'LoCoMo tests whether an agent can answer questions that depend on long-range conversation history. The important part is that OpenViking was not measured on one bespoke agent. It was attached to OpenClaw, Hermes, and Claude Code, and all three crossed 80% accuracy.',
        zh: 'LoCoMo 测的是长对话记忆问答：问题往往依赖很早之前的对话。关键不在于 OpenViking 只适配了一个定制 Agent，而是它接到 OpenClaw、Hermes、Claude Code 三个不同基座后，准确率都超过 80%。',
      })}</P>

      <CompactTable
        headers={[
          T({ en: 'Integration', zh: '方案' }),
          T({ en: 'Accuracy', zh: '准确率' }),
          T({ en: 'Avg. query time', zh: '平均耗时' }),
          T({ en: 'Input tokens', zh: '输入 Token' }),
        ]}
        rows={[
          [T({ en: 'OpenClaw native memory', zh: 'OpenClaw 原生 memory-core' }), '24.20%', '95.14s', '392,559,404'],
          [<Strong>OpenClaw + OpenViking</Strong>, <Strong>82.08%</Strong>, <Strong>38.8s</Strong>, <Strong>37,423,456</Strong>],
          [T({ en: 'Hermes native memory', zh: 'Hermes 原生记忆' }), '33.38%', '82.4s', '79,228,398'],
          [<Strong>Hermes + OpenViking</Strong>, <Strong>82.86%</Strong>, <Strong>27.9s</Strong>, <Strong>52,026,755</Strong>],
          [T({ en: 'Claude Code auto-memory', zh: 'Claude Code Auto-Memory' }), '57.21%', '49.1s', '353,306,422'],
          [<Strong>Claude Code + OpenViking</Strong>, <Strong>80.32%</Strong>, <Strong>20.4s</Strong>, <Strong>129,968,899</Strong>],
        ]}
      />

      <P>{T({
        en: 'The efficiency movement is just as important as the accuracy movement. Compared with each native baseline, latency dropped by roughly 58% to 66%, while token use dropped by 34% to 91%.',
        zh: '准确率之外，效率变化同样重要。相对各自原生基线，时延下降约 58% 到 66%，Token 消耗下降 34% 到 91%。',
      })}</P>

      <CompactTable
        headers={[
          T({ en: 'Agent', zh: 'Agent' }),
          T({ en: 'Accuracy lift', zh: '准确率提升' }),
          T({ en: 'Latency reduction', zh: '时延降低' }),
          T({ en: 'Token reduction', zh: 'Token 降低' }),
        ]}
        rows={[
          ['OpenClaw', '24.20% → 82.08% (+3.39×)', '-59.22%', '-91.0%'],
          ['Hermes', '33.38% → 82.86% (+2.48×)', '-66.10%', '-34.3%'],
          ['Claude Code', '57.21% → 80.32% (+1.40×)', '-58.45%', '-63.2%'],
        ]}
      />

      <Hr ornament />

      <H2 id="agent-memory">{T({ en: 'II: Agent Memory', zh: 'II: Agent 经验记忆' })}</H2>
      <P>{T({
        en: 'User memory answers “what does this user care about?” Agent memory answers a different question: “what has this agent learned from previous work that should change the next attempt?” The new results look at both economic simulation and task success.',
        zh: '用户记忆回答“这个用户在意什么”。Agent 经验记忆回答另一个问题：“这个 Agent 过去做事学到了什么，下一次应该怎么改变做法？”这次结果同时看了经济仿真和任务成功率。',
      })}</P>

      <MetricGrid items={[
        {
          label: 'ClawWork',
          value: '+69.34%',
          detail: T({ en: 'Net income after 50 tasks increased from $2,269.77 to $3,843.74.', zh: '完成 50 个任务后的净收入从 $2,269.77 提升到 $3,843.74。' }),
        },
        {
          label: 'ClawWork',
          value: '-22.8%',
          detail: T({ en: 'Average hourly token use dropped from 1,030.3K/h to 872.4K/h.', zh: '平均每小时 Token 消耗从 1,030.3K/h 降到 872.4K/h。' }),
        },
        {
          label: 'tau2-bench',
          value: '+6.87pp / +11.87pp',
          detail: T({ en: 'Retail and Airline accuracy improved after adding OpenViking experience memory.', zh: 'Retail 和 Airline 两个对话 Agent 场景在加入经验记忆后都有提升。' }),
        },
      ]} />

      <CompactTable
        headers={[
          T({ en: 'Setting', zh: '方案' }),
          T({ en: 'Retail accuracy', zh: 'Retail 正确率' }),
          T({ en: 'Airline accuracy', zh: 'Airline 正确率' }),
        ]}
        rows={[
          [T({ en: 'LLM without memory', zh: 'LLM 无记忆' }), '70.94%', '54.38%'],
          [<Strong>LLM + OpenViking experience memory</Strong>, <Strong>77.81% (+6.87pp)</Strong>, <Strong>66.25% (+11.87pp)</Strong>],
        ]}
      />

      <Hr ornament />

      <H2 id="knowledge-base-qa">{T({ en: 'III: Knowledge Base QA', zh: 'III: 知识库问答' })}</H2>
      <P>{T({
        en: 'Knowledge-base QA is where the trade-off becomes visible. Some systems push accuracy by spending many tokens or accepting high retrieval latency. OpenViking aims for a practical point: strong accuracy with low retrieval latency and controlled indexing cost.',
        zh: '知识库问答能直接看出取舍：有些方案靠大量 Token 或高检索延迟换准确率。OpenViking 的目标更务实：准确率要高，检索要快，建库成本也要可控。',
      })}</P>

      <H3 id="hotpotqa">{T({ en: 'HotpotQA', zh: 'HotpotQA' })}</H3>
      <CompactTable
        headers={[
          T({ en: 'Method', zh: '方案' }),
          T({ en: 'Pattern', zh: '检索范式' }),
          'Accuracy',
          T({ en: 'Tokens / QA', zh: '每 QA Token' }),
          T({ en: 'Latency / QA', zh: '每 QA 耗时' }),
        ]}
        rows={[
          ['Naive RAG', T({ en: 'Vector', zh: '向量检索' }), '62.50%', '1,290', '0.11s'],
          ['HippoRAG 2', T({ en: 'Vector + KG', zh: '向量 + 知识图谱' }), '61.00%', '726', '20s'],
          ['LightRAG', T({ en: 'Vector + KG', zh: '向量 + 知识图谱' }), '89.00%', '28,443', '75s'],
          ['LangChain SQL', T({ en: 'SQL agent', zh: 'SQL + Agent' }), '78.00%', '4,776', '132s'],
          ['OpenViking top-5', T({ en: 'Vector', zh: '向量检索' }), '72.75%', '3,154', '0.22s'],
          [<Strong>OpenViking top-20</Strong>, <Strong>{T({ en: 'Vector', zh: '向量检索' })}</Strong>, <Strong>91.00%</Strong>, <Strong>12,533</Strong>, <Strong>0.23s</Strong>],
          ['Nanobot + OpenViking', T({ en: 'Vector + agent', zh: '向量检索 + Agent' }), '87.00%', '71,300', '61.6s'],
        ]}
      />

      <H3 id="single-turn-rag">{T({ en: 'Single-turn RAG Average', zh: '单轮 RAG 均值' })}</H3>
      <P>{T({
        en: 'Across FinanceBench, NaturalQuestions, ClapNQ, Qasper, and SyllabusQA, OpenViking reaches 66.87% average accuracy with 0.19s retrieval latency. Its indexing token cost is 8.67M, about 13.8% of LightRAG in this comparison.',
        zh: '在 FinanceBench、NaturalQuestions、ClapNQ、Qasper、SyllabusQA 五个数据集上，OpenViking 平均准确率 66.87%，检索耗时 0.19s。建库 Token 为 867 万，约为本组 LightRAG 的 13.8%。',
      })}</P>

      <CompactTable
        headers={[
          T({ en: 'Method', zh: '方案' }),
          T({ en: 'Average accuracy', zh: '平均 Accuracy' }),
          T({ en: 'Indexing tokens', zh: '建库 Token' }),
          T({ en: 'Tokens / QA', zh: '每 QA Token' }),
          T({ en: 'Retrieval latency', zh: '检索耗时' }),
        ]}
        rows={[
          ['Naive RAG', '53.93%', '2,755,356', '1,435', '0.13s'],
          ['PageIndex', '36.75%', '5,609,206', '710,480', '84.60s'],
          ['HippoRAG 2', '44.50%', '124,963,618', '637', '18.83s'],
          ['LightRAG', '76.00%', '62,705,469', '27,035', '9.19s'],
          [<Strong>OpenViking</Strong>, <Strong>66.87%</Strong>, <Strong>8,671,538</Strong>, <Strong>3,060</Strong>, <Strong>0.19s</Strong>],
        ]}
      />

      <Hr ornament />

      <H2 id="takeaway">{T({ en: 'What These Numbers Say', zh: '这些数字说明什么' })}</H2>
      <P>{T({
        en: 'The pattern is consistent: OpenViking is strongest when the product needs a context-management loop. Long user history, previous agent attempts, and large knowledge bases all need a layer that can store, narrow, retrieve, and reuse context without forcing every token into the prompt.',
        zh: '这组结果的方向是一致的：OpenViking 的强项在完整的上下文管理循环。长用户历史、Agent 过去的尝试、大知识库问答，都需要一层能存、能收窄、能召回、能复用的上下文系统，而不是把所有东西都塞进 prompt。',
      })}</P>
      <P>{T({
        en: <>The research track is moving in the same direction. The <A href={PAPER}>VikingMem</A> paper has been accepted by VLDB 2026, and OpenViking exposes part of that context-database direction as an open-source system developers can try today.</>,
        zh: <>论文进展也在同一方向上推进：<A href={PAPER}>VikingMem</A> 已被 VLDB 2026 接收，OpenViking 则把其中一部分上下文数据库能力开源出来，供开发者现在就能接入和验证。</>,
      })}</P>
    </Article>
  );
};

export default {
  id: 'openviking-benchmark-results',
  Component: OpenVikingBenchmarkResults,
  meta: {
    title: {
      zh: 'OpenViking 最新评测：用户记忆、Agent 经验记忆与知识库问答',
      en: 'OpenViking Benchmark Update: User Memory, Agent Memory, and Knowledge Base QA',
    },
    description: {
      zh: 'OpenViking 2026 年 5 月评测更新：LoCoMo 用户记忆、ClawWork 和 tau2-bench Agent 经验记忆、HotpotQA 与单轮 RAG 知识库问答结果。',
      en: 'May 2026 OpenViking benchmark update across LoCoMo user memory, ClawWork and tau2-bench agent memory, and HotpotQA / single-turn RAG knowledge-base QA.',
    },
    cover: '/assets/covers/openviking-benchmark-results.png',
    cardCover: '/assets/covers/openviking-benchmark-results.png',
    publishedAt: '2026-05-29',
    updatedAt: '2026-05-29',
    readingTime: { zh: 6, en: 6 },
    category: { zh: '评测', en: 'Benchmarks' },
    tags: ['openviking', 'benchmark', 'memory', 'rag'],
    languages: ['en', 'zh'],
    authors: [{ name: 'OpenViking Team' }],
    llmPath: LLM_PATH,
  },
};
