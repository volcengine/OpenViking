/* Text builders for everything the reader can copy: the AI prompts, the
 * one-pager for a manager, and the share line. Pure functions (lang in,
 * string out), so the copy buttons and llm.txt can be checked against them. */

import { QUESTIONS, DIMS, DIM_ORDER, BANDS, PERSONAS } from './quiz-data.js';
import { LINKS, FORMS, FIT_REASONS } from './copy.js';

const pick = (v, lang) => (v && typeof v === 'object' ? (v[lang] ?? v.en) : v);

export const FACTS = {
  zh: `关于 OpenViking 的公开事实（请只依据这些和你能核实的公开资料，不要编造价格、SLA 或认证）：
- OpenViking 是面向 AI Agent 的开源上下文数据库，由火山引擎 Viking 团队开发。它把知识（Resources）、记忆（Memories）和技能（Skills）放进同一个文件系统，用 viking:// 路径组织，agent 可以用 ls、tree、read、grep 浏览。
- 分层加载：先读摘要（L0 约 256 字、L1 约 4k 字），需要时再读全文；检索可以限定在某个目录里。
- 记忆在会话提交后异步提取，存成可以直接查看的 Markdown。
- 支持 Claude Code、Codex、Cursor、TRAE、OpenCode、OpenClaw、Hermes Agent、LangChain / LangGraph 和任何 MCP 客户端。所有接入都需要一个运行中的 OpenViking 服务（自部署或托管）。
- 不适合的场景：现有的向量检索已经够用（官方 FAQ 也这么建议）；无状态的一次性调用；要求刚说完就能从长期记忆召回（记忆在后台异步处理）。

四种方案：
1. 开源版（自部署）：AGPLv3，免费，无需激活码。自带多租户（account / user，ROOT / ADMIN / USER 角色）、ACL、静态加密、MCP 的 OAuth 2.1，以及 Prometheus / OpenTelemetry 可观测。单节点运行，或自行搭多副本（需要远程向量后端），没有自动故障切换。模型自己配：embedding 默认用内置本地模型，不需要 key；摘要和记忆提取需要一个 VLM，支持火山方舟、OpenAI 兼容接口、Ollama、litellm 等。安装方式包括 uv tool install openviking、Docker 和 Helm。
2. 托管版 · 个人版（火山引擎 OpenViking Context）：全托管，不用部署、升级，也不用自己部署模型，和开源版共享同一套代码内核；单用户；每个个人版库前 50 个文件免费，之后按小时按量计费，可用 Agent Plan 额度抵扣。
3. 托管版 · 企业版（火山引擎 OpenViking Context）：多用户、多个隔离的数据空间，独享资源并按文件量自动扩容；从建库开始按小时计费，可用 Agent Plan 额度抵扣。
   托管版两档的共同条件：完成实名认证的火山引擎账号；至少绑定一个方舟凭证（Agent Plan、Coding Plan 或自有推理接入点），模型固定为豆包系列；服务部署在火山引擎华北2（北京），数据存放在火山引擎上；海外用户也可以使用（接入前评估跨境网络延迟和数据合规要求），海外区域的托管服务（BytePlus）即将上线。提供商业 SLA 与专业 Oncall 支持。价格以计费说明为准：${LINKS.billing}
4. 私有化部署版：部署在你自己的云账号 / VPC（BYOC）或离线环境，提供分布式部署和官方技术支持，用 License 激活；基于 Kubernetes，需要运维团队。通过表单申请，团队确认后邮件发送安装包和试用 License。价格和支持方案按部署规模和需求定制，联系团队获取。离线环境需要自备模型服务，并规划 License 续期。
- 许可或合规问题（包括 AGPL）请直接咨询团队，不要自行做法律解读。`,
  en: `Public facts about OpenViking (rely only on these and on public sources you can verify; do not invent prices, SLAs or certifications):
- OpenViking is an open-source context database for AI agents, built by Volcengine's Viking team. It puts resources (knowledge), memories and skills into one filesystem organized by viking:// paths, which agents browse with ls, tree, read and grep.
- Tiered loading: summaries first (L0 about 256 characters, L1 about 4k), full content on demand; search can be scoped to a directory.
- Memories are extracted asynchronously after a session is committed and stored as readable Markdown.
- Supported clients include Claude Code, Codex, Cursor, TRAE, OpenCode, OpenClaw, Hermes Agent, LangChain / LangGraph and any MCP client. Every integration needs a running OpenViking server, self-hosted or managed.
- Where it is not the right tool: vector search you already have that works well (the official FAQ says the same); stateless one-shot calls; needing a fact recallable from long-term memory the moment it is said (memories are processed in the background).

Four ways to run it:
1. Open source (self-hosted): AGPLv3, free, no activation key. Includes multi-tenancy (account / user, ROOT / ADMIN / USER roles), ACLs, encryption at rest, OAuth 2.1 for MCP, and Prometheus / OpenTelemetry observability. Runs as a single node, or as replicas you assemble yourself (needs a remote vector backend); no automatic failover. Bring your own models: embedding defaults to a built-in local model with no key; summaries and memory extraction need a VLM (Volcengine Ark, OpenAI-compatible APIs, Ollama, litellm and more). Install with uv tool install openviking, Docker or Helm.
2. Managed · Personal (OpenViking Context on Volcengine): fully managed, no deployment, upgrades or model hosting, same code core as open source; single user; the first 50 files in each Personal library are free, then hourly pay-as-you-go; Agent Plan credits apply.
3. Managed · Enterprise (OpenViking Context on Volcengine): many users, multiple isolated data spaces, dedicated resources that scale up automatically with file count; billed hourly from the moment a library is created; Agent Plan credits apply.
   Both managed plans require a real-name-verified Volcengine account and at least one Ark credential (Agent Plan, Coding Plan or your own inference endpoint); models are fixed to the Doubao family. The service runs in Volcengine's Beijing region (cn-beijing) and stores data on Volcengine; it is available outside mainland China (review cross-border latency and data-compliance requirements). Overseas-region hosting on BytePlus is coming soon. Commercial SLA and professional on-call support. Current prices: ${LINKS.billing}
4. Private deployment: runs in your own cloud account / VPC (BYOC) or offline, adds distributed deployment and official support, activated by a license key; Kubernetes-based and needs an ops team. Apply through a form; once the team confirms, a package link and a trial license are emailed. Pricing and support are tailored to the deployment; contact the team for a quote. Offline sites bring their own model services and plan license renewal.
- For licensing or compliance questions (including AGPL), ask the team; do not offer a legal interpretation.`,
};

