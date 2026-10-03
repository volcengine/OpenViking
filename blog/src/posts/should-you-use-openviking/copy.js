/* Reader-facing copy for editions, notes and result bullets, keyed so the
 * quiz state never stores localized strings. */

import { SLUG } from './quiz-data.js';

export const UTM = `utm_source=blog&utm_medium=article&utm_campaign=${SLUG}`;

const docs = (path) => ({
  zh: `https://docs.openviking.ai/zh/${path}`,
  en: `https://docs.openviking.ai/en/${path}`,
});

export const LINKS = {
  github: `https://github.com/volcengine/OpenViking?${UTM}`,
  commercial: `https://github.com/volcengine/OpenViking?${UTM}#commercial-editions`,
  quickstart: docs('getting-started/02-quickstart'),
  agentSetup: docs('getting-started/04-setup-for-agent'),
  integrations: docs('agent-integrations/01-overview'),
  deployment: docs('guides/03-deployment'),
  faq: docs('faq/faq'),
  hostedProduct: `https://www.volcengine.com/product/openviking-service?${UTM}`,
  hostedQuickstart: 'https://docs.volcengine.com/docs/84313/2374479',
  hostedPersonal: 'https://docs.volcengine.com/docs/84313/2693723',
  hostedOverview: 'https://docs.volcengine.com/docs/84313/2374478',
  billing: 'https://docs.volcengine.com/docs/84313/2485124',
  privateForm: {
    zh: 'https://my.feishu.cn/share/base/form/shrcnMFqymCd9sq77sLk34Krxoc',
    en: 'https://docs.google.com/forms/d/e/1FAIpQLScQqwsm7fvKdjtNiW5rWNXJjoHPtedVzLsKSMJgObtsj2_udA/viewform',
  },
  discord: 'https://discord.com/invite/eHvx8E9XF3',
  benchmark: 'https://blog.openviking.ai/post/openviking-benchmark-results/',
};

const HOSTED_MODELS = {
  zh: '使用豆包系列模型，不用自己部署；绑定一个方舟凭证即可（Agent Plan、Coding Plan 或自有推理接入点）。',
  en: 'Runs on the Doubao model family, nothing to deploy; just link one Ark credential (Agent Plan, Coding Plan or your own inference endpoint).',
};
const HOSTED_DATA = {
  zh: '火山引擎华北2（北京），海外用户也可以使用；海外区域的托管服务（BytePlus）即将上线。',
  en: "Volcengine's Beijing region (cn-beijing), also available to users outside mainland China; hosting in overseas regions on BytePlus is coming soon.",
};
const HOSTED_NEEDS = {
  zh: '完成实名认证的火山引擎账号，并绑定一个方舟凭证（Agent Plan、Coding Plan 或自有推理接入点）。',
  en: 'A real-name-verified Volcengine account and one Ark credential (Agent Plan, Coding Plan or your own inference endpoint).',
};

/* Editions. `cta[0]` is the primary action on a result. `inline` is the name
 * used mid-sentence (the one-pager). */
