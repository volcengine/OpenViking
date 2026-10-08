import React from 'react';
import {
  Article, Lead, P, H2, H3, Pre, Callout, Hr,
  Ol, Li, Ul, Table, A, InlineCode, Strong,
} from '../../blog-components';

const OPENVIKING_GITHUB = 'https://github.com/volcengine/OpenViking?utm_source=blog&utm_medium=article&utm_campaign=deploy-openviking-on-dgx-spark';
const DOCS = 'https://docs.openviking.ai';
const DOC_CUVS = 'https://docs.openviking.ai/en/guides/16-cuvs';
const DOC_CUVS_ZH = 'https://docs.openviking.ai/zh/guides/16-cuvs';
const DOC_QUICKSTART = 'https://docs.openviking.ai/en/getting-started/02-quickstart';
const DOC_QUICKSTART_ZH = 'https://docs.openviking.ai/zh/getting-started/02-quickstart';
const DOC_DEPLOY = 'https://docs.openviking.ai/en/guides/03-deployment';
const DOC_DEPLOY_ZH = 'https://docs.openviking.ai/zh/guides/03-deployment';
const DOC_CONFIG = 'https://docs.openviking.ai/en/guides/01-configuration';
const DOC_CONFIG_ZH = 'https://docs.openviking.ai/zh/guides/01-configuration';
const DOC_AUTH = 'https://docs.openviking.ai/en/guides/04-authentication';
const DOC_AUTH_ZH = 'https://docs.openviking.ai/zh/guides/04-authentication';
const DOC_PUBLIC = 'https://docs.openviking.ai/en/guides/12-public-access';
const DOC_PUBLIC_ZH = 'https://docs.openviking.ai/zh/guides/12-public-access';
const DOC_AGENT_SETUP = 'https://docs.openviking.ai/en/getting-started/04-setup-for-agent';
const DOC_INTEGRATIONS = 'https://docs.openviking.ai/en/agent-integrations/01-overview';
const DOC_INTEGRATIONS_ZH = 'https://docs.openviking.ai/zh/agent-integrations/01-overview';
const DOC_GATEWAY = 'https://docs.openviking.ai/en/guides/15-context-gateway';
const DOC_GATEWAY_ZH = 'https://docs.openviking.ai/zh/guides/15-context-gateway';
const BENCH_README = 'https://github.com/volcengine/OpenViking/blob/main/benchmark/cuvs/README.md';
const CUVS_INSTALL = 'https://docs.nvidia.com/cuvs/installation';
const ARCH_POST = '/post/openviking-context-database-architecture';
const CODING_AGENT_POST = '/post/openviking-coding-agent';
const LLM_PATH = '/post/deploy-openviking-on-dgx-spark/llm.txt';

const SMOKE = `git clone --depth 1 https://github.com/volcengine/OpenViking.git
python OpenViking/examples/cuvs_smoke.py`;

const OV_CONF = `{
  "server": { "host": "127.0.0.1", "port": 1933 },
  "storage": {
    "workspace": "/home/<you>/.openviking/data",
    "vectordb": {
      "backend": "cuvs",
      "distance_metric": "cosine",
      "cuvs": {
        "algorithm": "brute_force",
        "dtype": "float32",
        "max_concurrent_gpu_searches": 1,
        "micro_batching_enabled": false,
        "fallback_to_native": true,
        "filter_cache_size": 16
      }
    }
  },
  "embedding": {
    "dense": {
      "provider": "ollama",
      "model": "qwen3-embedding:0.6b",
      "api_base": "http://localhost:11434/v1",
      "dimension": 1024,
      "input": "text"
    }
  },
  "vlm": {
    "provider": "litellm",
    "model": "ollama/qwen3.6:27b",
    "api_key": "no-key",
    "api_base": "http://localhost:11434",
    "temperature": 0.0,
    "max_retries": 2,
    "extra_request_body": { "num_ctx": 16384, "think": false }
  }
}`;

const AUTO_CONF = `"vectordb": {
  "backend": "local",
  "cuvs": {
    "auto_enable": true,
    "algorithm": "brute_force",
    "auto_memory_reserve_mb": 8192,
    "auto_memory_safety_factor": 2.0,
    "auto_background_rebuild": true
  }
}`;

const SAMPLE_DOC = `# OpenViking release rotation

OpenViking ships a new release every Friday.
Release owners rotate in this order: qin-ctx, zhoujh01, ZaynJarvis, t0saki.`;

const SYSTEMD_UNIT = `[Unit]
Description=OpenViking HTTP Server
After=network.target ollama.service
Wants=ollama.service

[Service]
Type=simple
User=<you>
Group=<you>
WorkingDirectory=/home/<you>
ExecStart=/home/<you>/openviking-env/bin/openviking-server
Environment="OPENVIKING_CONFIG_FILE=/home/<you>/.openviking/ov.conf"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target`;

const AGENT_PROMPT = `Follow this guide to install and start an OpenViking Server on this machine,
using local Ollama models:

https://docs.openviking.ai/en/getting-started/04-setup-for-agent

Then add the NVIDIA cuVS GPU backend as described in:

https://blog.openviking.ai/post/deploy-openviking-on-dgx-spark/

Ask me before choosing models or opening the server to other machines.
Never print API keys back to me.`;

const FIG_CSS = `
.ov-fig { margin: 1.5rem 0; }
.ov-fig__box { border: 1px solid var(--th-line); border-radius: 10px; background: var(--th-bg-2); padding: 18px; overflow-x: auto; }
.ov-fig__cap { color: var(--th-mute); font-size: 13px; margin-top: 8px; text-align: center; line-height: 1.5; }
.ov-flow { display: grid; grid-template-columns: minmax(0, 1fr) 28px minmax(0, 1.15fr) 28px minmax(0, 1fr); align-items: center; gap: 8px; }
.ov-flow--3 { grid-template-columns: minmax(0, 1fr) 28px minmax(0, 1.1fr) 28px minmax(0, 1fr); }
.ov-flow--arch { grid-template-columns: minmax(0, 0.6fr) 28px minmax(0, 2.6fr); }
.ov-flow--inner { grid-template-columns: minmax(0, 1fr) 24px minmax(0, 1fr); }
.ov-node { border: 1px solid var(--th-line); border-radius: 8px; background: var(--th-bg); padding: 10px 12px; }
.ov-node--accent { background: var(--th-accent); color: var(--th-bg); border-color: transparent; }
.ov-node__t { font-family: var(--th-font-display); font-weight: 600; font-size: 14px; overflow-wrap: anywhere; }
.ov-node__s { font-size: 12px; opacity: 0.8; margin-top: 2px; line-height: 1.5; overflow-wrap: anywhere; }
.ov-node__m { font-family: var(--th-font-mono); font-size: 11px; opacity: 0.75; margin-top: 3px; }
.ov-arrow { text-align: center; color: var(--th-mute); font-size: 18px; }
.ov-stack { display: grid; gap: 8px; }
.ov-zone { border: 1.5px dashed var(--th-line); border-radius: 10px; padding: 12px; }
.ov-zone__label { font-family: var(--th-font-mono); font-size: 11px; color: var(--th-mute); margin-bottom: 8px; overflow-wrap: anywhere; }
.ov-steps { display: flex; flex-wrap: wrap; gap: 8px; }
.ov-step { display: flex; align-items: center; gap: 8px; border: 1px solid var(--th-line); border-radius: 999px; padding: 5px 12px 5px 5px; background: var(--th-bg); font-size: 13px; }
.ov-step__n { width: 22px; height: 22px; border-radius: 50%; background: var(--th-accent); color: var(--th-bg); display: flex; align-items: center; justify-content: center; font-family: var(--th-font-mono); font-size: 11px; font-weight: 700; flex-shrink: 0; }
.ov-bar { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 12px; align-items: center; margin: 10px 0; font-size: 13px; }
.ov-bar__track { height: 26px; border-radius: 6px; background: var(--th-bg); border: 1px solid var(--th-line); position: relative; overflow: hidden; }
.ov-bar__fill { height: 100%; background: var(--th-accent); }
.ov-bar__val { position: absolute; left: 10px; top: 0; line-height: 26px; font-family: var(--th-font-mono); font-size: 12px; color: var(--th-ink); mix-blend-mode: normal; }
.ov-modes { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
.ov-mode { border: 1px solid var(--th-line); border-radius: 10px; padding: 14px 16px; background: var(--th-bg); }
.ov-mode--on { border-color: var(--th-accent); box-shadow: inset 3px 0 0 var(--th-accent); }
.ov-mode__t { font-family: var(--th-font-mono); font-size: 13px; font-weight: 700; }
.ov-mode__l { margin: 8px 0 0; padding: 0; list-style: none; font-size: 13px; color: var(--th-mute); line-height: 1.6; }
@media (max-width: 640px) {
  .ov-flow, .ov-flow--3, .ov-flow--arch, .ov-flow--inner { grid-template-columns: minmax(0, 1fr); }
  .ov-arrow { transform: rotate(90deg); }
}
`;