export function answerLines(answers, lang) {
  return QUESTIONS.map((q, i) => {
    const opt = q.options.find(o => o.key === answers[q.id]);
    const chosen = opt ? pick(opt.t, lang) : (lang === 'zh' ? '（未作答）' : '(not answered)');
    return `Q${i + 1}. ${pick(q.q, lang)} → ${chosen}`;
  }).join('\n');
}

export function resultLine(result, lang) {
  const band = BANDS.find(b => b.key === result.band);
  const persona = PERSONAS[result.persona];
  const dims = DIM_ORDER.map(d => `${pick(DIMS[d].name, lang)} ${result.dims[d]}`).join(lang === 'zh' ? '、' : ', ');
  const f = result.forms;
  const ranking = f.ranked.map(k => {
    const name = pick(FORMS[k].name, lang);
    if (f.blocks[k].length) return `${name} ${lang === 'zh' ? '不适用' : 'not a fit'}`;
    return `${name} ${f.scores[k]}`;
  }).join(lang === 'zh' ? '、' : ', ');
  if (lang === 'zh') {
    return `适配度 ${result.fit}/100（${pick(band.title, lang)}）；类型：${pick(persona.name, lang)}；五维：${dims}；推荐：${pick(FORMS[f.top].name, lang)}；各方案匹配度：${ranking}`;
  }
  return `Fit ${result.fit}/100 (${pick(band.title, lang)}); type: ${pick(persona.name, lang)}; profile: ${dims}; recommended: ${pick(FORMS[f.top].name, lang)}; edition match: ${ranking}`;
}

export function promptWithAnswers(answers, result, lang) {
  if (lang === 'zh') {
    return `我刚在 OpenViking 博客上做了一份“你该不该用 OpenViking”的测验。请你作为独立的技术顾问，结合你对我和我项目的了解，重新判断一次。如果你能看到我的代码仓库或工作环境，以你看到的为准，而不是我的选择题答案。如果更简单的办法就够用（比如维护好一份 AGENTS.md，或者继续用现有的向量库），请直接说。

我的答案：
${answerLines(answers, lang)}

测验页给出的结果（供你对照）：
${resultLine(result, lang)}

${FACTS.zh}

请用中文、按下面的结构回答，简洁直接：
1. 结论：我现在该不该用 OpenViking？给一个 0–100 的适配度和一句话理由。
2. 方案：开源版、托管版 · 个人版、托管版 · 企业版、私有化部署版，各给出“高 / 中 / 低 / 不适用”和理由。数据驻留和模型来源这两条硬约束要单独核对，地域也要考虑。
3. 和测验结果的分歧：哪里同意，哪里不同意，为什么。
4. 会改变结论的因素：哪 1–3 个事实一变，推荐就会变。
5. 下一步：如果建议用，给出本周就能做完的三步；如果不建议，告诉我现在该用什么。
6. 未知项：列出你从现有信息里判断不了的事。不要猜价格、SLA 或认证。`;
  }
  return `I just took a “Should you use OpenViking?” quiz on the OpenViking blog. Please act as an independent technical advisor and judge again, using what you know about me and my project. If you can see my repository or working environment, trust what you see over my multiple-choice answers. If something simpler would do (a well-kept AGENTS.md, or the vector store I already have), say so.

My answers:
${answerLines(answers, lang)}

The quiz page's result (for comparison):
${resultLine(result, lang)}

${FACTS.en}

Reply in English, concise and direct, with this structure:
1. Verdict: should I use OpenViking now? A 0–100 fit score and one sentence of reasoning.
2. Edition: rate Open source, Managed · Personal, Managed · Enterprise and Private deployment as high / medium / low / not a fit, with reasons. Check data residency and model source separately (they are hard constraints), and factor in region.
3. Where you disagree with the quiz: what you agree with, what you don't, and why.
4. What would flip it: the 1–3 facts that would change your recommendation.
5. Next steps: if yes, three steps I can finish this week; if no, what I should use instead for now.
6. Unknowns: what you cannot judge from the information available. Don't guess prices, SLAs or certifications.`;
}