export const FORMS = {
  oss: {
    name: { zh: '开源版', en: 'Open source' },
    inline: { zh: 'OpenViking 开源版', en: 'the open-source edition of OpenViking' },
    sub: { zh: '自部署', en: 'Self-hosted' },
    tagline: { zh: '免费、功能完整，自己部署，模型自己挑。', en: 'Free and full-featured. You run it and pick the models.' },
    who: { zh: '愿意自己部署、想完全掌控的个人和团队。', en: 'Individuals and teams who want full control and are happy to run it.' },
    ops: { zh: '自己部署和升级；单节点运行，或自己搭多副本。', en: 'You deploy and upgrade it; a single node, or replicas you assemble yourself.' },
    models: { zh: '自己配：embedding 默认用内置本地模型，不需要 key；摘要和记忆提取需要一个 VLM，火山方舟、OpenAI 兼容接口、Ollama、litellm 都行。', en: 'Bring your own: embedding defaults to a built-in local model with no key; summaries and memory extraction need a VLM (Volcengine Ark, OpenAI-compatible APIs, Ollama, litellm).' },
    data: { zh: '你部署在哪里，数据就在哪里。', en: 'Wherever you deploy it.' },
    cost: { zh: '免费（AGPLv3）。要花的是服务器、模型调用和维护时间。', en: 'Free (AGPLv3). The real costs are servers, model usage and maintenance time.' },
    needs: { zh: '一台能跑 Python 3.10+ 或 Docker 的机器，外加一个 VLM。', en: 'A machine that runs Python 3.10+ or Docker, plus a VLM.' },
    cta: [
      { id: 'quickstart', label: { zh: '快速开始', en: 'Quickstart' }, href: LINKS.quickstart },
      { id: 'agent-setup', label: { zh: '让 agent 帮你装', en: 'Let your agent install it' }, href: LINKS.agentSetup },
      { id: 'github', label: { zh: 'GitHub', en: 'GitHub' }, href: LINKS.github },
    ],
  },
  personal: {
    name: { zh: '托管版 · 个人版', en: 'Managed · Personal' },
    inline: { zh: 'OpenViking 托管版 · 个人版', en: 'OpenViking Managed (Personal plan)' },
    sub: { zh: '火山引擎 OpenViking Context', en: 'OpenViking Context on Volcengine' },
    tagline: { zh: '一个人用、不想运维：开通以后直接接上你的 agent。', en: 'One user, no ops: sign up and connect your agent.' },
    who: { zh: '个人开发者、个人助理、单个 agent。', en: 'Individual developers, personal assistants, single agents.' },
    ops: { zh: '不用部署和升级，也不用自己部署模型。', en: 'No deployment, upgrades or model hosting on your side.' },
    models: HOSTED_MODELS,
    data: HOSTED_DATA,
    cost: { zh: '每个个人版库前 50 个文件免费，之后按小时按量计费，起步价很低；可以用 Agent Plan 额度抵扣。', en: 'The first 50 files in each Personal library are free, then hourly pay-as-you-go from a low starting price; Agent Plan credits apply.' },
    needs: HOSTED_NEEDS,
    cta: [
      { id: 'start', label: { zh: '开通个人版（前 50 个文件免费）', en: 'Start Personal (first 50 files free)' }, href: LINKS.hostedQuickstart },
      { id: 'walkthrough', label: { zh: '个人记忆接入教程', en: 'Personal memory walkthrough' }, href: LINKS.hostedPersonal },
      { id: 'pricing', label: { zh: '计费说明', en: 'Pricing' }, href: LINKS.billing },
    ],
  },
  enterprise: {
    name: { zh: '托管版 · 企业版', en: 'Managed · Enterprise' },
    inline: { zh: 'OpenViking 托管版 · 企业版', en: 'OpenViking Managed (Enterprise plan)' },
    sub: { zh: '火山引擎 OpenViking Context', en: 'OpenViking Context on Volcengine' },
    tagline: { zh: '多人、多 agent、要隔离，又不想养一支运维团队。', en: 'Many people and agents, strict isolation, no ops team to spare.' },
    who: { zh: '团队、企业应用、多 agent 协作和大量终端用户。', en: 'Teams, enterprise apps, multi-agent systems and many end users.' },
    ops: { zh: '不用部署和升级；独享资源，按文件量自动扩容。', en: 'Nothing to deploy or upgrade; dedicated resources that scale up automatically with your file count.' },
    models: HOSTED_MODELS,
    data: HOSTED_DATA,
    cost: { zh: '从建库开始按小时计费，可以用 Agent Plan 额度抵扣。', en: 'Billed hourly from the moment a library is created; Agent Plan credits apply.' },
    needs: HOSTED_NEEDS,
    cta: [
      { id: 'product', label: { zh: '了解企业版', en: 'Explore Enterprise' }, href: LINKS.hostedProduct },
      { id: 'setup-guide', label: { zh: '开通指南', en: 'Setup guide' }, href: LINKS.hostedQuickstart },
      { id: 'pricing', label: { zh: '计费说明', en: 'Pricing' }, href: LINKS.billing },
    ],
  },
  private: {
    name: { zh: '私有化部署版', en: 'Private deployment' },
    inline: { zh: 'OpenViking 私有化部署版', en: 'a private deployment of OpenViking' },
    sub: { zh: 'BYOC / VPC / 离线', en: 'BYOC / VPC / offline' },
    tagline: { zh: '数据必须留在你自己的环境里，还要官方支持。', en: 'Data stays in your own environment, with official support.' },
    who: { zh: '数据必须留在自己的云账号、VPC 或机房里的企业。', en: 'Organizations whose data must stay in their own cloud account, VPC or data center.' },
    ops: { zh: '部署在你自己的 Kubernetes 上，由你的运维团队管理；提供分布式部署和官方技术支持。', en: 'Runs on your own Kubernetes, operated by your team, with distributed deployment and official support.' },
    models: { zh: '可以对接火山方舟或你自己的模型服务；离线环境要自备模型。', en: 'Volcengine Ark or your own model services; offline sites bring their own models.' },
    data: { zh: '你自己的云账号、VPC 或离线环境。', en: 'Your own cloud account, VPC or offline site.' },
    cost: { zh: '按部署规模和支持需求定制，联系团队获取方案。', en: 'Tailored to your deployment and support needs; contact the team for a quote.' },
    needs: { zh: 'Kubernetes 和运维团队。提交申请表，团队确认后会把安装包和试用 License 发到你的邮箱。', en: 'Kubernetes and an ops team. Once the team confirms your application, the package link and a trial license arrive by email.' },
    cta: [
      { id: 'trial-form', label: { zh: '申请私有化试用', en: 'Request a trial license' }, href: LINKS.privateForm },
      { id: 'commercial', label: { zh: '商业版说明', en: 'Commercial editions' }, href: LINKS.commercial },
    ],
  },
};

