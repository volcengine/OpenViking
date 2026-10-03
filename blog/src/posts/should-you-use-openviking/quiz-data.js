/* Questions, personas, bands and editions for the fit test.
 * Pure data: safe to import from the SSG build and from node tests.
 * Option keys are what the quiz stores and what the share code encodes
 * (by position), so never reorder options once the post is published. */

export const SLUG = 'should-you-use-openviking';

// Raw-point ceilings per dimension. Scores are round(100 * raw / MAX), clamped.
export const DIM_MAX = { A: 10, S: 12, C: 12, G: 6, O: 5 };
export const DIM_ORDER = ['A', 'S', 'C', 'G', 'O'];

export const DIMS = {
  A: {
    short: { zh: '健忘', en: 'Amnesia' },
    name: { zh: '健忘指数', en: 'Amnesia' },
    desc: {
      zh: 'agent 有多健忘，你要花多少时间帮它回忆',
      en: 'How forgetful your agent is, and how much of your time goes into reminding it',
    },
  },
  S: {
    short: { zh: '体量', en: 'Sprawl' },
    name: { zh: '知识体量', en: 'Sprawl' },
    desc: {
      zh: '它要知道的资料有多少、有多散，现有检索有多吃力',
      en: 'How much it needs to know, how scattered it is, and how much your current retrieval struggles',
    },
  },
  C: {
    short: { zh: '协作', en: 'Crowd' },
    name: { zh: '协作规模', en: 'Crowd' },
    desc: {
      zh: '多少人、多少 agent、多少租户共用这份上下文',
      en: 'How many people, agents and tenants share the context',
    },
  },
  G: {
    short: { zh: '管控', en: 'Control' },
    name: { zh: '管控要求', en: 'Control' },
    desc: {
      zh: '隔离、审计和数据驻留的要求有多硬',
      en: 'How strict your isolation, audit and data-residency rules are',
    },
  },
  O: {
    short: { zh: '动手', en: 'DIY' },
    name: { zh: '动手意愿', en: 'DIY drive' },
    desc: {
      zh: '你愿意、也有能力自己部署和运维多少东西',
      en: 'How much infrastructure you are willing and able to run yourself',
    },
  },
};

