import React from 'react';
import {
  Article, Lead, P, H2, Figure, Table,
  Strong, Em, Ul, Ol, Li,
} from '../../blog-components';

const LLM_PATH = '/post/agent-as-a-colleague/llm.txt';
const COVER = '/assets/covers/agent-as-a-colleague.png';
const FIGURE_ROOT = '/post/agent-as-a-colleague/images';

function EnglishArticle() {
  return (
    <Article className="agent-colleague-article">
      <Lead>We put an AI agent into a 20-person project channel. The first thing it had to learn wasn&apos;t how to answer. It was when to stay quiet.</Lead>

      <P>We learned this the hard way. It was a standard Tuesday morning. Maya said the demo got pushed to Friday, Chris flagged a release blocker, and Alex said he was in a client meeting. Not a single message was directed at the agent. Twenty minutes later, someone asked the channel: &quot;What changed this morning?&quot;</P>
      <P>The agent received every single message, but it completely blanked. It was in the group, sure, but it was watching the play from behind soundproof glass. The messages hit the logs, but they didn&apos;t translate into usable context.</P>
      <P>During the post-mortem, we had to admit a hard truth: &quot;reading&quot; a message and &quot;catching&quot; the context are two entirely different things. An agent living in a team channel can&apos;t just wake up when someone @-mentions it. Nor can it eagerly reply to every stray thought. It needs to know when to listen, what&apos;s worth remembering, and—crucially—who exactly a memory belongs to.</P>

      <Figure
        src={`${FIGURE_ROOT}/figure-01-listen-before-speaking-en.png`}
        alt="A work-chat sequence in which the agent attributes updates about the demo, release blocker, and client meeting to Maya, Chris, and Alex"
        caption="Silence no longer means it wasn't paying attention."
        size="lg"
        frame="frame"
      />

      <H2>Group Chats Aren&apos;t Just Giant DMs</H2>

      <P>We started with the classic assistant model: one user, one agent, one giant memory bucket. Chat history, preferences, half-finished tasks—we dumped it all in.</P>
      <P>The second we put it in a group chat, this model broke. The exact same sentence can have entirely different ownership:</P>

      <Table
        headers={['What was said in chat', 'Who it actually belongs to']}
        rows={[
          ['"Pause this feature until Risk confirms the schedule."', 'A team-wide decision.'],
          ['"Just give me the conclusion, owner, and deadline."', 'A specific person\'s communication preference.'],
          ['"Check the deployment diff before shipping—that saved us last time."', 'The agent\'s own learned execution experience.'],
        ]}
      />

      <P>If you smash all of this into a single bucket, one person&apos;s quirk becomes a team rule. A temporary call becomes a permanent fact. The real danger isn&apos;t that the agent doesn&apos;t know the answer—it&apos;s that it answers with absolute confidence, but gets the subject completely wrong.</P>
      <P>OpenViking&apos;s User/Peer Mode gave us a much better starting point. The resident agent operates in a user space, while everyone in the chat maps to an independent peer tied to their stable platform identity. Human facts, preferences, and events get written to the corresponding peer. The agent itself is treated as a first-class citizen—it gets its own dedicated memory space to store its identity and learned execution experience. Docs, runbooks, and project materials remain shared resources.</P>
      <P>We didn&apos;t do this to silo memories away from each other. Our rule is actually the opposite: <strong className="agent-colleague-keyline">Write narrow, read wide.</strong> When writing, pin it to the right owner. When recalling, the agent can scour authorized team memories and resources for the answer—but the results must carry enough provenance. It needs to know if this is Chris&apos;s preference, a risk Maya flagged, or a confirmed team decision.</P>
      <P>Memories without ownership eventually devolve into &quot;I heard this somewhere, but I don&apos;t know who said it.&quot; In a group chat, that&apos;s basically just confidently spreading gossip.</P>

      <H2>It Remembered the People, But Still Sounded Like Customer Support</H2>

      <P>Once we sorted out memory ownership, a new problem immediately popped up. A teammate just wanted a quick status update. The agent replied with a three-paragraph consulting report.</P>
      <P>Here&apos;s how people actually talked in this channel:</P>

      <Ul>
        <Li>&quot;Stop calling it an edge case. Did we figure out why only new accounts are failing?&quot;</Li>
        <Li>&quot;Did anyone actually test the rollback? Just mentioning it in the PR doesn&apos;t count.&quot;</Li>
        <Li>&quot;p50 doubled. Even if the error rate looks fine, dig into it.&quot;</Li>
      </Ul>

      <P>Short, direct, sometimes a little sharp. Dropping a watertight, overly polite essay into that mix makes the agent sound like an outsider who only reads Jira tickets and never attends standups.</P>
      <P>We ended up separating understanding others from maintaining the agent&apos;s own identity. Every peer gets a profile.md to track their background, communication quirks, and work habits. The agent gets its own identity.md to answer &ldquo;Who am I?&rdquo; and a soul.md for its values, boundaries, and working style. It can give Maya the conclusion and deadline first, and hand Chris the logs and failure chain first—without having to invent a new personality for every person it talks to.</P>
      <P>These files aren&apos;t magic prompts. They work because they decouple two things we usually conflate: <Strong>Relationships adapt. Personalities persist.</Strong></P>
      <P>Next, we broke the agent&apos;s group chat behavior into three distinct actions:</P>

      <Ol>
        <Li><strong className="agent-colleague-keyline">Observe:</strong> Ingest the conversation. Keep the speaker, channel, thread, and timestamp intact.</Li>
        <Li><strong className="agent-colleague-keyline">Remember:</strong> Write durable human evidence to the right peer, keep the agent&apos;s own execution experience in its dedicated memory space, and leave team materials as shared resources.</Li>
        <Li><strong className="agent-colleague-keyline">Engage:</strong> Decide if speaking up right now is actually helpful.</Li>
      </Ol>

      <P>The first two steps can happen constantly. The third step might never happen. Silence no longer means it wasn&apos;t paying attention.</P>

      <H2>Memory Actually Runs on Two Clocks</H2>

      <P>A teammate says, &quot;The client pushed the meeting to 4 PM.&quot; Thirty seconds later, someone else asks about it. The agent can&apos;t wait for a background memory extraction job to finish. It needs immediate access to recent, attributed chat context.</P>
      <P>But if someone asks next month, &quot;Does this client always reschedule last minute?&quot; replaying a massive chunk of raw chat logs is useless. What you need then is durable memory—patterns distilled from multiple conversations over time.</P>
      <P>So we kept two parallel, non-interchangeable tracks:</P>

      <Ul>
        <Li><strong className="agent-colleague-keyline">The Now:</strong> A bounded window of recent dialogue to maintain immediate continuity.</Li>
        <Li><strong className="agent-colleague-keyline">The Later:</strong> Sessions committed to OpenViking, which handle the extraction, organization, and retrieval of durable memory.</Li>
      </Ul>

      <P>The real-time window handles &quot;what just happened.&quot; OpenViking handles &quot;what&apos;s worth remembering later.&quot; Mashing them into a single mechanism sounds elegant, but in practice, it either drags down real-time chat with extraction latency, or degrades long-term memory into an infinitely scrolling chat replay.</P>

      <H2>Our First Attribution Leak</H2>

      <P>These principles sounded a bit abstract—until we hit our first attribution leak.</P>
      <P>A teammate&apos;s personal preference got written to a shared location instead of their peer space. The agent could still search and find it, so on the surface, it didn&apos;t &quot;forget.&quot; The real issue? It could no longer reliably tell us <Em>whose</Em> preference it was. The answer was fluent, but the ownership was busted.</P>
      <P>The root cause wasn&apos;t that OpenViking lacked peers. It was that the identity pipeline on the agent side was incomplete. The gateway knew the speaker&apos;s platform user ID, but that identity didn&apos;t reliably translate into a peer ID, nor did it thread through the entire execution cycle, memory hooks, and write requests.</P>
      <P>After we fixed it, the pipeline became dead simple:</P>

      <Ol>
        <Li>The gateway identifies the speaker using their stable platform ID.</Li>
        <Li>The runtime builds a structured identity context for the current turn.</Li>
        <Li>The same identity travels with every memory read and write.</Li>
        <Li>Human evidence gets written with a specific peer ID; the agent&apos;s own operational evidence stays in its dedicated memory space.</Li>
        <Li>Recall preserves the source, so the agent can actually explain <Em>who</Em> said <Em>what</Em>.</Li>
      </Ol>

      <P>Break this chain anywhere, and retrieval might still &quot;look fine&quot;—but the memory has quietly lost its owner.</P>

      <H2>Three Moments It Started Feeling Like a Colleague</H2>

      <P>I eventually realized that progress didn&apos;t show up in the benchmarks first. It showed up in tiny, quiet moments.</P>
      <P>One time, the team was arguing over a release window. The agent didn&apos;t interrupt once. When someone finally asked, &quot;What are we actually blocked on right now?&quot; it didn&apos;t spit out a sanitized pros-and-cons list. It reconstructed the actual friction: Maya was worried about the rollback window, Chris refused to accept conclusions without load-testing data, and the owner hadn&apos;t confirmed the on-call rotation. More importantly, it nailed exactly <Em>who</Em> raised which concern.</P>
      <P>Another moment happened between a DM and a project channel. A coworker had mentioned in a DM: for urgent issues, just give me the conclusion and next steps—skip the background. Later, when production actually caught fire in the group chat, the agent led with the conclusion, owner, and deadline, then backfilled the evidence. It didn&apos;t broadcast the private DM to the group; it just used that relational memory to adjust its delivery. That kind of personalization doesn&apos;t feel like &quot;I remember you prefer short replies.&quot; It feels like someone who actually knows how to work with you.</P>
      <P>The most obvious one was when it botched the weekly report. Date ranges, formatting, publishing steps—all wrong. A teammate corrected it line by line. The following week, it didn&apos;t just regurgitate an apology. It actually followed the right process.</P>
      <P>This was OpenViking&apos;s Agent Evolution capability at work. Once we flipped it on server-side, OpenViking started extracting trajectories and experiences from agent sessions that involved tool calls and decision-making. Trajectories save reusable operational contracts; experiences save more abstract execution lessons. It doesn&apos;t guarantee every correction magically turns into a flawless rule, but it finally gives &quot;what we did right this time&quot; and &quot;what to avoid next time&quot; a better home than a raw chat log.</P>

      <Figure
        src={`${FIGURE_ROOT}/figure-02-agent-evolution.png`}
        alt="A watercolor sequence showing an AI agent making a mistake, receiving human feedback, distilling the lesson into durable memory, and completing the task correctly next time"
        caption="From correction to reusable execution experience."
        size="lg"
        frame="frame"
      />

      <P>Chat logs can store apologies. But only reusable trajectories and experiences can store actual, valuable lessons.</P>

      <H2>Wrapping Up</H2>

      <P>Memory alone doesn&apos;t make a colleague. You need the right tools, sane permissions, low latency, and—crucially—the ability to fail quietly and step back.</P>
      <P>What OpenViking changed was continuity. It means an agent living in a multiplayer environment doesn&apos;t have to flatten everyone into one blurry &quot;User.&quot; It doesn&apos;t have to treat every chat log as the exact same type of memory. Humans get their own peer spaces. The agent gets its own dedicated space for its identity, soul, and execution experience. Team materials stay shared. Recent context keeps it up to speed right now, and durable memory ensures it actually does better next time.</P>
      <P>We ended up with four dead-simple rules:</P>

      <Ol>
        <Li>Put the agent where the actual work happens.</Li>
        <Li>Let it listen first, then decide if it should speak.</Li>
        <Li>Remember <Em>who</Em> said what, not just <Em>what</Em> was said.</Li>
        <Li>Let it accumulate experience from its own work.</Li>
      </Ol>

      <P>Nail these, and when the agent finally speaks up, it won&apos;t sound like a script desperately parsing chat logs. It&apos;ll sound like a colleague who was in the room the whole time—and who will actually remember it tomorrow.</P>
    </Article>
  );
}