/* One-line reason when an edition is the pick. */
export const FIT_REASONS = {
  oss: { zh: '你愿意自己动手。开源版免费、功能完整，模型也由你来选。', en: "You're happy to run it yourself. Open source is free, full-featured, and you choose the models." },
  personal: { zh: '一个人用，又不想运维：开通就能用，前 50 个文件免费。', en: 'One user and no appetite for ops: sign up and go, with the first 50 files free.' },
  enterprise: { zh: '多人、多 agent、要隔离，又不想自己养运维：独享资源，多个隔离的数据空间。', en: 'Many people and agents, isolation required, no ops team to spare: dedicated resources and isolated data spaces.' },
  private: { zh: '数据必须留在你自己的环境里：部署在你的云账号、VPC 或离线环境，带官方支持。', en: 'Your data has to stay in your environment: it runs in your cloud account, VPC or offline, with official support.' },
};

/* Why an edition is not a fit. */
export const BLOCK_REASONS = {
  residency: { zh: '你要求数据留在自己的云账号、VPC 或机房里，而托管版的数据存放在火山引擎上。', en: 'Your data must stay in your own cloud account, VPC or data center; managed data lives on Volcengine.' },
  gateway: { zh: '托管版要绑定方舟凭证，和“外部模型 API 一律不许用”冲突。', en: 'The managed service needs an Ark credential, which conflicts with “no external model APIs.”' },
  singleUser: { zh: '个人版面向单个用户，多人共用请选托管版 · 企业版。', en: 'Managed · Personal is built for one user; for shared use, choose Managed · Enterprise.' },
};