function Fig({ caption, children }) {
  return (
    <figure className="ov-fig">
      <div className="ov-fig__box">{children}</div>
      {caption ? <figcaption className="ov-fig__cap">{caption}</figcaption> : null}
    </figure>
  );
}

function Node({ title, sub, mono, accent }) {
  return (
    <div className={`ov-node${accent ? ' ov-node--accent' : ''}`}>
      <div className="ov-node__t">{title}</div>
      {sub ? <div className="ov-node__s">{sub}</div> : null}
      {mono ? <div className="ov-node__m">{mono}</div> : null}
    </div>
  );
}

const Arrow = () => <div className="ov-arrow" aria-hidden="true">→</div>;

function StepsFigure({ T }) {
  const steps = [
    T({ en: 'Check the machine', zh: '检查机器' }),
    T({ en: 'Ollama and models', zh: 'Ollama 与模型' }),
    T({ en: 'Install OpenViking and cuVS', zh: '安装 OpenViking 与 cuVS' }),
    T({ en: 'Configure', zh: '配置' }),
    T({ en: 'Start and check', zh: '启动与检查' }),
    T({ en: 'Connect the CLI', zh: '连接命令行' }),
    T({ en: 'Verify end to end', zh: '端到端验证' }),
  ];
  return (
    <Fig caption={T({
      en: 'The seven steps. Step 2 ends with a smoke test that checks the GPU path on its own.',
      zh: '七个步骤。第 2 步结束时有一个冒烟测试，可以单独确认 GPU 链路。',
    })}>
      <ol className="ov-steps" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {steps.map((s, i) => (
          <li key={i} className="ov-step"><span className="ov-step__n">{i}</span>{s}</li>
        ))}
      </ol>
    </Fig>
  );
}

function ArchFigure({ T }) {
  return (
    <Fig caption={T({
      en: 'Everything runs on the Spark and listens on loopback. The CLI can sit on any machine that can reach the server.',
      zh: '所有组件都在 Spark 上运行，只监听本机回环地址。命令行可以放在任何能连到服务的机器上。',
    })}>
      <div className="ov-flow ov-flow--arch">
        <Node title={T({ en: 'Your laptop or agent', zh: '你的笔记本或 Agent' })} sub="ov CLI · SDK · MCP" />
        <Arrow />
        <div className="ov-zone">
          <div className="ov-zone__label">DGX Spark · GB10 · {T({ en: '128 GB unified memory', zh: '128 GB 统一内存' })}</div>
          <div className="ov-flow ov-flow--inner">
            <Node accent title="OpenViking server" sub={T({ en: 'import · summarize · index · retrieve', zh: '导入 · 摘要 · 索引 · 检索' })} mono="127.0.0.1:1933" />
            <Arrow />
            <div className="ov-stack">
              <Node title="Ollama" sub={T({ en: 'embedding model + VLM', zh: 'Embedding 模型 + VLM' })} mono="127.0.0.1:11434" />
              <Node title="NVIDIA cuVS" sub={T({ en: 'dense vector search on the GPU', zh: 'GPU 上的稠密向量检索' })} mono={T({ en: 'in the server process', zh: '在服务进程内' })} />
            </div>
          </div>
        </div>
      </div>
    </Fig>
  );
}

function RouteFigure({ T }) {
  return (
    <Fig caption={T({
      en: 'Auto mode decides per query. An explicit cuvs backend always takes the GPU branch.',
      zh: 'auto 模式按每次查询决定走哪条路。显式指定 cuvs 后端时，总是走 GPU 分支。',
    })}>
      <div className="ov-flow ov-flow--3">
        <Node title={T({ en: 'Filter and route', zh: '过滤与路由' })} sub={T({ en: 'URI scope + scalar filters → candidate set', zh: 'URI 范围 + 标量过滤 → 候选集' })} mono="OpenViking" />
        <Arrow />
        <div className="ov-stack">
          <Node accent title="cuVS GPU" sub={T({ en: 'wide scope, index ready, memory fits', zh: '范围广、索引就绪、内存够用' })} />
          <Node title="Native CPU" sub={T({ en: 'few candidates, rebuild pending, memory short, no GPU', zh: '候选很少、正在重建、内存不足、没有 GPU' })} />
        </div>
        <Arrow />
        <Node title={T({ en: 'Results', zh: '结果' })} sub={T({ en: 'rerank · read the original on demand', zh: 'Rerank · 按需读取原文' })} mono="OpenViking" />
      </div>
    </Fig>
  );
}

function ModeCards({ T }) {
  return (
    <Fig caption={T({
      en: 'Start with the explicit backend to verify the GPU path, and switch to auto mode if the LLM and the index share memory.',
      zh: '先用显式后端验证 GPU 链路；如果大模型和索引共用内存，再换成 auto 模式。',
    })}>
      <div className="ov-modes">
        <div className="ov-mode ov-mode--on">
          <div className="ov-mode__t">backend: "cuvs"</div>
          <ul className="ov-mode__l">
            <li>{T({ en: 'Fails fast if the GPU is unavailable', zh: 'GPU 不可用时直接报错' })}</li>
            <li>{T({ en: 'Dense search always on the GPU', zh: '稠密检索始终走 GPU' })}</li>
            <li>{T({ en: 'Best for verification and a dedicated box', zh: '适合验证阶段和专用机器' })}</li>
          </ul>
        </div>
        <div className="ov-mode">
          <div className="ov-mode__t">backend: "local" + auto_enable</div>
          <ul className="ov-mode__l">
            <li>{T({ en: 'Falls back to the CPU index on its own', zh: '自动回退到 CPU 索引' })}</li>
            <li>{T({ en: 'Checks free memory before each build', zh: '每次构建前检查空闲内存' })}</li>
            <li>{T({ en: 'Best when the GPU is shared with the LLM', zh: '适合和大模型共用 GPU 的场景' })}</li>
          </ul>
        </div>
      </div>
    </Fig>
  );
}

