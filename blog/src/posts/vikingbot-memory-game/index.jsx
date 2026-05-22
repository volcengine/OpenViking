import React from 'react';
import {
  A, Article, Callout, Figure, H2, H3, InlineCode, Lead, Li, P, Pre, Quote, Table, Ul,
} from '../../blog-components';

const SOURCE_URL = 'https://my.oschina.net/u/6210722/blog/19667020';
const COVER_URL = 'https://oscimg.oschina.net//AiCreationDetail/up-22c7b3ef3d4fefd88e78bd3cdfd1995b.png';
const BOT_README = 'https://github.com/volcengine/OpenViking/blob/main/bot/README_CN.md';
const WEREWOLF_DEMO = 'https://github.com/volcengine/OpenViking/blob/main/bot/demo/werewolf/README.md';
const LLM_PATH = '/post/vikingbot-memory-game/llm.txt';

const MEMORY_SAMPLE = `<memory type="summary">
  <uri>viking://user/player_3/memories/profile.md</uri>
  <content>3 号玩家经常强势上警，拿猎人时倾向于早亮身份。</content>
</memory>

<memory type="event">
  <uri>viking://user/player_5/memories/events/2026/04/13/悍跳预言家.md</uri>
  <content>5 号曾在警长竞选中悍跳预言家，并被多名玩家质疑。</content>
</memory>`;

const INSTALL_SNIPPET = `pip install "openviking[bot]"
openviking-server --with-bot
ov chat`;