export const NOTES = {
  arkNeeded: { zh: '托管版用豆包模型做摘要和记忆提取，你的 agent 照常用现在的模型；开通时绑定一个方舟凭证。', en: 'The managed service uses Doubao models for summaries and memory extraction, and your agent keeps its current model; link one Ark credential when you sign up.' },
  fixedModels: { zh: '想让 OpenViking 本身也只用本地模型，选开源版或私有化部署版。', en: 'To keep OpenViking itself on local models, choose Open source or Private deployment.' },
  regionOverseas: { zh: '托管版部署在火山引擎北京地域，海外用户也可以使用；接入前按你的业务评估跨境延迟和数据合规要求。海外区域的托管服务（BytePlus）即将上线。', en: "The managed service runs in Volcengine's Beijing region and is available to overseas users; review cross-border latency and data-compliance requirements for your workload. Overseas-region hosting on BytePlus is coming soon." },
  regionBoth: { zh: '托管版部署在火山引擎北京地域，海外用户也可以使用；为海外用户接入前，评估一下跨境延迟和数据合规要求。海外区域的托管服务（BytePlus）即将上线。', en: "The managed service runs in Volcengine's Beijing region and serves overseas users too; review cross-border latency and data compliance for them. Overseas-region hosting on BytePlus is coming soon." },
  ownModels: { zh: '可以接你自己的模型：摘要和记忆提取需要一个 VLM，Ollama、litellm 和 OpenAI 兼容接口都支持；embedding 默认用内置本地模型。', en: 'Works with your own models: summaries and memory extraction need a VLM (Ollama, litellm and OpenAI-compatible APIs all work); embedding defaults to a built-in local model.' },
  noOfficialSupport: { zh: '开源版可以直接装在你自己的环境里，有问题可以到 GitHub 和 Discord 社区交流；需要分布式部署和官方技术支持时，选私有化部署版。', en: 'Open source runs inside your environment, with community help on GitHub and Discord; for distributed deployment and official support, choose Private deployment.' },
  agpl: { zh: '开源版采用 AGPLv3，许可方面的问题可以直接联系团队。', en: 'Open source is AGPLv3; bring any licensing questions to the team.' },
  opsGap: { zh: '私有化部署基于 Kubernetes，需要有人负责运维。运维人手紧的话，申请时写明情况，和团队商量交付与支持方式。', en: 'Private deployment runs on Kubernetes and needs someone to operate it. If ops capacity is tight, note it in the form and work out delivery and support with the team.' },
  offlinePlan: { zh: '离线环境需要自备模型服务，并规划好 License 续期。', en: 'Offline sites bring their own model services and plan license renewal.' },
};

/* Shown on the pick when a tie-break or the "I wouldn't pay" rule decided it.
 * Functions of the two editions involved, so the note names them. */
export const TIE_NOTES = {
  managedOverOss: (w) => ({
    zh: `${w.zh}和开源版得分接近，相差不到 5 分。这种情况我们优先推荐托管版：省下的部署和运维时间，通常比账单更值钱。`,
    en: `${w.en} and Open source scored less than 5 points apart. In that case we recommend the managed plan: the setup and ops time you save is usually worth more than the bill.`,
  }),
  privateWithSre: (w, o) => ({
    zh: `${w.zh}和${o.zh}得分接近，相差不到 5 分。你们有 SRE 或平台团队，所以优先推荐私有化部署版。`,
    en: `${w.en} and ${o.en} scored less than 5 points apart. You have SREs or a platform team, so we recommend Private deployment.`,
  }),
  otherOverPrivate: (w, o) => ({
    zh: `${w.zh}和${o.zh}得分接近，相差不到 5 分。私有化部署需要有人运维，所以优先推荐${w.zh}。`,
    en: `${w.en} and ${o.en} scored less than 5 points apart. A private deployment needs someone to run it, so we recommend ${w.en}.`,
  }),
  freeFirst: (w, o) => ({
    zh: `你选了“不花钱”，所以先推荐免费的开源版。团队有了预算，再看看${o.zh}。`,
    en: `You said you wouldn't pay, so we start you on the free open-source edition. Once there's a budget, take another look at ${o.en}.`,
  }),
};

/* "Where it fits" bullets, in priority order; the result shows up to four.
 * `memo` is the same point phrased for the one-pager. */