/* fx: raw points per dimension. Fit caps for some options live in scoring.js. */
export const QUESTIONS = [
  {
    id: 'stage',
    q: { zh: '你和 agent 现在是什么关系？', en: "What's your relationship with AI agents right now?" },
    aside: { zh: '没有对错，选最像你的那个。', en: 'No wrong answers. Pick the closest one.' },
    options: [
      { key: 'chat', fx: {}, t: { zh: '偶尔在聊天框里问两句，关掉页面就忘了', en: 'I ask a chatbot the odd question, then close the tab' } },
      { key: 'pair', fx: { A: 2, S: 1 }, t: { zh: '每天和 coding agent 结对，它是我最勤快、也最健忘的同事', en: 'I pair with a coding agent every day. Hardest-working, most forgetful coworker I have' } },
      { key: 'build', fx: { A: 1, S: 1, C: 1 }, t: { zh: '我在做 agent 产品，用的人不是我自己', en: "I'm building an agent product for people who aren't me" } },
      { key: 'fleet', fx: { A: 1, S: 1, C: 2, G: 1 }, t: { zh: '我管着一群 agent，还管着管 agent 的那群人', en: 'I run a fleet of agents, and the people who run them' } },
    ],
  },
  {
    id: 'reexplain',
    q: { zh: '新开一个会话，你要讲多少背景，agent 才能重新进入状态？', en: 'New session. How much do you explain before your agent is caught up?' },
    aside: { zh: '“我们用 pnpm，不用 npm。”这已经是第 47 次了。', en: '“We use pnpm, not npm.” For the 47th time.' },
    options: [
      { key: 'never', fx: {}, t: { zh: '不用讲，我的任务都是一次性的', en: 'No catching up needed. Every task is one-and-done' } },
      { key: 'minute', fx: { A: 1 }, t: { zh: '一两句话，还能忍', en: 'A sentence or two. Bearable' } },
      { key: 'essay', fx: { A: 3 }, t: { zh: '一段小作文，我专门存了一份，每次开工先粘贴', en: 'A short essay. I keep it in a text snippet, ready to paste' } },
      { key: 'groundhog', fx: { A: 4 }, t: { zh: '天天都是《土拨鼠之日》：它不记得昨天修过的 bug，也不记得我是谁', en: "Every day is Groundhog Day. It forgets yesterday's bug fix, our conventions, and who I am" } },
    ],
  },
  {
    id: 'memhack',
    q: { zh: '你现在靠什么让 agent “记住”事情？', en: 'How do you currently make your agent “remember” things?' },
    aside: { zh: '放心作答，没人统计你的 AGENTS.md 有多少行。', en: 'Be honest. Nobody is counting the lines in your AGENTS.md.' },
    options: [
      { key: 'nothing', fx: {}, t: { zh: '不让它记，每次从零开始，挺禅的', en: "I don't. A fresh start every time. Very zen" } },
      { key: 'bigmd', fx: { A: 2, S: 2 }, t: { zh: '一个越写越长的 AGENTS.md / CLAUDE.md，后半段它已经开始假装没看见', en: 'An AGENTS.md / CLAUDE.md that keeps growing. The agent now politely ignores the bottom half' } },
      { key: 'builtin', fx: { A: 2, S: 1, C: 1 }, t: { zh: '客户端自带的记忆，换个工具就清零', en: "My client's built-in memory, which resets the moment I switch tools" } },
      { key: 'homemade', fx: { A: 2, S: 2, O: 1 }, t: { zh: '自己搭的：向量库、摘要脚本，外加一点玄学', en: 'A homemade stack: a vector store, a summarizer script and a little prayer' } },
    ],
  },
  {
    id: 'volume',
    q: { zh: 'agent 要知道多少东西，才能把活干好？', en: 'How much does your agent need to know to do the job well?' },
    aside: { zh: '模型读过整个互联网，唯独没读过你们的 wiki。', en: 'The model has read the entire internet, except your wiki.' },
    options: [
      { key: 'prompt', fx: {}, t: { zh: '一段 prompt 就够', en: 'One prompt covers it' } },
      { key: 'repo', fx: { S: 1 }, t: { zh: '一个代码仓库，加几份文档', en: 'One repo and a few docs' } },
      { key: 'many', fx: { S: 3 }, t: { zh: '好几个仓库、一个 wiki、一摞设计文档，外加一堆 skill', en: 'Several repos, a wiki, a stack of design docs and a pile of skills' } },
      { key: 'ocean', fx: { S: 4, C: 1 }, t: { zh: '整个团队乃至公司的知识，多到没人完整看过', en: 'Everything the team, or the whole company, knows. More than any one person has read' } },
    ],
  },
  {
    id: 'retrieval',
    q: { zh: 'agent 去找资料的时候，过程大概是？', en: 'When your agent goes looking for something, what happens?' },
    aside: { zh: '包括那个只有老王知道的部署步骤。', en: 'Including the deploy step only Dave knows.' },
    options: [
      { key: 'paste', fx: {}, t: { zh: '它不找，我直接贴给它', en: "It doesn't. I paste things in" } },
      { key: 'grep', fx: { S: 1 }, t: { zh: 'grep，加上运气', en: 'grep, plus luck' } },
      { key: 'rag', fx: { S: 3 }, t: { zh: '一套 RAG：切块、embedding、top-k，召回一堆看着相关、其实不对的段落', en: "A RAG pipeline: chunk, embed, top-k, and back come paragraphs that look relevant and aren't" } },
      { key: 'spaghetti', fx: { S: 4 }, t: { zh: '三套 RAG、两个向量库，外加一堆 if-else，没人敢动', en: 'Three RAG pipelines, two vector stores and a pile of if-else. Nobody dares touch it' } },
      { key: 'vectorsok', fx: {}, t: { zh: '有向量检索或一套 RAG，效果挺好，暂时不想动', en: 'Vector search or a RAG setup that works fine. Not touching it' } },
    ],
  },
  {
    id: 'share',
    q: { zh: '这份上下文，谁在共用？', en: 'Who shares this context?' },
    aside: { zh: '换个问法：出了问题，谁会来找你。', en: 'Put differently: who comes looking for you when it breaks?' },
    options: [
      { key: 'me', fx: {}, t: { zh: '只有我自己', en: 'Just me' } },
      { key: 'fewtools', fx: { A: 1, C: 1 }, t: { zh: '只有我，但横跨好几个工具：Claude Code 记得的，Cursor 不知道', en: 'Just me, across several tools. What Claude Code knows, Cursor has never heard of' } },
      { key: 'team', fx: { C: 3 }, t: { zh: '一个团队：知识要共享，各人的记忆要隔开', en: 'A team: shared knowledge, separate personal memories' } },
      { key: 'customers', fx: { C: 4, G: 1 }, t: { zh: '我的用户或客户：每个人的记忆都不能串', en: 'My users or customers: their memories must never mix' } },
    ],
  },
  {
    id: 'swarm',
    q: { zh: '你的 agent 之间怎么交接工作？', en: 'How do your agents hand work to each other?' },
    aside: { zh: '如果交接全靠你手动转，选 B。', en: "If hand-offs go through you, that's B." },
    options: [
      { key: 'single', fx: {}, t: { zh: '只有一个 agent，它很孤独', en: "There's only one. It's lonely" } },
      { key: 'copy', fx: { A: 1, C: 2 }, t: { zh: '我当人肉总线：把 A 的输出复制给 B', en: "I'm the message bus: I copy A's output into B" } },
      { key: 'chaos', fx: { C: 3 }, t: { zh: '好几个 agent 各记各的，经常互相推翻', en: "Several agents with separate notes, regularly undoing each other's work" } },
      { key: 'orchestrated', fx: { S: 1, C: 4 }, t: { zh: '有编排框架，但共享状态是一团乱麻', en: 'We have an orchestrator. The shared state is a mess' } },
    ],
  },
  {
    id: 'compliance',
    q: { zh: '跟安全团队说“放到云上”，他们是什么表情？', en: 'You tell your security team “let’s put it in the cloud.” What face do they make?' },
    aside: { zh: '如果你就是安全团队，按你自己的表情选。', en: 'If you are the security team, go with your own face.' },
    options: [
      { key: 'who', fx: {}, t: { zh: '“我们有安全团队？”', en: '“We have a security team?”' } },
      { key: 'cloud', fx: { G: 1 }, t: { zh: '“正规公有云、有访问控制就行。”', en: '“A reputable public cloud with access control. Fine.”' } },
      { key: 'account', fx: { G: 3 }, t: { zh: '“数据必须留在我们自己的云账号或 VPC 里。先约个会。”', en: '“Data stays in our own cloud account or VPC. Let’s book a meeting.”' } },
      { key: 'offline', fx: { G: 4 }, t: { zh: '“一个字节都别出机房。会已经约好了。”', en: '“Not one byte leaves the building. The meeting is already booked.”' } },
    ],
  },
  {
    id: 'region',
    q: { zh: '你的用户和服务器主要在哪里？', en: 'Where are your users and servers, mostly?' },
    aside: { zh: '这题不开玩笑，它会影响推荐哪种方案。', en: 'No joke on this one. It shapes which edition we suggest.' },
    options: [
      { key: 'cn', fx: {}, t: { zh: '中国大陆', en: 'Mainland China' } },
      { key: 'overseas', fx: {}, t: { zh: '主要在海外', en: 'Mostly outside mainland China' } },
      { key: 'both', fx: {}, t: { zh: '两边都有', en: 'Both' } },
    ],
  },
  {
    id: 'ops',
    q: { zh: '凌晨三点服务挂了，谁起床？', en: 'The service goes down at 3 a.m. Who gets up?' },
    aside: { zh: '这题基本决定了你该自己部署，还是交给别人。', en: 'This one mostly decides whether you run it yourself.' },
    options: [
      { key: 'love', fx: { O: 4 }, t: { zh: '我。折腾基础设施是我的爱好，路由器固件都刷过', en: "Me. Running infrastructure is my idea of fun. I've flashed my router's firmware" } },
      { key: 'platform', fx: { O: 3 }, t: { zh: '我们的 SRE。Kubernetes 是他们的日常', en: 'Our SRE team. Kubernetes is their day job' } },
      { key: 'ok', fx: { O: 2 }, t: { zh: '我会起，但希望一年别超过一次', en: "I will, but I'd rather it happened once a year at most" } },
      { key: 'nope', fx: {}, t: { zh: '最好谁都不用起，这就是云厂商存在的意义', en: "Ideally nobody. That's what cloud vendors are for" } },
    ],
  },
  {
    id: 'models',
    q: { zh: '你的模型从哪儿来？', en: 'Where do your models come from?' },
    aside: { zh: 'OpenViking 要用一个 VLM 做摘要和记忆提取，所以得问一句。', en: 'OpenViking uses a VLM for summaries and memory extraction, so we have to ask.' },
    options: [
      { key: 'ark', fx: {}, t: { zh: '已经在用火山方舟：Agent Plan、Coding Plan 或自有推理接入点', en: 'Already on Volcengine Ark: Agent Plan, Coding Plan or my own inference endpoint' } },
      { key: 'apikeys', fx: {}, t: { zh: 'OpenAI、Anthropic 或其他厂商的 API', en: "OpenAI, Anthropic or another provider's API" } },
      { key: 'local', fx: {}, t: { zh: '本地模型，Ollama 是我最好的朋友', en: 'Local models. Ollama is my best friend' } },
      { key: 'gateway', fx: {}, t: { zh: '公司内部的模型网关，外部 API 一律不许用', en: 'An internal model gateway. External APIs are off-limits' } },
    ],
  },
  {
    id: 'budget',
    q: { zh: '如果能治好 agent 的健忘症，你打算怎么付钱？', en: "If something cured your agent's amnesia, how would you pay for it?" },
    aside: { zh: '这题不影响适配度，只影响推荐哪种方案。', en: "This one doesn't touch your fit score. It only steers which edition we suggest." },
    options: [
      { key: 'free', fx: {}, t: { zh: '不花钱，我有的是时间', en: "I wouldn't. I have more time than money" } },
      { key: 'coffee', fx: {}, t: { zh: '每月一杯咖啡钱，能换回几个下午就值', en: 'About a coffee a month, if it buys back a few afternoons' } },
      { key: 'budget', fx: {}, t: { zh: '团队有预算，我缺一个能说服老板的理由', en: 'There is a team budget. I need a reason my boss will accept' } },
      { key: 'procure', fx: {}, t: { zh: '采购、合同、评审，一样都不能少', en: 'Procurement, contracts, reviews: the full ceremony' } },
    ],
  },
];