function MemoryFigure({ T }) {
  const rows = [['10,000', 39, '39 MiB'], ['100,000', 391, '391 MiB'], ['200,000', 781, '781 MiB']];
  return (
    <Fig caption={T({
      en: 'Device allocation for 1024-dimensional float32 vectors, measured on the Spark. The formula is N × dimension × 4 bytes.',
      zh: '1024 维 float32 向量在设备侧的分配量，Spark 上实测。公式是 N × 维度 × 4 字节。',
    })}>
      {rows.map(([n, v, label]) => (
        <div className="ov-bar" key={n}>
          <div>{n}</div>
          <div className="ov-bar__track">
            <div className="ov-bar__fill" style={{ width: `${Math.max(4, (v / 781) * 100)}%`, opacity: 0.85 }} />
            <span className="ov-bar__val">{label}</span>
          </div>
        </div>
      ))}
    </Fig>
  );
}

// The brand lockup is painted through a mask so it follows the active theme's ink color.
function BrandMark() {
  const mask = 'url(/assets/brand-lockup-light.svg) no-repeat left center / contain';
  return (
    <a
      href={OPENVIKING_GITHUB}
      aria-label="OpenViking on GitHub"
      style={{
        display: 'block', width: 168, height: 31, margin: '0 0 1.5rem',
        background: 'var(--th-ink)', WebkitMask: mask, mask,
      }}
    />
  );
}