function ChineseArticle() {
  return (
    <Article className="agent-colleague-article">
      <Lead>我们把一个 Agent 拉进了二十多人的项目群。它要学会的第一件事，不是怎么回答，而是什么时候该保持沉默。</Lead>

      <P>本来指望它能大展身手，结果第一次翻车就让我意识到一件事：<Strong>做一个单机版的“私人助手”，和做一个群聊里的“团队同事”，完全是两套逻辑。</Strong></P>
      <P>而要完成从助手到同事的转变，Agent 进群后要学会的第一件事，其实是适时沉默。</P>
      <P>那是一个普通的上午，Maya 说 demo 改到周五，Chris 报了 release blocker，Alex 说他正在和客户开会。这些话大家都在互相 catch up，没有一句是专门发给 Agent 的。结果二十分钟后，有人问了一句：“今天上午有什么变动？”</P>
      <P>Agent 傻眼了。它每条消息都收到了，但根本答不上来。它确实在群里，但更像是一个隔着玻璃看戏的观众。消息进了它的日志，但并没有变成它此刻能用的 context。</P>
      <P>事后复盘的时候，我才意识到，<Strong>“读过”和“接住”完全是两回事。</Strong> 一个真正在工作群里生存的 Agent，不能只在被 @ 的时候才醒过来，也不能看到什么都抢着回答。它得知道什么时候该听，哪些事情值得记下来，以及最关键的——这句话到底是谁说的。</P>

      <Figure
        src={`${FIGURE_ROOT}/figure-01-listen-before-speaking-zh.png`}
        alt="Agent 记住 Maya、Chris 和 Alex 各自说过什么，再带着身份归属回答群聊里的问题"
        caption="沉默不再等于没看见。"
        size="lg"
        frame="frame"
      />

      <H2>群聊不是大号的私聊</H2>

      <P>我们一开始想得很简单，沿用了最熟悉的单机助手模型：一个用户，一个 Agent，一个记忆桶。历史记录、偏好、没做完的事情，统统往这个桶里扔。</P>
      <P>但是一放进群聊，这个模型立刻就崩溃了。因为在群里，同样是一句话，背后的归属完全不一样：</P>

      <Table
        headers={['聊天里的话', '更合理的归属']}
        rows={[
          ['“这个需求先暂停，等风控确认再排期”', '团队正在执行的决定'],
          ['“给我结论、owner 和 deadline 就行”', '某个同事个人的沟通偏好'],
          ['“发布前先核对部署 diff，上次就是这里救了我们”', 'Agent 自己从执行中学到的经验'],
        ]}
      />

      <P>如果把这些全都塞进同一个桶里，某个人的个人习惯就会被当成团队的规则，一次临时的决定会被当成长期事实。最可怕的还不是 Agent 不知道，而是它回答得非常笃定，却把主语给弄错了。</P>
      <P>后来我们换了 OpenViking 的 User / Peer Mode。群里的每个人都有独立的 peer 身份。人的事实和偏好写进对应的 peer 里；Agent 自己的经验留在 self 里；而文档和项目材料作为共享资源。</P>
      <P>我们的原则变成了：<strong className="agent-colleague-keyline">写得窄，读得宽。</strong></P>
      <P>记东西的时候，先把“主人”找对。等需要回答的时候，Agent 可以在授权的团队记忆里找答案，但必须带着来源——它得知道这是 Chris 的偏好，还是 Maya 提过的风险。没有归属的记忆，在群聊里跟自信地传八卦没什么区别。</P>

      <H2>记住了人，但开口还是像个客服</H2>

      <P>把记忆的归属理顺之后，另一个问题又来了：队友只想要一句简单的进展，Agent 却非要给你回三段详尽的“咨询报告”。</P>
      <P>真实的群聊画风往往是短平快，甚至带点刺的：</P>

      <Ul>
        <Li>“别动不动就说偶发，为什么只有新账号挂查清楚了吗？”</Li>
        <Li>“回滚到底有人真的测过吗？PR 里提了一句不算。”</Li>
        <Li>“p50翻倍了，就算err正常也要再排查下。”</Li>
      </Ul>

      <P>这时候，一段滴水不漏、客客气气的长回复插进来，就像一个只读过SOP工单、从来没参加过团队例会的外包客服。</P>
      <P>我后来发现，其实需要把“了解别人”和“保持自己”拆开。</P>
      <P>每个同事的 profile.md 记录他们的背景和沟通偏好。而 Agent 自己的 identity.md 负责回答“我是谁？”，soul.md 负责价值观和做事风格。它可以对 Maya 直接给结论和 deadline，对 Chris 先给日志，但它不需要每见一个人就换一套人格。<Strong>关系可以适应，但人格需要连续。</Strong></P>
      <P>后来，我们把 Agent 在群里的动作拆成了三件事：</P>

      <Ol>
        <Li><strong className="agent-colleague-keyline">观察：</strong>先把对话接住。谁说的、在哪个群、哪个 thread、什么时候说的，都别丢。</Li>
        <Li><strong className="agent-colleague-keyline">记忆：</strong>人的事实和偏好写回对应的 peer；Agent 自己跑通的流程和踩过的坑留给自己；团队资料继续作为共享资源。</Li>
        <Li><strong className="agent-colleague-keyline">参与：</strong>最后才判断，这时候开口到底有没有帮助。</Li>
      </Ol>

      <P>前两件事可以一直默默发生，第三件事可以一直不发生。沉默不再等于没看见。</P>

      <H2>记忆的两只钟</H2>

      <P>在群里，Agent 的记忆其实是跑在两只钟上的。</P>
      <P>队友刚说“客户把会改到四点”，半分钟后有人问起，Agent 必须马上能答出来。这时候需要的是近期的、实时的对话窗口。</P>
      <P>但如果下个月有人问：“这个客户是不是经常临时改时间？” 这时候就需要从多次对话里沉淀出来的持久记忆。</P>

      <P>所以，我们把它拆成了两条路：</P>

      <Ul>
        <Li><strong className="agent-colleague-keyline">当下：</strong>保留一段有边界的近期对话，用来接住刚刚发生的事。</Li>
        <Li><strong className="agent-colleague-keyline">以后：</strong>把 session 提交给 OpenViking，由它提取、整理和检索值得长期留下的记忆。</Li>
      </Ul>

      <P>“刚刚发生了什么”交给实时窗口，“以后还值得记住什么”交给 OpenViking。把这两件事揉在一起，要么让实时聊天背上记忆提取的延迟，要么让长期记忆退化成一条永远翻不完的聊天记录。</P>

      <H2>第一次归属泄露</H2>

      <P>在这个过程中，我们遇到过一次很有意思的 bug，叫 attribution leak（归属泄露）。一个队友的个人偏好被错误地写进了共享位置。Agent 表面上没失忆，回答依然流畅，但它已经不知道这句话是谁说的了。所有权错了。</P>
      <P>问题不在 OpenViking 没有 peer，而是 Agent 侧的身份链路没有接完整。网关其实知道说话人的平台 ID，但这个 ID 没有稳定地变成 peer ID，也没有一路跟到后面的记忆读写里。</P>
      <P>修完之后，整条链路反而很简单：</P>

      <Ol>
        <Li>网关先用稳定的平台 ID 认出说话的人。</Li>
        <Li>运行时把这份身份挂到当前这一轮上。</Li>
        <Li>之后每一次记忆读写，都沿用同一个身份。</Li>
        <Li>人的内容写进对应的 peer；Agent 自己的执行经验留在自己的空间。</Li>
        <Li>召回时保留来源，Agent 才能说清楚是谁说了什么。</Li>
      </Ol>

      <P>这让我意识到，<Strong>一旦身份链路断掉，检索出来的东西“看起来能用”，但记忆已经悄悄失去了它的主人。</Strong></P>

      <H2>真正像同事的瞬间</H2>

      <P>其实，Agent 的进步往往不是体现在 benchmark 的跑分上，而是出现在一些很小的瞬间里。</P>
      <P>有一次，团队在群里争论发布窗口。Agent 全程没插话。等有人问“现在到底卡在哪里”时，它没有像以前那样生成一份四平八稳的优缺点清单，而是真实地还原了分歧：Maya 担心回滚窗口，Chris 不接受没有压测数据的结论，owner 还没确认值班。更重要的是，它清楚地说出了每个顾虑是谁提出的。</P>
      <P>还有一次，某位同事在私聊里跟 Agent 说过，紧急问题直接给结论，不要铺垫背景。后来项目群里真的出了事，Agent @他的时候，直接给了结论、owner 和 deadline。它没有把私聊内容公之于众，只是用这段关系记忆调整了表达方式。那一刻，它不像是在执行“我记得你喜欢简短回复”的代码，更像是一个已经知道怎么跟你协作的活生生的人。</P>
      <P>最明显的一次是写周报。它把日期和格式全弄错了，被队友在群里逐项纠正。到了下一周，它没有复读那段道歉，而是直接走对了流程。</P>
      <P>这是 OpenViking 的 Agent Evolution 在起作用。trajectory 记下可以复用的执行过程和约束，experience 再从里面提炼更抽象的做事经验。它当然不会把每次纠正都神奇地变成一条完美规则，但至少，“这次为什么做对了”和“下次别再踩什么坑”有了比原始聊天记录更合适的去处。</P>

      <Figure
        src={`${FIGURE_ROOT}/figure-02-agent-evolution.png`}
        alt="一组水彩画面：Agent 做错任务、接受同事反馈、把教训沉淀成持久记忆，并在下一次正确完成任务"
        caption="把一次纠正，沉淀成下一次可复用的经验。"
        size="lg"
        frame="frame"
      />

      <P><Strong>对话记录只能留下道歉，但可复用的经验，才能留下真正值钱的教训。</Strong></P>

      <H2>最后</H2>

      <P>现在很多企业都在推进 Agent 的使用，但绝大部分都还是单机聊天的模式。当我们真正把 Agent 放到多人协作的环境里，会发现光靠记忆，是造不出一个同事的。</P>
      <P>OpenViking 真正补上的，是连续性。它让 Agent 不必把所有人压扁成一个模糊的“用户”，也不必把所有聊天都当成同一种记忆。人有各自的 peer，Agent 有自己的身份和执行经验，团队资料继续共享；近期对话负责接住当下，持久记忆负责让它下次做得更好。</P>
      <P>趁手的工具、合理的权限、够低的延迟，以及失败时安静退场的能力，一样都不能少。这倒逼我们去思考，一个真正融入团队的 Agent 到底需要什么？</P>

      <Ul>
        <Li>把 Agent 放在工作真正发生的地方</Li>
        <Li>让它先听，再决定要不要说</Li>
        <Li>记住是谁说了什么，而不只是记住说过什么</Li>
        <Li>让它从自己的工作里积累经验</Li>
      </Ul>

      <P>从一个“只对一个人负责的私人助手”，进化成一个“在群里跟大家协作的团队同事”，绝不是简单地把 bot 拉进群就能解决的。它需要在底层重构记忆和身份的逻辑——而这，也正是 OpenViking Peer Mode 想要解决的核心问题。</P>
      <P>只有做到这些，当 Agent 开口的时候，才不会像一个临时翻完聊天记录的机器，而更像是一个当时就在场、下次也还记得的同事。</P>
    </Article>
  );
}

function AgentAsAColleaguePost({ lang }) {
  return lang === 'zh' ? <ChineseArticle /> : <EnglishArticle />;
}

export default {
  id: 'agent-as-a-colleague',
  Component: AgentAsAColleaguePost,
  meta: {
    title: {
      en: "From Assistant to Colleague: Building Multi-User Agents with OpenViking's Peer Mode",
      zh: '从助手到同事：基于 OpenViking Peer Mode 构建多用户 Agent',
    },
    description: {
      en: 'Standard AI memory breaks in group chats. Learn how OpenViking’s Peer Mode decouples identity from context, turning passive bots into true team colleagues.',
      zh: '传统 AI 记忆在群聊中往往会失效。看 OpenViking Peer Mode 如何解耦身份与上下文，让 Agent 从机器人进化为真正的同事。',
    },
    cover: COVER,
    cardCover: COVER,
    publishedAt: '2026-08-07',
    readingTime: { en: 10, zh: 8 },
    category: { en: 'Agent Memory', zh: 'Agent 记忆' },
    tags: ['openviking', 'agent-memory', 'multi-user', 'agents'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [{ name: 'haozhe', github: 'ehz0ah', role: { en: 'Engineer', zh: '工程师' } }],
  },
};