export const FITS = [
  {
    id: 'reexplain',
    memo: { zh: '每次开新会话都要重讲一遍项目背景', en: 'Every new session starts with re-explaining the project' },
    when: a => a.reexplain === 'essay' || a.reexplain === 'groundhog',
    zh: '你每次都在重讲背景。OpenViking 在会话提交后提取记忆，下次开工时 agent 能自己找回来，开工前那段背景介绍可以退休了。',
    en: 'You re-explain the background every time. OpenViking extracts memories after a session is committed, so next time the agent finds them itself and the daily briefing can retire.',
  },
  {
    id: 'bigmd',
    memo: { zh: 'agent 的规则全堆在一个越来越长的 AGENTS.md / CLAUDE.md 里', en: 'Agent rules pile up in an ever-growing AGENTS.md / CLAUDE.md' },
    when: a => a.memhack === 'bigmd',
    zh: 'AGENTS.md 越写越长是典型症状。OpenViking 分层加载：先给 agent 看摘要，需要时再读全文，不用把所有规矩塞进一个文件。',
    en: 'An ever-growing AGENTS.md is the classic symptom. OpenViking loads in tiers: summaries first, full text only when needed, so not every rule has to live in one file.',
  },
  {
    id: 'crossTool',
    memo: { zh: '换一个 agent 工具，积累下来的记忆就清零', en: 'Switching agent tools resets everything they had learned' },
    when: a => a.memhack === 'builtin' || a.share === 'fewtools',
    zh: '换个工具记忆就清零？OpenViking 是独立的服务，Claude Code、Codex、Cursor、OpenClaw 这些客户端可以接同一份上下文。',
    en: 'Memory resets when you switch tools? OpenViking is a separate service, so Claude Code, Codex, Cursor, OpenClaw and others can share one context.',
  },
  {
    id: 'homemade',
    memo: { zh: '自研的记忆脚本和向量库需要持续维护', en: 'Our home-grown memory scripts and vector store need constant upkeep' },
    when: a => a.memhack === 'homemade',
    zh: '你已经手搓了半个 OpenViking。把向量库、摘要脚本和玄学，换成一个有目录、能 ls 和 grep 的上下文数据库。',
    en: "You've already built half of OpenViking by hand. Swap the vector store, the summarizer and the prayer for a context database with real directories you can ls and grep.",
  },
  {
    id: 'retrieval',
    memo: { zh: '现有 RAG 召回经常不准，调一个参数会牵动多个指标', en: 'Our RAG retrieval is often off, and every tuning change moves several metrics' },
    when: a => a.retrieval === 'rag' || a.retrieval === 'spaghetti',
    zh: '召回“看着相关、其实不对”，常常是因为切块丢了结构。OpenViking 用 viking:// 目录组织资料，检索可以限定在某个目录里。',
    en: 'When results look relevant but aren’t, chunking has usually thrown away the structure. OpenViking keeps material in viking:// directories, and search can be scoped to a single directory.',
  },
  {
    id: 'volume',
    memo: { zh: '资料散在多个仓库、wiki 和文档里，agent 只能看到手动贴进去的部分', en: 'Knowledge is spread across repos, wikis and docs; agents only see what we paste in' },
    when: a => a.volume === 'many' || a.volume === 'ocean',
    zh: '资料、记忆和技能放进同一个文件系统，agent 可以像逛目录一样 ls、tree、read，不用你一段段贴给它。',
    en: 'Resources, memories and skills live in one filesystem the agent browses with ls, tree and read, instead of waiting for you to paste the right slice.',
  },
  {
    id: 'isolation',
    memo: { zh: '多人或多个用户共用上下文，记忆必须相互隔离', en: 'Several people or end users share context, and their memories must stay isolated' },
    when: a => a.share === 'team' || a.share === 'customers',
    zh: '既要共享又要隔离：开源内核自带 account / user 多租户和 ACL，托管版 · 企业版提供多个隔离的数据空间。',
    en: 'Shared yet isolated: the open-source core ships account / user multi-tenancy and ACLs, and Managed · Enterprise adds multiple isolated data spaces.',
  },
  {
    id: 'swarm',
    memo: { zh: '多个 agent 之间没有共享的上下文，交接靠人转述，结论经常互相冲突', en: 'Our agents share no context, hand-offs depend on people relaying them, and conclusions often conflict' },
    when: a => a.swarm === 'copy' || a.swarm === 'chaos' || a.swarm === 'orchestrated',
    zh: '多个 agent 从同一份资料和技能出发；定下来的结论写进共享资源，后来的 agent 直接读到，不用再靠人在中间转述。',
    en: 'Your agents start from one shared set of resources and skills. Settled decisions go into that shared space, so the next agent reads them directly instead of waiting for someone to relay them.',
  },
  {
    id: 'residency',
    memo: { zh: '数据必须留在自己的云账号、VPC 或机房里', en: 'Data has to stay in our own cloud account, VPC or data center' },
    when: a => a.compliance === 'account' || a.compliance === 'offline',
    zh: '数据必须留在自己的环境里：私有化部署版装在你的云账号、VPC 或离线环境中，带官方技术支持。',
    en: 'Data must stay in your environment: Private deployment runs in your cloud account, VPC or an offline site, with official support.',
  },
];