const DeployOnDgxSpark = ({ t }) => {
  const T = t;

  return (
    <Article>
      <style>{FIG_CSS}</style>
      <BrandMark />
      <Lead>{T({
        en: 'DGX Spark puts the CPU and GPU on one 128 GB pool of unified memory, which is enough to keep the models, the database, and GPU vector search on a single desktop machine. This guide starts from a clean box. You install Ollama and two local models, install OpenViking and NVIDIA cuVS, switch the vector backend to the GPU, run it as a service, and verify the whole path from import to read-back.',
        zh: 'DGX Spark 的 CPU 和 GPU 共用一块 128 GB 统一内存，足够把模型、数据库和 GPU 向量检索都放在一台桌面机上。这篇文章从一台干净的机器开始：安装 Ollama 和两个本地模型，安装 OpenViking 与 NVIDIA cuVS，把向量后端切到 GPU，做成常驻服务，最后验证从导入到读取的完整链路。',
      })}</Lead>

      <Callout type="info">
        <P>{T({
          en: <>Tested on DGX Spark (GB10, aarch64) with NVIDIA driver 580, CUDA 13.0, Python 3.12, OpenViking 0.4.17.1, cuVS 26.06, and CuPy 14.1. New to OpenViking? Read the <A href={ARCH_POST}>architecture overview</A> first.</>,
          zh: <>测试环境：DGX Spark（GB10，aarch64），NVIDIA 驱动 580，CUDA 13.0，Python 3.12，OpenViking 0.4.17.1，cuVS 26.06，CuPy 14.1。对 OpenViking 还不熟悉？先读<A href={ARCH_POST}>架构介绍</A>。</>,
        })}</P>
      </Callout>

      <StepsFigure T={T} />

      <H2 id="overview">{T({ en: 'What You Will Build', zh: '你将搭出什么' })}</H2>

      <ArchFigure T={T} />

      <P>{T({
        en: 'The embedding model turns text into vectors, and the VLM writes the L0 abstract and L1 overview for every file and directory. cuVS accelerates only the dense vector search step. OpenViking keeps directory scoping, scalar filters, and permissions, and hands cuVS a candidate set to search.',
        zh: 'Embedding 模型把文本变成向量，VLM 为每个文件和目录生成 L0 摘要与 L1 概览。cuVS 只加速稠密向量检索这一步：目录范围、标量过滤和权限仍由 OpenViking 处理，再把候选集交给 cuVS 搜索。',
      })}</P>

      <H2 id="paths">{T({ en: 'Pick a Deployment Path', zh: '先选部署方式' })}</H2>

      <P>{T({
        en: 'OpenViking runs as an HTTP service, and there are several ways to run it. The GPU backend narrows the choice for this guide.',
        zh: 'OpenViking 是一个 HTTP 服务，有几种运行方式。GPU 后端会缩小本文能用的选择。',
      })}</P>

      <Table
        headers={[T({ en: 'Path', zh: '方式' }), T({ en: 'Use it when', zh: '适用场景' }), T({ en: 'With cuVS on the Spark', zh: '在 Spark 上配合 cuVS' })]}
        rows={[
          [T({ en: 'Managed service (Volcengine)', zh: '托管服务（火山引擎）' }), T({ en: 'You want no servers and no local models', zh: '不想运维服务器，也不想跑本地模型' }), T({ en: 'Not applicable', zh: '不适用' })],
          [T({ en: 'Python install (uv or venv)', zh: 'Python 安装（uv 或 venv）' }), T({ en: 'One machine, full control of the environment', zh: '单机部署，完全掌控运行环境' }), <Strong key="a">{T({ en: 'This guide', zh: '本文使用' })}</Strong>],
          [T({ en: 'systemd service', zh: 'systemd 常驻服务' }), T({ en: 'A Linux host that should keep the server up across reboots', zh: '希望重启后服务自动恢复的 Linux 主机' }), T({ en: 'Added on top of the Python install, see below', zh: '在 Python 安装之上添加，见下文' })],
          [T({ en: 'Docker image', zh: 'Docker 镜像' }), T({ en: 'You prefer containers and a mounted volume', zh: '希望用容器和挂载卷' }), T({ en: 'The published image does not install the cuVS packages, so use the Python install for the GPU backend', zh: '官方镜像不包含 cuVS 安装包，GPU 后端请用 Python 安装' })],
          [T({ en: 'Helm / private delivery', zh: 'Helm / 私有化交付' }), T({ en: 'Kubernetes clusters and enterprise rollouts', zh: 'Kubernetes 集群和企业部署' }), T({ en: 'Out of scope here', zh: '不在本文范围内' })],
        ]}
      />

      <P>{T({
        en: <>The <A href={DOC_DEPLOY}>deployment guide</A> covers Docker, Compose, and Helm in full. Before you deploy, decide where the workspace lives, how you will back it up, and who gets which key. The guide's deployment checklist lists these items.</>,
        zh: <><A href={DOC_DEPLOY_ZH}>部署文档</A>完整介绍了 Docker、Compose 和 Helm。部署之前先想清楚三件事：workspace 放在哪里、怎么备份、谁用哪把 Key。文档里的部署检查清单列了这些项目。</>,
      })}</P>

      <Callout type="tip">
        <P>{T({
          en: <>You can also hand the first part of this guide to your coding agent. OpenViking publishes setup instructions written for agents, and the prompt below asks it to follow them and then come back here for the GPU backend. See the <A href={DOC_AGENT_SETUP}>setup guide for agents</A>.</>,
          zh: <>这篇文章的前半部分也可以交给你的 Coding Agent 去做。OpenViking 发布了写给 Agent 看的安装指引，下面的提示词让它按指引安装，再回到本文配置 GPU 后端。见<A href={DOC_AGENT_SETUP}>Agent 安装指引</A>。</>,
        })}</P>
      </Callout>

      <Pre lang="text" filename="prompt" lineNumbers={false}>{AGENT_PROMPT}</Pre>

      <H2 id="prerequisites">{T({ en: 'Step 0: Check the Machine', zh: '第 0 步：检查机器' })}</H2>

      <Pre lang="bash" filename="terminal">{`nvidia-smi                 # driver 580+, CUDA 13.x
python3 --version          # 3.11 or newer (cuVS 26.06 wheels need it)
uname -m                   # aarch64`}</Pre>

      <P>{T({
        en: <>OpenViking itself runs on Python 3.10 or newer, and the cuVS 26.06 packages need 3.11. The CUDA major version decides which cuVS package you install: <InlineCode>cuvs-cu13</InlineCode> for CUDA 13, <InlineCode>cuvs-cu12</InlineCode> for CUDA 12. On the Spark, <InlineCode>nvidia-smi</InlineCode> reports the total memory usage as not supported because the memory is unified. Read per-process usage with <InlineCode>nvidia-smi --query-compute-apps=pid,used_gpu_memory --format=csv</InlineCode> instead.</>,
        zh: <>OpenViking 本身支持 Python 3.10 及以上，cuVS 26.06 的安装包要求 3.11。CUDA 主版本决定装哪个 cuVS 安装包：CUDA 13 用 <InlineCode>cuvs-cu13</InlineCode>，CUDA 12 用 <InlineCode>cuvs-cu12</InlineCode>。Spark 使用统一内存，<InlineCode>nvidia-smi</InlineCode> 的整卡显存用量显示为 Not Supported，要看进程级用量，用 <InlineCode>nvidia-smi --query-compute-apps=pid,used_gpu_memory --format=csv</InlineCode>。</>,
      })}</P>

      <H2 id="ollama">{T({ en: 'Step 1: Install Ollama and Pull the Models', zh: '第 1 步：安装 Ollama 并拉取模型' })}</H2>

      <Pre lang="bash" filename="terminal">{`curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3-embedding:0.6b     # embedding, 1024 dimensions, ~639 MB
ollama pull qwen3.6:27b              # VLM, ~17 GB
curl -fsS http://127.0.0.1:11434/api/tags`}</Pre>

      <P>{T({
        en: 'These are two of the presets the OpenViking setup wizard offers for local models. The table lists more of them, with the RAM the wizard recommends. On a 128 GB Spark every row fits, but the VLM, the vector index, and your other workloads share that one pool, so choose the smallest VLM that gives good summaries.',
        zh: '这两个模型是 OpenViking 安装向导为本地模型提供的预设。下表列出更多预设，以及向导建议的内存。128 GB 的 Spark 放得下每一行，但 VLM、向量索引和你的其他任务共用同一块内存，所以选能给出合格摘要的最小 VLM 就好。',
      })}</P>

      <Table
        headers={[T({ en: 'Model', zh: '模型' }), T({ en: 'Role', zh: '用途' }), T({ en: 'Download', zh: '体积' }), T({ en: 'Recommended RAM', zh: '建议内存' })]}
        rows={[
          [<InlineCode key="a">qwen3-embedding:0.6b</InlineCode>, 'Embedding · 1024 dim', '~639 MB', '4 GB'],
          [<InlineCode key="b">qwen3-embedding:4b</InlineCode>, 'Embedding · 1024 dim', '~2.5 GB', '8 GB'],
          [<InlineCode key="c">qwen3.5:9b</InlineCode>, 'VLM', '~6.6 GB', '16 GB'],
          [<InlineCode key="d">qwen3.6:27b</InlineCode>, 'VLM', '~17 GB', '32 GB'],
          [<InlineCode key="e">qwen3.6:35b</InlineCode>, 'VLM', '~24 GB', '48 GB'],
        ]}
      />

      <P>{T({
        en: 'If you change the embedding model, set its real output size as the dimension in the configuration step, because the index is created with that size.',
        zh: '如果更换 Embedding 模型，要在配置那一步把它真实的输出维度填进去，索引会按这个维度创建。',
      })}</P>

      <H2 id="install">{T({ en: 'Step 2: Install OpenViking and cuVS', zh: '第 2 步：安装 OpenViking 与 cuVS' })}</H2>

      <P>{T({
        en: 'Use one virtual environment for both, so the server process can import cuVS and CuPy.',
        zh: '把两者装进同一个虚拟环境，这样服务进程才能导入 cuVS 和 CuPy。',
      })}</P>

      <Pre lang="bash" filename="terminal">{`python3 -m venv ~/openviking-env
source ~/openviking-env/bin/activate
pip install openviking

# CUDA 13 (use cuvs-cu12 and cupy-cuda12x for CUDA 12)
pip install cuvs-cu13 'cupy-cuda13x[ctk]' --extra-index-url=https://pypi.nvidia.com`}</Pre>

      <P>{T({
        en: <>The <InlineCode>[ctk]</InlineCode> extra installs the CUDA toolkit headers that cuVS needs when the host has a driver but no toolkit. See the <A href={CUVS_INSTALL}>cuVS installation requirements</A> for the release you pick.</>,
        zh: <><InlineCode>[ctk]</InlineCode> 会安装 cuVS 需要的 CUDA toolkit 头文件，适用于主机只有驱动、没有 toolkit 的情况。具体要求见所选版本的 <A href={CUVS_INSTALL}>cuVS 安装文档</A>。</>,
      })}</P>

      <H3 id="smoke">{T({ en: 'Check the GPU Path Before Adding Models', zh: '先单独验证 GPU 链路' })}</H3>

      <P>{T({
        en: 'The repository ships a smoke test that writes vectors and runs a filtered search through cuVS. It needs neither the embedding model nor the VLM, so a failure here points at the GPU stack and nothing else.',
        zh: '仓库自带一个冒烟测试，会通过 cuVS 写入向量并做带过滤的检索。它不依赖 Embedding 模型和 VLM，所以在这里失败，问题就在 GPU 软件栈上。',
      })}</P>

      <Pre lang="bash" filename="terminal">{SMOKE}</Pre>

      <H2 id="configure">{T({ en: 'Step 3: Configure OpenViking', zh: '第 3 步：配置 OpenViking' })}</H2>

      <P>{T({
        en: <>Run the setup wizard and choose the local Ollama option for both models. It checks that Ollama is reachable, offers to pull the models, and writes <InlineCode>~/.openviking/ov.conf</InlineCode>.</>,
        zh: <>运行安装向导，Embedding 和 VLM 都选本地 Ollama。它会检查 Ollama 是否可达，可以帮你拉取模型，并写出 <InlineCode>~/.openviking/ov.conf</InlineCode>。</>,
      })}</P>

      <Pre lang="bash" filename="terminal">{`openviking-server init`}</Pre>

      <P>{T({
        en: <>The wizard does not configure the GPU backend, so open <InlineCode>ov.conf</InlineCode> and add the <InlineCode>storage.vectordb</InlineCode> block. The complete file looks like this:</>,
        zh: <>向导不会配置 GPU 后端，所以要打开 <InlineCode>ov.conf</InlineCode>，补上 <InlineCode>storage.vectordb</InlineCode> 这一段。完整文件如下：</>,
      })}</P>

      <Pre lang="json" filename="~/.openviking/ov.conf">{OV_CONF}</Pre>

      <Ul>
        <Li><Strong>{T({ en: 'num_ctx and think', zh: 'num_ctx 与 think' })}</Strong>{T({
          en: ': Ollama defaults to a 4096-token context, and the memory-extraction prompt alone is about 5k tokens, so the default silently truncates the conversation. The wizard sets 16384 and turns thinking off, because a thinking model can otherwise emit only reasoning and stall.',
          zh: '：Ollama 默认上下文只有 4096 token，而记忆提取的提示词本身就约 5k token，默认值会悄悄截断对话。向导把它设为 16384，并关闭 thinking，否则 thinking 模型可能只输出推理过程、一直不给结果。',
        })}</Li>
        <Li><Strong>{T({ en: 'fallback_to_native', zh: 'fallback_to_native' })}</Strong>{T({
          en: ': dense search runs on the GPU, while sparse and hybrid queries fall back to the native index.',
          zh: '：稠密检索走 GPU；稀疏和混合检索回退到原生索引。',
        })}</Li>
        <Li><Strong>{T({ en: 'server.host', zh: 'server.host' })}</Strong>{T({
          en: <>: <InlineCode>127.0.0.1</InlineCode> is the default. In the default development mode, local requests need no key. The section on remote access below covers opening it up safely.</>,
          zh: <>：默认值就是 <InlineCode>127.0.0.1</InlineCode>。默认的开发模式下，本机请求不需要 Key。如何安全地对外开放，见下文的远程访问一节。</>,
        })}</Li>
      </Ul>

      <H3 id="backend-mode">{T({ en: 'Choose the Backend Mode', zh: '选择后端模式' })}</H3>

      <ModeCards T={T} />

      <P>{T({
        en: 'On a Spark the VLM and the vector index draw on the same unified memory. If you want OpenViking to use cuVS only when memory allows, keep the backend on local and enable auto mode instead:',
        zh: '在 Spark 上，VLM 和向量索引共用同一块统一内存。如果希望 OpenViking 只在内存允许时才用 cuVS，把 backend 保持为 local，改用 auto 模式：',
      })}</P>

      <Pre lang="json" filename="ov.conf (storage section)">{AUTO_CONF}</Pre>

      <RouteFigure T={T} />

      <P>{T({
        en: 'Before each build, auto mode reads the free device memory, estimates the index size, multiplies it by the safety factor, and keeps the reserve free. If the index does not fit, that query uses the native CPU index and a later query retries. Small filtered scopes also route to the CPU by design, because a few thousand candidates are faster there. The reserve above is an example; size it against the model you run.',
        zh: '每次构建前，auto 模式会读取设备空闲内存，估算索引大小并乘以安全系数，同时保留一部分余量。放不下时，这次查询使用原生 CPU 索引，之后的查询会重试。较小的过滤范围也会按设计走 CPU，因为几千个候选在 CPU 上更快。上面的预留值只是示例，请按你运行的模型来定。',
      })}</P>

      <H2 id="start">{T({ en: 'Step 4: Check and Start the Server', zh: '第 4 步：检查并启动服务' })}</H2>

      <Pre lang="bash" filename="terminal">{`openviking-server doctor     # validates embedding, VLM, storage, and the vector backend
openviking-server            # keep this terminal open

# in another terminal
curl http://127.0.0.1:1933/health`}</Pre>

      <P>{T({
        en: <>A healthy server answers <InlineCode>{'{"status":"ok","healthy":true,...}'}</InlineCode>. Only one server can own a workspace: with an embedded vector backend, OpenViking takes an exclusive file lock on <InlineCode>storage.workspace</InlineCode>. Web Studio, the built-in management UI, is served from the same address at <InlineCode>/studio</InlineCode>.</>,
        zh: <>服务正常时会返回 <InlineCode>{'{"status":"ok","healthy":true,...}'}</InlineCode>。一个 workspace 只能由一个服务占用：使用内置向量后端时，OpenViking 会对 <InlineCode>storage.workspace</InlineCode> 加独占文件锁。内置的管理界面 Web Studio 就在同一个地址的 <InlineCode>/studio</InlineCode> 下。</>,
      })}</P>

      <H2 id="cli">{T({ en: 'Step 5: Connect the CLI', zh: '第 5 步：连接命令行' })}</H2>

      <Pre lang="bash" filename="terminal">{`npm install -g @openviking/cli
ov config      # choose Custom, URL http://127.0.0.1:1933, leave the key empty
ov health`}</Pre>

      <H2 id="verify">{T({ en: 'Step 6: Verify the Full Path', zh: '第 6 步：验证完整链路' })}</H2>

      <P>{T({
        en: 'A passing health check does not prove that models, indexing, and retrieval work together. Run one document through the whole path. Save this as ov-release-rotation.md:',
        zh: '健康检查通过，并不能证明模型、索引和检索配合正常。拿一份文档走完整条链路。先把下面的内容保存为 ov-release-rotation.md：',
      })}</P>

      <Pre lang="markdown" filename="ov-release-rotation.md" lineNumbers={false}>{SAMPLE_DOC}</Pre>

      <Pre lang="bash" filename="terminal">{`ov add-resource ./ov-release-rotation.md \\
  --to viking://resources/ov-release-rotation --wait --timeout 120

ov tree viking://resources/ov-release-rotation
ov overview viking://resources/ov-release-rotation
ov find "Who owns the weekly OpenViking release?" \\
  --uri viking://resources/ov-release-rotation`}</Pre>

      <P>{T({
        en: 'Then read the file back by the URI that find returns, and compare it with the source. The comparison is the acceptance test, because a successful HTTP status alone does not show that the stored text is intact:',
        zh: '然后用 find 返回的 URI 把文件读回来，和源文件比对。以比对结果为准，因为 HTTP 状态成功并不能说明存下来的正文完整：',
      })}</P>

      <Pre lang="bash" filename="terminal">{`ov read "<returned-file-uri>" > /tmp/readback.md
diff /tmp/readback.md ov-release-rotation.md && echo READBACK_OK`}</Pre>

      <H3 id="confirm-gpu">{T({ en: 'Confirm the Search Used the GPU', zh: '确认检索走了 GPU' })}</H3>

      <P>{T({
        en: <>Ask for operation telemetry on a search. In the response, <InlineCode>summary.vector.cuvs.routes</InlineCode> counts the searches by route, and a <InlineCode>cuvs</InlineCode> entry means the dense search ran on the GPU. <InlineCode>builds</InlineCode> is 1 on the first search, when the GPU index is built lazily, and 0 afterward.</>,
        zh: <>在检索请求里要求返回 operation telemetry。响应中的 <InlineCode>summary.vector.cuvs.routes</InlineCode> 按路由统计检索次数，出现 <InlineCode>cuvs</InlineCode> 表示稠密检索在 GPU 上执行。第一次检索会惰性构建 GPU 索引，<InlineCode>builds</InlineCode> 为 1，之后为 0。</>,
      })}</P>

      <Pre lang="bash" filename="terminal">{`curl -s -X POST http://127.0.0.1:1933/api/v1/search/find \\
  -H "Content-Type: application/json" \\
  -d '{"query": "weekly release owner", "target_uri": "viking://resources", "telemetry": true}'`}</Pre>

      <P>{T({
        en: 'To watch the process from the GPU side, run the per-process query from Step 0 before and after the first search.',
        zh: '想从 GPU 一侧观察，可以在第一次检索前后各执行一次第 0 步里的进程级查询。',
      })}</P>

      <H2 id="service">{T({ en: 'Run It as a Service', zh: '做成常驻服务' })}</H2>

      <P>{T({
        en: <>For a machine that stays on, let systemd keep the server up and start it on boot. This is the recommended way to run OpenViking on Linux, and it works with the virtual environment from Step 2. Replace every <InlineCode>{'<you>'}</InlineCode> with the user that owns the environment and the configuration. Ollama's installer registers its own systemd unit, so the server can start after it.</>,
        zh: <>机器要长期开着的话，让 systemd 负责保活并在开机时启动。这是 OpenViking 在 Linux 上推荐的运行方式，和第 2 步的虚拟环境可以直接配合。把所有的 <InlineCode>{'<you>'}</InlineCode> 换成拥有该环境和配置文件的用户。Ollama 的安装脚本会注册自己的 systemd 服务，所以 OpenViking 可以排在它之后启动。</>,
      })}</P>

      <Pre lang="ini" filename="/etc/systemd/system/openviking.service">{SYSTEMD_UNIT}</Pre>

      <Pre lang="bash" filename="terminal">{`sudo systemctl daemon-reload
sudo systemctl enable --now openviking.service
sudo systemctl status openviking.service
sudo journalctl -u openviking.service -f`}</Pre>

      <P>{T({
        en: <>Stop the foreground server from Step 4 first, because only one server can hold the workspace lock. The unit points at the configuration file through <InlineCode>OPENVIKING_CONFIG_FILE</InlineCode>, so a file under your home directory is used even though systemd starts the service.</>,
        zh: <>先停掉第 4 步里前台运行的服务，因为 workspace 的锁只能由一个服务持有。这个服务单元通过 <InlineCode>OPENVIKING_CONFIG_FILE</InlineCode> 指定配置文件，所以放在你家目录下的文件也会被用到。</>,
      })}</P>

      <H2 id="remote">{T({ en: 'Reach It from Your Laptop', zh: '从笔记本访问' })}</H2>

      <P>{T({
        en: 'The server listens on loopback, so a laptop cannot reach it directly. There are two ways to fix that, and the first is usually enough.',
        zh: '服务只监听本机回环地址，所以笔记本不能直接连上。有两种办法，第一种通常就够了。',
      })}</P>

      <H3 id="tunnel">{T({ en: 'Option 1: An SSH tunnel', zh: '办法一：SSH 隧道' })}</H3>

      <Pre lang="bash" filename="laptop">{`ssh -N -L 1933:127.0.0.1:1933 <you>@<spark-host>

# in another laptop terminal
ov config      # Custom, URL http://127.0.0.1:1933
ov health
# Web Studio: http://127.0.0.1:1933/studio`}</Pre>

      <P>{T({
        en: 'Traffic stays inside the SSH connection, the server never listens on a public address, and no key is needed in the default development mode.',
        zh: '流量全部走在 SSH 连接里，服务不会监听任何对外地址，默认的开发模式下也不需要 Key。',
      })}</P>

      <H3 id="open-up">{T({ en: 'Option 2: Listen on the network with authentication', zh: '办法二：对网络开放并开启认证' })}</H3>

      <P>{T({
        en: <>Use this when several people or machines share the Spark. A non-loopback listener requires a root key, and the server refuses to start in development mode on such an address. Add the listener and the key to <InlineCode>ov.conf</InlineCode>, then create an account and a user key with the Admin API:</>,
        zh: <>几个人或几台机器共用这台 Spark 时用这种方式。非回环地址的监听必须配置 root key，开发模式下服务会拒绝在这种地址上启动。在 <InlineCode>ov.conf</InlineCode> 里加上监听地址和 root key，然后用 Admin API 创建账户和用户 Key：</>,
      })}</P>

      <Pre lang="json" filename="ov.conf (server section)">{`"server": {
  "host": "0.0.0.0",
  "port": 1933,
  "root_api_key": "<a long random secret>"
}`}</Pre>

      <Pre lang="bash" filename="terminal">{`curl -X POST http://127.0.0.1:1933/api/v1/admin/accounts \\
  -H "X-API-Key: <root key>" -H "Content-Type: application/json" \\
  -d '{"account_id": "team", "admin_user_id": "alice"}'
# returns a user_key; give that key to the client, never the root key`}</Pre>

      <P>{T({
        en: <>Clients then put the Spark's address and their user key in <InlineCode>~/.openviking/ovcli.conf</InlineCode>. A root key is for administration only. Put TLS in front of the server before you expose it beyond a trusted network; the <A href={DOC_PUBLIC}>public access guide</A> and <A href={DOC_AUTH}>authentication</A> cover the options.</>,
        zh: <>之后客户端把 Spark 的地址和各自的用户 Key 写进 <InlineCode>~/.openviking/ovcli.conf</InlineCode>。root key 只用于管理。要在受信任网络之外开放，先在服务前面加上 TLS；<A href={DOC_PUBLIC_ZH}>公网访问指南</A>和<A href={DOC_AUTH_ZH}>认证文档</A>介绍了具体做法。</>,
      })}</P>

      <H2 id="agents">{T({ en: 'Connect Your Agents', zh: '接入你的 Agent' })}</H2>

      <P>{T({
        en: 'A running server is useful once an agent talks to it. Three routes cover most setups:',
        zh: '服务跑起来之后，要让 Agent 连上才有用。三种方式覆盖大多数场景：',
      })}</P>

      <Ul>
        <Li><Strong>{T({ en: 'Plugins for Claude Code and Codex', zh: 'Claude Code 与 Codex 插件' })}</Strong>{T({
          en: <>: run <InlineCode>curl -fsSL https://openviking.ai/install | bash</InlineCode>, tick the tools to set up, and choose Custom URL with your server address (the tunnel address works). Afterward you use claude or codex as before. The <A href={CODING_AGENT_POST}>coding agent guide</A> walks through it.</>,
          zh: <>：运行 <InlineCode>curl -fsSL https://openviking.ai/install | bash</InlineCode>，勾选要配置的工具，服务器选 Custom URL 并填入你的服务地址（隧道地址也可以）。之后照常使用 claude 或 codex。<A href={CODING_AGENT_POST}>Coding Agent 接入指南</A>有完整步骤。</>,
        })}</Li>
        <Li><Strong>{T({ en: 'MCP clients', zh: 'MCP 客户端' })}</Strong>{T({
          en: ': point any MCP-compatible client at the built-in /mcp endpoint of the server.',
          zh: '：把任何支持 MCP 的客户端指向服务内置的 /mcp 端点。',
        })}</Li>
        <Li><Strong>{T({ en: 'Context Gateway', zh: 'Context Gateway' })}</Strong>{T({
          en: <>: for clients that cannot install a plugin, such as chat apps and SDK scripts, change only the base URL and the API key. The <A href={DOC_GATEWAY}>gateway guide</A> is in beta and explains what it recalls, saves, and compacts.</>,
          zh: <>：对装不了插件的客户端，比如聊天应用和 SDK 脚本，只改 base URL 和 API Key 就行。<A href={DOC_GATEWAY_ZH}>Gateway 文档</A>目前是 beta，说明了它会召回、保存和压缩什么。</>,
        })}</Li>
      </Ul>

      <P>{T({
        en: <>The <A href={DOC_INTEGRATIONS}>integration overview</A> lists every supported agent with a one-line recommendation.</>,
        zh: <><A href={DOC_INTEGRATIONS_ZH}>集成总览</A>用一行建议列出了所有支持的 Agent。</>,
      })}</P>

      <H2 id="memory">{T({ en: 'Plan the Memory', zh: '规划内存' })}</H2>

      <P>{T({
        en: <>The vectors live in two places: a host-side copy that OpenViking keeps for recovery and fallback, and the cuVS dataset on the device. Both are logical allocations from the same unified memory pool. With the default <InlineCode>float32</InlineCode>, the device payload is <InlineCode>N × dimension × 4</InlineCode> bytes. We measured these CuPy allocations for 1024-dimensional vectors on the Spark:</>,
        zh: <>向量存在两处：OpenViking 在主机侧保留一份，用于恢复和回退；另一份是设备上的 cuVS 数据集。两者都是从同一块统一内存里分配的逻辑内存。默认 <InlineCode>float32</InlineCode> 时，设备侧的数据量是 <InlineCode>N × 维度 × 4</InlineCode> 字节。我们在 Spark 上对 1024 维向量实测了 CuPy 的分配量：</>,
      })}</P>

      <MemoryFigure T={T} />

      <P>{T({
        en: <>The server process also carries a CUDA runtime baseline of roughly 170 MiB, and the models loaded by Ollama take their own share of the same pool. Setting <InlineCode>"dtype": "float16"</InlineCode> halves the device payload; measure Recall@K against float32 before you rely on it. The <A href={DOC_CUVS}>cuVS guide</A> lists the CAGRA graph overhead and the filter-cache cost.</>,
        zh: <>服务进程本身还有约 170 MiB 的 CUDA 运行时基线开销，Ollama 加载的模型也占用同一块内存。设置 <InlineCode>"dtype": "float16"</InlineCode> 可以把设备侧数据量减半，使用前请对照 float32 测一下 Recall@K。CAGRA 的图结构开销和过滤缓存开销见 <A href={DOC_CUVS_ZH}>cuVS 指南</A>。</>,
      })}</P>

      <H3 id="behavior">{T({ en: 'Behavior to Expect', zh: '运行行为' })}</H3>

      <Ul>
        <Li>{T({ en: 'The GPU index is not persisted. After a restart, OpenViking rebuilds it from the locally stored vectors, and the first dense search pays that cost.', zh: 'GPU 索引不做持久化。重启后，OpenViking 会用本地存储的向量重建它，第一次稠密检索要承担这部分耗时。' })}</Li>
        <Li>{T({ en: 'Inserts, updates, and deletes mark the GPU index dirty. By default the next search rebuilds it synchronously; with auto mode and background rebuild, queries use the CPU index until the new snapshot is ready.', zh: '写入、更新和删除会把 GPU 索引标记为脏。默认情况下，下一次检索会同步重建；auto 模式加后台重建时，新快照就绪前的查询使用 CPU 索引。' })}</Li>
        <Li>{T({ en: 'A built snapshot stays in memory until the next rebuild or shutdown. There is no idle eviction yet, so budget for the full index.', zh: '构建好的快照会一直留在内存里，直到下次重建或服务关闭。目前没有空闲淘汰，请按完整索引规划预算。' })}</Li>
        <Li>{T({ en: 'The CPU path searches an int8-quantized index and the GPU path searches float32 or float16 vectors, so scores and near-tie ordering can differ slightly between the two.', zh: 'CPU 路径搜索的是 int8 量化索引，GPU 路径搜索 float32 或 float16 向量，两条路径的分数和近似并列结果的顺序可能略有差异。' })}</Li>
      </Ul>

      <H2 id="speedup">{T({ en: 'What Speed-up to Expect', zh: '能期待多大加速' })}</H2>

      <P>{T({
        en: 'We measured exact brute-force search on a 1.94 million vector, 1024-dimensional collection on the Spark, with top-100 results and 8 concurrent clients:',
        zh: '我们在 Spark 上测了精确暴力检索：194 万条、1024 维的数据集，返回 top-100，8 个并发客户端：',
      })}</P>

      <Table
        headers={[T({ en: 'Scope', zh: '范围' }), T({ en: 'CPU native', zh: 'CPU 原生' }), T({ en: 'cuVS GPU with Auto', zh: 'cuVS GPU（Auto）' }), T({ en: 'Speed-up', zh: '加速比' })]}
        rows={[
          [T({ en: 'No directory filter', zh: '无目录过滤' }), '19.0 QPS', '76.8 QPS', '4.04×'],
          [T({ en: 'Directory filter', zh: '路径过滤' }), '71.1 QPS', '115.0 QPS', '1.62×'],
        ]}
      />

      <P>{T({
        en: 'Read these numbers with their limits in mind. They cover the vector recall step only, not the end-to-end latency of an agent. The CPU path searched an int8 index and the GPU path float16, so this is not an equal-dtype kernel comparison. A directory filter leaves fewer candidates, which is why the gain is smaller there and why auto mode keeps a CPU path. The benchmark harness lets you repeat the measurement on your own data.',
        zh: '读这些数字时请记住它们的边界：只覆盖向量召回这一步，不是 Agent 的端到端延迟；CPU 路径搜索的是 int8 索引，GPU 路径是 float16，不是同 dtype 的 kernel 对比。目录过滤之后候选变少，所以那一行提升较小，这也是 auto 模式保留 CPU 路径的原因。你可以用 benchmark 工具在自己的数据上重测。',
      })}</P>

      <H3 id="tuning">{T({ en: 'Tune for Your Workload', zh: '按负载调优' })}</H3>

      <Table
        headers={[T({ en: 'Goal', zh: '目标' }), T({ en: 'Setting', zh: '设置' })]}
        rows={[
          [T({ en: 'More throughput under concurrent requests', zh: '并发请求下提高吞吐' }), <InlineCode key="a">micro_batching_enabled: true</InlineCode>],
          [T({ en: 'Smaller device footprint', zh: '减少设备侧占用' }), <InlineCode key="b">dtype: float16</InlineCode>],
          [T({ en: 'Approximate graph search on large collections', zh: '大规模数据上的近似图检索' }), <InlineCode key="c">algorithm: cagra</InlineCode>],
          [T({ en: 'Measure your own configuration', zh: '测量你自己的配置' }), <A key="d" href={BENCH_README}>benchmark/cuvs</A>],
        ]}
      />

      <P>{T({
        en: 'Micro-batching supports exact brute-force only and needs max_concurrent_gpu_searches set to 1. Change one setting at a time and re-run the benchmark harness on your data, because crossover points depend on the hardware and the workload.',
        zh: 'Micro-batching 只支持 exact brute-force，并要求 max_concurrent_gpu_searches 为 1。每次只改一个设置，并用 benchmark 工具在你自己的数据上重测，因为交叉点取决于硬件和负载。',
      })}</P>

      <H2 id="troubleshooting">{T({ en: 'Troubleshooting', zh: '排障线索' })}</H2>

      <Table
        headers={[T({ en: 'Symptom', zh: '现象' }), T({ en: 'Where to look', zh: '先看哪里' })]}
        rows={[
          [T({ en: 'Import error for cuvs or cupy, or a CUDA version error', zh: '导入 cuvs 或 cupy 报错，或提示 CUDA 版本不匹配' }), T({ en: 'The package does not match the CUDA major version. Reinstall cu13 or cu12 packages, keep the [ctk] extra, and re-run the smoke test.', zh: '安装包和 CUDA 主版本不匹配。重装对应的 cu13 或 cu12 包，保留 [ctk]，再跑冒烟测试。' })],
          [T({ en: 'doctor reports an embedding or VLM failure', zh: 'doctor 报告 Embedding 或 VLM 失败' }), T({ en: 'curl http://127.0.0.1:11434/api/tags and confirm the model names match ov.conf exactly.', zh: '执行 curl http://127.0.0.1:11434/api/tags，确认模型名和 ov.conf 里的完全一致。' })],
          [T({ en: 'Overviews are empty or show a placeholder', zh: '概览为空或只有占位文字' }), T({ en: 'The VLM is not available, so semantic processing did not run. Fix the model configuration before judging retrieval quality.', zh: 'VLM 不可用，语义处理没有执行。先修好模型配置，再评价检索效果。' })],
          [T({ en: 'Memory extraction stalls or looks truncated', zh: '记忆提取卡住或内容被截断' }), T({ en: 'Keep num_ctx at 16384 and think set to false in extra_request_body.', zh: '确认 extra_request_body 里 num_ctx 为 16384、think 为 false。' })],
          [T({ en: 'A second server will not start', zh: '第二个服务起不来' }), T({ en: 'The workspace holds an exclusive lock. Stop the first server (including the systemd unit) or use a separate workspace.', zh: 'workspace 持有独占锁。停掉第一个服务（包括 systemd 服务），或者换一个独立的 workspace。' })],
          [T({ en: 'The server refuses to start on 0.0.0.0', zh: '服务拒绝在 0.0.0.0 上启动' }), T({ en: 'Development mode does not allow a non-loopback address. Set server.root_api_key.', zh: '开发模式不允许非回环地址。设置 server.root_api_key。' })],
          [T({ en: 'Search is not faster than the CPU', zh: '检索没有比 CPU 快' }), T({ en: 'Check summary.vector.cuvs.routes. In auto mode, small filtered scopes route to the CPU on purpose.', zh: '看 summary.vector.cuvs.routes。auto 模式下，较小的过滤范围会按设计走 CPU。' })],
          [T({ en: 'First search after a restart or write is slow', zh: '重启或写入后的第一次检索很慢' }), T({ en: 'The GPU index is being rebuilt. Use auto mode with background rebuild if that latency matters.', zh: 'GPU 索引正在重建。对这段延迟敏感的话，使用 auto 模式并开启后台重建。' })],
        ]}
      />

      <H2 id="upgrade">{T({ en: 'Upgrade and Roll Back', zh: '升级与回滚' })}</H2>

      <Ol>
        <Li>{T({ en: 'Copy the virtual environment, and install the new OpenViking version into the copy. Run pip check there.', zh: '复制虚拟环境，在副本里安装新版本的 OpenViking，并执行 pip check。' })}</Li>
        <Li>{T({ en: 'Stop the server (sudo systemctl stop openviking.service) and back up the whole workspace directory together with ov.conf.', zh: '停掉服务（sudo systemctl stop openviking.service），把整个 workspace 目录连同 ov.conf 一起备份。' })}</Li>
        <Li>{T({ en: 'Point ExecStart at the new environment, start the service, and run the Step 6 checks again.', zh: '把 ExecStart 改指向新环境，启动服务，重新执行第 6 步的验证。' })}</Li>
      </Ol>

      <P>{T({
        en: 'To roll back, stop the new server, restore the backed-up workspace, and start the old environment. Do not point the old version at data the new version has written.',
        zh: '回滚时，先停掉新服务，恢复备份的 workspace，再启动旧环境。不要让旧版本直接接管新版本写过的数据。',
      })}</P>

      <Hr ornament />

      <H2 id="next">{T({ en: 'What to Try Next', zh: '接下来' })}</H2>

      <P>{T({
        en: 'With the server running and an agent connected, import your own documents and see what the agent recalls. Everything in this guide stays on the machine, so the same setup also works for private repositories and documents.',
        zh: '服务跑起来、Agent 接上之后，导入你自己的文档，看看 Agent 能召回什么。本文所有组件都留在本机，所以同样的部署也适合私有代码仓库和内部文档。',
      })}</P>

      <H2 id="links" toc={false}>{T({ en: 'Links', zh: '相关传送门' })}</H2>

      <Ul>
        <Li><A href={OPENVIKING_GITHUB}>OpenViking GitHub</A></Li>
        <Li><A href={T({ en: DOC_QUICKSTART, zh: DOC_QUICKSTART_ZH })}>{T({ en: 'Quickstart', zh: '快速开始' })}</A></Li>
        <Li><A href={T({ en: DOC_DEPLOY, zh: DOC_DEPLOY_ZH })}>{T({ en: 'Server deployment', zh: '服务器部署' })}</A></Li>
        <Li><A href={T({ en: DOC_CUVS, zh: DOC_CUVS_ZH })}>{T({ en: 'NVIDIA cuVS backend guide', zh: 'NVIDIA cuVS 后端指南' })}</A></Li>
        <Li><A href={T({ en: DOC_CONFIG, zh: DOC_CONFIG_ZH })}>{T({ en: 'Model configuration', zh: '模型配置' })}</A></Li>
        <Li><A href={T({ en: DOC_INTEGRATIONS, zh: DOC_INTEGRATIONS_ZH })}>{T({ en: 'Agent integrations', zh: 'Agent 集成' })}</A></Li>
        <Li><A href={BENCH_README}>{T({ en: 'cuVS benchmark harness', zh: 'cuVS 基准测试工具' })}</A></Li>
        <Li><A href={DOCS}>OpenViking Docs</A></Li>
      </Ul>
    </Article>
  );
};

export default {
  id: 'deploy-openviking-on-dgx-spark',
  Component: DeployOnDgxSpark,
  meta: {
    title: {
      zh: '在 DGX Spark 上部署全本地 OpenViking：本地模型加 cuVS GPU 检索',
      en: 'Deploy a Fully Local OpenViking on NVIDIA DGX Spark with cuVS',
    },
    description: {
      zh: '从一台干净的 DGX Spark 开始：安装 Ollama 本地模型、OpenViking 与 NVIDIA cuVS，配置 GPU 向量检索，做成常驻服务，并验证从导入到读取的完整链路。',
      en: 'Start from a clean DGX Spark: install Ollama local models, OpenViking, and NVIDIA cuVS, configure GPU vector search, run it as a service, and verify the path from import to read-back.',
    },
    cover: '/assets/covers/deploy-openviking-on-dgx-spark.png',
    publishedAt: '2026-10-08',
    updatedAt: '2026-10-08',
    readingTime: { zh: 14, en: 17 },
    category: { zh: '工程', en: 'Engineering' },
    tags: ['openviking', 'nvidia', 'cuvs', 'dgx-spark', 'deployment', 'ollama'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [{ name: 'zayn', github: 'ZaynJarvis' }],
  },
};