export const QUESTION_IDS = QUESTIONS.map(q => q.id);

/* Fit bands, highest first. */
export const BANDS = [
  {
    key: 'textbook', min: 85,
    title: { zh: '教科书级病例', en: 'Textbook case' },
    line: { zh: '我们设计 OpenViking 的时候，脑子里想的大概就是你。', en: 'When we designed OpenViking, we were more or less picturing you.' },
  },
  {
    key: 'strong', min: 65,
    title: { zh: '缺的就是这块拼图', en: 'The missing piece' },
    line: { zh: '你遇到的问题，和 OpenViking 要解决的问题高度重合。', en: 'Your problems line up closely with the ones OpenViking was built to solve.' },
  },
  {
    key: 'worth', min: 45,
    title: { zh: '值得花一个下午', en: 'Worth an afternoon' },
    line: { zh: '有几处能明显受益，先挑最痛的那一处试。', en: 'A few spots would clearly benefit. Start with the one that hurts most.' },
  },
  {
    key: 'later', min: 30,
    title: { zh: '先收藏，以后用', en: 'Bookmark it' },
    line: { zh: '现在就能帮上一些忙；等 agent 多了、要记的东西多了，收益会更明显。', en: 'It can help a bit today, and more once the agents multiply and there is more to remember.' },
  },
  {
    key: 'fine', min: 0,
    title: { zh: '你的 agent 过得挺好', en: 'Your agents are doing fine' },
    line: { zh: '说实话，你暂时用不上 OpenViking。', en: "Honestly, you don't need OpenViking yet." },
  },
];