const WerewolfMemoryPost = () => (
  <Article>
    <Lead>
      多 Agent 协作的问题，不只是让几个模型同时说话，而是让它们在多轮任务里记住彼此：谁说过什么、谁骗过谁、哪种策略曾经奏效。VikingBot 的狼人杀实验把这个问题放进了一个足够具体的场景。
    </Lead>

    <Callout type="info" title="参考来源">
      <P>
        本文基于字节跳动 Viking 团队发布在 OSCHINA 的实验文章重新整理，保留核心实验结论，并改写成 OpenViking Blog 的技术说明版本。原文见 <A href={SOURCE_URL}>OSCHINA 参考文章</A>。
      </P>
    </Callout>

    <H2 id="why-memory">为什么要给多 Agent 一层记忆</H2>

    <P>
      如果每个 Agent 只看当前对话窗口，它就很难形成稳定策略。上一局谁悍跳过预言家、谁习惯强势上警、谁曾经因为过早亮身份被集火，这些信息如果不能沉淀，下一轮协作又会回到零。
    </P>

    <P>
      OpenViking 在这里扮演的不是普通日志库，而是一层可检索、可追溯、可更新的上下文底座。它把对局中的发言、投票、工具调用和复盘结果，整理成 Agent 能够继续使用的记忆。
    </P>

    <Quote cite="实验问题">
      当 Agent 能跨局记住玩家风格和历史事件，它会不会开始利用这些记忆做更像人的推理、伪装和反制？
    </Quote>

    <H2 id="experiment">实验怎么设计</H2>

    <P>
      参考实验使用 6 个 VikingBot 进行狼人杀对战。1、2、3 号 Bot 接入 OpenViking，能够跨局保留长期记忆；4、5、6 号 Bot 只拥有当前对局的短期上下文。
    </P>

    <Table
      headers={['组别', '玩家', '记忆能力']}
      rows={[
        ['实验组', '1、2、3 号 VikingBot', '接入 OpenViking，保留跨局长期记忆'],
        ['对照组', '4、5、6 号 VikingBot', '只使用当前对局上下文'],
      ]}
    />

    <Figure
      src={COVER_URL}
      alt="狼人杀实验中的玩家 Bot 身份初始化"
      caption="上帝 Bot 给玩家 Bot 分配身份，并通过 GAME.md 传递当前局的私有信息。"
      credit="Source: OSCHINA reference article"
      frame="card"
      size="lg"
    />

    <H2 id="what-changed">记忆真正改变了什么</H2>

    <H3>1. 记住风格，而不只是记住原话</H3>
    <P>
      对局结束后，OpenViking 不只是保存完整聊天记录，还会沉淀更适合复用的事实：某个玩家是否常悍跳、是否喜欢早亮神职、是否习惯用强势发言带节奏。下一局开始时，这些画像会重新进入 Agent 上下文。
    </P>

    <H3>2. 用历史事件支持推理</H3>
    <P>
      有记忆的 Bot 可以把当前发言和过去事件对照起来。比如某个玩家这局再次跳身份，Agent 可以检索到它之前类似行为的结果，再判断这是稳定风格、真实身份暗示，还是刻意伪装。
    </P>

    <Pre lang="xml" filename="retrieved-memory.xml">{MEMORY_SAMPLE}</Pre>

    <H3>3. 策略会被复盘放大</H3>
    <P>
      参考实验中，有记忆的 Bot 会逐渐学会隐藏真实身份、利用他人的固定打法、甚至用上一局的事件为这一局的发言背书。这说明长期记忆在多 Agent 场景里会变成策略资产，而不是简单的聊天历史。
    </P>

    <H2 id="how-it-works">OpenViking 在底层做了什么</H2>

    <P>
      VikingBot 和 OpenViking 的组合可以拆成两条链路：写入链路负责把高价值对话沉淀成结构化记忆；检索链路负责在下一轮任务开始前，把最相关的记忆放回上下文。
    </P>

    <Ul>
      <Li><InlineCode>viking://</InlineCode> 目录协议让记忆像文件一样可列举、可读取、可搜索。</Li>
      <Li>L0 / L1 / L2 三层内容结构先给摘要和线索，再按需读取完整内容，减少 token 浪费。</Li>
      <Li>增量更新机制让 Agent 修改记忆片段，而不是每次重写整份画像。</Li>
      <Li>语义化文件名本身就是索引，例如把一次“悍跳预言家”记录到对应事件文件里。</Li>
    </Ul>

    <P>
      这也是为什么“记忆”不能只理解成向量检索。多 Agent 系统需要的是可管理的上下文资源：它有目录、有来源、有权限边界，也能被人审计和删除。
    </P>

    <H2 id="evaluation">从游戏到长程对话评测</H2>

    <P>
      参考文章还给出了 LoCoMo 长程对话评测结果：原生 OpenClaw 的准确率约为 24%，接入 OpenViking Plugin 2.0 后约为 80%，VikingBot 深度集成后同样约为 80%，但 token 成本从约 3500 万进一步降到约 2100 万。
    </P>

    <Table
      headers={['方案', '准确率', 'Token 成本']}
      rows={[
        ['原生 OpenClaw', '约 24%', '约 3.9 亿'],
        ['OpenClaw + OpenViking Plugin 2.0', '约 80%', '约 3500 万'],
        ['VikingBot 深度集成', '约 80%', '约 2100 万'],
      ]}
    />

    <P>
      这里的关键不是“记得越多越好”，而是把检索粒度、写回时机和内容层级设计好，让模型只拿到这一步真正需要的上下文。
    </P>

    <H2 id="production">如果放到真实业务里</H2>

    <P>
      狼人杀只是一个容易观察群体行为的实验场。放到真实业务里，同样的问题会出现在客服、研发助手、审批 Bot、法务助手等场景：Agent 需要记住用户偏好、项目背景、工具使用经验，也需要在团队边界内共享知识。
    </P>

    <P>
      VikingBot 支持通过 Bot Server Gateway 接入多个渠道，也支持按不同粒度隔离工作空间：所有渠道共享一个工作空间、按渠道隔离，或精确到渠道加会话隔离。配合 OpenViking 的 account / user / agent 多租户边界，可以把“记忆共享”和“数据隔离”同时做成平台能力。
    </P>

    <H2 id="try-it">如何上手</H2>

    <P>
      最小启动路径是安装 OpenViking Bot 扩展，启动带 Bot 的 OpenViking Server，然后进入命令行对话：
    </P>

    <Pre lang="bash" filename="terminal">{INSTALL_SNIPPET}</Pre>

    <P>
      更完整的配置可以看 <A href={BOT_README}>VikingBot README</A>。如果想复现实验中的多 Agent 狼人杀，可以看 <A href={WEREWOLF_DEMO}>werewolf demo</A>。
    </P>

    <Callout type="note" title="一句话总结">
      <P>
        VikingBot 展示的是 OpenViking 的一个核心价值：把 Agent 的临时上下文升级成可追溯、可检索、可隔离、能持续更新的长期记忆层。
      </P>
    </Callout>
  </Article>
);

export default {
  id: 'vikingbot-memory-game',
  Component: WerewolfMemoryPost,
  meta: {
    title: { zh: 'VikingBot：当 Agent 记忆开始影响策略' },
    description: {
      zh: '从狼人杀多 Agent 实验看 OpenViking 如何把聊天历史沉淀成可追溯、可检索、可复用的长期记忆。',
    },
    cover: COVER_URL,
    publishedAt: '2026-05-22',
    updatedAt: '2026-05-22',
    readingTime: { zh: 8 },
    category: { zh: 'Agent 记忆' },
    tags: ['openviking', 'vikingbot', 'agent-memory', 'multi-agent'],
    languages: ['zh'],
    llmPath: LLM_PATH,
    sourceUrl: SOURCE_URL,
    sourceTitle: '局中局！给 Agent 装上 OpenViking，它们竟然学会了“记仇”和“伪装”？',
    sourceUpdatedAt: '2026-05-13',
    authors: [{ name: 'ByteDance Viking Team', role: { zh: 'OpenViking 团队' } }],
  },
};