/* "Good to know" bullets; the result shows one or two. */
export const FINE_PRINT = [
  {
    id: 'stateless',
    when: a => a.reexplain === 'never',
    zh: '你的任务大多一次完成，长期记忆能帮上的忙有限。',
    en: 'Most of your tasks finish in one go, so long-term memory has limited room to help.',
  },
  {
    id: 'vectors',
    when: a => a.retrieval === 'vectorsok',
    zh: '现有的向量检索效果不错的话，可以先继续用；我们的 FAQ 也是这么建议的。',
    en: 'If your current vector search works well, keep using it for now; our FAQ says the same.',
  },
  {
    id: 'chatOnly',
    when: a => a.stage === 'chat',
    zh: 'OpenViking 通过 agent 客户端（Claude Code、Codex、Cursor 等）或你自己的应用接入；从网页聊天转到这些工具后，它就能派上用场。',
    en: 'OpenViking connects through agent clients (Claude Code, Codex, Cursor and others) or your own app; it comes into play once you move beyond a web chat box.',
  },
  {
    id: 'wiring',
    when: () => true,
    zh: '服务准备好以后，把 agent 接上就能用：主流客户端都有官方插件和一键安装脚本。',
    en: "Once the server is up, connect your agents and you're set: the main clients have official plugins and a one-line installer.",
  },
  {
    id: 'asyncRecall',
    when: () => true,
    zh: '记忆在会话提交后由后台异步提取；当前会话里刚说的内容，agent 直接从上下文里读。',
    en: "Memories are extracted in the background after a session is committed; anything said in the current session is already in the agent's context.",
  },
];

export const NUDGES = {
  personalTrial: {
    zh: '哪天不想自己维护服务了，可以换到托管版 · 个人版：和开源版同一套代码内核，前 50 个文件免费，之后按小时计费，开通时绑定一个方舟凭证。',
    en: 'If you ever tire of running the server, Managed · Personal runs the same code core as open source: the first 50 files are free, then hourly billing, and you link one Ark credential at sign-up.',
  },
  enterpriseLater: {
    zh: '团队用开源版完全可行。等运维开始占用周会时间，可以看看托管版 · 企业版。',
    en: 'A team can run open source just fine. When operating it starts eating into your weekly meeting, look at Managed · Enterprise.',
  },
  personalUpgrade: {
    zh: '等团队一起用了，再开通托管版 · 企业版。',
    en: 'When your team wants in, start Managed · Enterprise.',
  },
  enterpriseTrial: {
    zh: '想先验证效果，可以开一个个人版库单人试跑（前 50 个文件免费），确定了再开企业版。',
    en: 'To validate first, open a Managed · Personal library and try it solo (first 50 files free), then start Managed · Enterprise.',
  },
  privatePilot: {
    zh: '提交申请后，可以先用开源版在内网起一个单节点，把接入流程跑通。',
    en: 'While your request is processed, stand up a single open-source node internally and get the integration working.',
  },
};

export const WHAT_IF_LABELS = {
  'ops:nope': { zh: '不想自己运维', en: 'If nobody wanted to run it' },
  'share:team': { zh: '换成团队一起用', en: 'If a team shared it' },
  'budget:budget': { zh: '团队批了预算', en: 'If the team had a budget' },
  'compliance:account': { zh: '数据必须留在自己的云账号', en: 'If data had to stay in your own account' },
  'models:ark': { zh: '已经在用火山方舟', en: 'If you were already on Volcengine Ark' },
};

export const LOW_FIT_TRIGGERS = [
  { zh: '你第三次给 agent 解释同一件事', en: 'You explain the same thing to an agent for the third time' },
  { zh: '你的 AGENTS.md 一屏装不下了', en: 'Your AGENTS.md no longer fits on one screen' },
  { zh: '第二个 agent 加入，开始和第一个各记各的', en: 'A second agent joins and starts keeping its own notes' },
  { zh: '有人问：“能不能让它记住每个用户？”', en: 'Someone asks, “Can it remember each user?”' },
];

export const INTERSTITIAL = [
  { zh: '正在翻阅你的 AGENTS.md……', en: 'Reading your AGENTS.md…' },
  { zh: '正在统计你重讲背景的次数……', en: 'Counting how often you re-explain things…' },
  { zh: '正在和你的安全团队开会……', en: 'In a meeting with your security team…' },
  { zh: '正在给你的 agent 做记忆测试……', en: 'Giving your agent a memory test…' },
];