/* Personas. `no` is the field-guide numeral. Resolution rules live in scoring.js. */
export const PERSONAS = {
  goldfish: {
    no: 'I',
    name: { zh: '金鱼饲养员', en: 'The Goldfish Keeper' },
    motto: { zh: '「我就是它的外接硬盘。」', en: '“I am its external hard drive.”' },
    symptom: {
      zh: '你的 agent 能力很强，就是记不住事。每次开工，你都要先花五分钟帮它想起自己是谁。',
      en: 'Your agent is brilliant and remembers nothing. Every session opens with five minutes of reminding it who it is.',
    },
    pain: { zh: '每次会话都要重讲一遍背景', en: 'Every session needs the background re-explained' },
  },
  archaeologist: {
    no: 'II',
    name: { zh: 'AGENTS.md 考古学家', en: 'The AGENTS.md Archaeologist' },
    motto: { zh: '「第 300 行往下，是上古时期定的规矩。」', en: '“Below line 300 lie the laws of a lost civilization.”' },
    symptom: {
      zh: '你把踩过的坑都写进了一个文件。现在它长到 agent 读不完，你也不敢删。',
      en: "Every lesson went into one file. Now it's too long for the agent to read and too sacred for you to prune.",
    },
    pain: { zh: '记忆全靠一个越来越长的 AGENTS.md 维持', en: 'Memory lives in an ever-growing AGENTS.md' },
  },
  plumber: {
    no: 'III',
    name: { zh: 'RAG 管道工', en: 'The RAG Plumber' },
    motto: { zh: '「召回率很高，召回的东西不太对。」', en: '“Recall is excellent. What it recalls is not.”' },
    symptom: {
      zh: '切块大小、embedding、top-k、rerank 都调过一遍。改一下切块大小，三个下游指标跟着一起变。',
      en: 'Chunk size, embeddings, top-k, rerankers: all tuned. Change one chunk size and three downstream metrics move.',
    },
    pain: { zh: '依赖一套难以维护的 RAG 管线', en: 'We depend on RAG pipelines that are hard to maintain' },
  },
  librarian: {
    no: 'IV',
    name: { zh: '超载图书管理员', en: 'The Overbooked Librarian' },
    motto: { zh: '「东西都在，就是谁也找不到。」', en: '“Everything is here. Nobody can find any of it.”' },
    symptom: {
      zh: '知识散在 wiki、仓库和群聊里，多到没人看完过。agent 只能看到你恰好贴给它的那一小块。',
      en: 'Knowledge is scattered across wikis, repos and chat threads, more than anyone has read. The agent sees only the slice you happened to paste.',
    },
    pain: { zh: '资料散落各处，agent 找不到', en: 'Knowledge is scattered and agents cannot find it' },
  },
  bus: {
    no: 'V',
    name: { zh: '人肉消息总线', en: 'The Human Message Bus' },
    motto: { zh: '「Ctrl+C、Ctrl+V，这就是我的编排框架。」', en: '“Ctrl+C, Ctrl+V: my orchestration framework.”' },
    symptom: {
      zh: '几个 agent 互不相通。你负责把 A 的结论搬给 B，再把 B 的疑问搬回 A。',
      en: "Your agents don't talk to each other, so you carry A's conclusions to B and B's questions back to A.",
    },
    pain: { zh: 'agent 之间靠人工复制粘贴交接', en: 'Agents hand work over by manual copy and paste' },
  },
  zookeeper: {
    no: 'VI',
    name: { zh: 'Agent 动物园园长', en: 'The Agent Zookeeper' },
    motto: { zh: '「单个都很聪明，凑一块就打架。」', en: '“Each one is clever. Put them together and they fight.”' },
    symptom: {
      zh: '多个 agent、多个人，各记各的。昨天 A 定下的方案，今天 B 理直气壮地推翻。',
      en: 'Many agents, many people, separate notes. What agent A settled yesterday, agent B confidently overturns today.',
    },
    pain: { zh: '多个 agent 各记各的，结论互相冲突', en: 'Agents keep separate, conflicting memories' },
  },
  landlord: {
    no: 'VII',
    name: { zh: '多租户房东', en: 'The Multi-Tenant Landlord' },
    motto: { zh: '「每位租客的记忆，都得锁在自己屋里。」', en: '“Every tenant’s memories stay behind their own door.”' },
    symptom: {
      zh: '你的 agent 服务很多用户，你最怕 A 用户的记忆出现在 B 用户的对话里。',
      en: "Your agent serves many users, and your recurring nightmare is user A's memory showing up in user B's chat.",
    },
    pain: { zh: '需要按用户严格隔离记忆', en: 'Memories must be strictly isolated per user' },
  },
  regular: {
    no: 'VIII',
    name: { zh: '合规会议常驻嘉宾', en: 'The Compliance Meeting Regular' },
    motto: { zh: '「这个问题我们会后对齐。」', en: '“Let’s take this offline. Literally.”' },
    symptom: {
      zh: '架构图还没讲完，数据驻留和审计已经排进了下周的会。日历里有个每周例会，叫“数据出境评审”。',
      en: 'Before the architecture slide is done, data residency and audit are on next week’s agenda. There is a weekly meeting called “Data Egress Review.”',
    },
    pain: { zh: '受数据驻留和审计要求约束', en: 'We are bound by data-residency and audit rules' },
  },
  tinkerer: {
    no: 'IX',
    name: { zh: '自建派极客', en: 'The Homelab Tinkerer' },
    motto: { zh: '「能自己搭的绝不花钱。花时间不算钱。」', en: '“If I can build it, I won’t buy it. My time is free, apparently.”' },
    symptom: {
      zh: '向量库、摘要脚本、定时任务，你已经手搓了半个记忆系统。仓库里还有个目录叫 memory_v3_final。',
      en: 'Vector store, summarizer, cron jobs: you have hand-built half a memory system. There is a folder called memory_v3_final.',
    },
    pain: { zh: '依赖自建的记忆脚本', en: 'We depend on home-grown memory scripts' },
  },
  traveler: {
    no: 'X',
    name: { zh: '轻装旅行者', en: 'The Light Traveler' },
    motto: { zh: '「问完就走，从不回头。」', en: '“Ask, answer, gone. No baggage.”' },
    symptom: {
      zh: '你的任务一次就做完，或者主要在网页聊天框里用 AI。agent 记不记得昨天，暂时不影响今天。',
      en: "Your tasks finish in one go, or you mostly use AI in a web chat box. Whether an agent remembers yesterday doesn't change today, for now.",
    },
    pain: { zh: '目前没有长期记忆的需求', en: 'No long-term memory needs yet' },
  },
};

export const PERSONA_ORDER = ['goldfish', 'archaeologist', 'plumber', 'librarian', 'bus', 'zookeeper', 'landlord', 'regular', 'tinkerer', 'traveler'];

/* Edition order is also the order of the comparison cards. */
export const FORM_ORDER = ['oss', 'personal', 'enterprise', 'private'];