export function promptInterview(lang) {
  if (lang === 'zh') {
    return `请帮我判断：我（或者当前这个项目）该不该接入 OpenViking，适合用哪种方案。请基于事实独立判断；如果不需要，直接说。

第一步：如果你能读取当前项目，先做只读检查。不要安装任何东西，不要修改文件；涉及配置时只看环境变量的名字，不要输出任何密钥的值。
- agent 指令文件：AGENTS.md、CLAUDE.md、.cursor/rules 等，记下长度；如果能看 git log，再看它们改得多频繁。
- 自建的记忆或检索：向量库（chromadb、faiss、pgvector、qdrant、milvus 等）、embedding 调用、LangChain / LangGraph、摘要或 memory 相关模块。
- 多 agent 或多用户的迹象：编排框架、多个 agent 配置、按用户或租户隔离数据的代码。
- 部署方式和地域：Dockerfile、docker-compose、Kubernetes / Helm、云厂商配置。
- 模型来源：OpenAI、Anthropic、火山方舟、本地模型或内部网关。
如果读不到项目，就跳过这一步。

第二步：向我提问，一次只问一个，最多 8 个，只问项目里看不出来的。至少要弄清：每次开新会话要重讲多少背景；资料有多少、散在哪里；谁在共用这份上下文；数据能不能放在公有云上；用户和服务器在中国大陆还是海外；谁来运维；模型从哪来；打算怎么付费。

${FACTS.zh}

第三步：问完以后，用中文按下面的结构给结论：
1. 证据：你看到了什么、问到了什么（项目里的发现附上文件路径）。
2. 结论：0–100 的适配度和一句话理由。
3. 方案：开源版、托管版 · 个人版、托管版 · 企业版、私有化部署版，各给出“高 / 中 / 低 / 不适用”和理由；数据驻留、模型来源和地域单独核对。
4. 接入方案：如果建议用，说明哪些内容适合放进 resources、哪些作为 memories、哪些作为 skills，接哪个客户端或 SDK，以及前三步怎么做。
5. 会改变结论的因素，以及你判断不了的未知项。不要猜价格、SLA 或认证。`;
  }
  return `Help me decide whether I (or the project in front of you) should adopt OpenViking, and which way to run it. Judge independently from the facts; if I don't need it, say so.

Step 1: if you can read the current project, start with a read-only pass. Do not install anything or modify any file; for configuration, look only at environment variable names and never print a secret's value.
- Agent instruction files: AGENTS.md, CLAUDE.md, .cursor/rules and similar. Note their length and, if git log is available, how often they change.
- Home-grown memory or retrieval: vector stores (chromadb, faiss, pgvector, qdrant, milvus…), embedding calls, LangChain / LangGraph, summarization or "memory" modules.
- Signs of multiple agents or users: orchestration frameworks, several agent configs, code that isolates data per user or tenant.
- How and where it deploys: Dockerfile, docker-compose, Kubernetes / Helm, cloud provider config.
- Where models come from: OpenAI, Anthropic, Volcengine Ark, local models or an internal gateway.
If you can't read a project, skip this step.

Step 2: ask me questions, one at a time, at most 8, only about what the project can't tell you. At minimum find out: how much background I re-explain in each new session; how much material there is and where it lives; who shares the context; whether data may sit on a public cloud; whether users and servers are in mainland China or elsewhere; who runs infrastructure; where models come from; and how I'd expect to pay.

${FACTS.en}

Step 3: once you have the answers, reply in English with this structure:
1. Evidence: what you found and what I told you (file paths for anything from the project).
2. Verdict: a 0–100 fit score and one sentence of reasoning.
3. Edition: rate Open source, Managed · Personal, Managed · Enterprise and Private deployment as high / medium / low / not a fit, with reasons; check data residency, model source and region separately.
4. Integration plan: if yes, which material belongs in resources, which in memories and which in skills, which client or SDK to connect, and the first three steps.
5. What would flip the verdict, and what you can't determine. Don't guess prices, SLAs or certifications.`;
}

export function shareUrl(code) {
  const origin = typeof window !== 'undefined' ? window.location.origin : 'https://blog.openviking.ai';
  return `${origin}/post/should-you-use-openviking/?r=${code}`;
}

export function shareText(result, url, lang) {
  const persona = PERSONAS[result.persona];
  const band = BANDS.find(b => b.key === result.band);
  const form = FORMS[result.forms.top];
  if (lang === 'zh') {
    if (result.lowFit) return `我在 OpenViking 适配测试里是「${persona.name.zh}」，适配度 ${result.fit}/100，结论是我的 agent 暂时用不上它。你是哪种 Agent 饲养员？${url}`;
    return `我在 OpenViking 适配测试里是「${persona.name.zh}」，适配度 ${result.fit}/100（${band.title.zh}），最适合我的是${form.name.zh}。你是哪种 Agent 饲养员？${url}`;
  }
  const typeName = persona.name.en.replace(/^The /, '');
  if (result.lowFit) return `I got ${typeName} on the OpenViking fit test (${result.fit}/100). Verdict: my agents are doing fine without it for now. What kind of agent wrangler are you? ${url}`;
  return `I got ${typeName} on the OpenViking fit test: ${result.fit}/100, “${band.title.en}”. Best fit for me: ${form.name.en}. What kind of agent wrangler are you? ${url}`;
}

export function bossMemo({ result, problems, url, lang }) {
  const persona = PERSONAS[result.persona];
  const band = BANDS.find(b => b.key === result.band);
  const top = result.forms.top;
  const form = FORMS[top];
  const cta = form.cta[0];
  const lines = problems.length ? problems.slice(0, 3) : [pick(persona.pain, lang)];
  const bullets = lines.map(l => `- ${l}`).join('\n');
  if (lang === 'zh') {
    return `关于试用 OpenViking 的一页说明

结论：建议用两周时间试用 ${form.inline.zh}。我们在 OpenViking 博客的适配测试里得到 ${result.fit}/100：「${band.title.zh}」。

我们现在的问题：
${bullets}

OpenViking 是什么：火山引擎 Viking 团队开源的 AI agent 上下文数据库，把知识、记忆和技能放进同一个文件系统，用 viking:// 路径组织，可以接入 Claude Code、Codex、Cursor 等 agent 和任何 MCP 客户端。

为什么选${form.name.zh}：${FIT_REASONS[top].zh}
需要准备：${form.needs.zh}
费用：${form.cost.zh}

两周试用计划：
1. 第 1–3 天：接入一个 agent，导入一个代码库或一组文档。
2. 第 4–10 天：日常使用，记录重讲背景的次数、token 消耗，以及检索有没有找对。
3. 第 11–14 天：对比前后，决定是否扩大范围。

参考：OpenViking 0.3.22 的公开评测（VLM 为豆包 2.0 Pro）中，在长对话记忆基准 LoCoMo 上，Claude Code 用自带记忆的准确率是 57.21%，接入 OpenViking 后是 80.32%。实际效果取决于数据、模型和配置：${LINKS.benchmark}

链接：
- ${pick(cta.label, 'zh')}：${pick(cta.href, 'zh')}
- 我的测验结果：${url}`;
  }
  return `One-pager: a two-week OpenViking trial

Bottom line: I'd like to run a two-week trial of ${form.inline.en}. We scored ${result.fit}/100 on the OpenViking blog's fit test: “${band.title.en}”.

Problems we have today:
${bullets}

What it is: an open-source context database for AI agents from Volcengine's Viking team. It keeps knowledge, memories and skills in one filesystem organized by viking:// paths, and connects to Claude Code, Codex, Cursor and any MCP client.

Why ${form.name.en}: ${FIT_REASONS[top].en}
What we need: ${form.needs.en}
Cost: ${form.cost.en}

Two-week trial plan:
1. Days 1–3: connect one agent and import one codebase or doc set.
2. Days 4–10: use it daily; track how often we re-explain context, token usage, and whether retrieval finds the right thing.
3. Days 11–14: compare before and after, then decide whether to expand.

For reference: in OpenViking 0.3.22's published LoCoMo results (long-conversation memory, Doubao 2.0 Pro as the VLM), Claude Code scored 57.21% with its native memory and 80.32% with OpenViking. Results depend on data, models and configuration: ${LINKS.benchmark}

Links:
- ${pick(cta.label, 'en')}: ${pick(cta.href, 'en')}
- My quiz result: ${url}`;
}
