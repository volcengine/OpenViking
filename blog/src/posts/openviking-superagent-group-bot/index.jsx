import React from 'react';
import {
  Article, Lead, P, H2, H3, A, Pre, Small,
} from '../../blog-components';
import { Chat, ChatStyles } from './chat.jsx';
import { CHATS } from './chats.js';

const SLUG = 'openviking-superagent-group-bot';
const LLM_PATH = `/post/${SLUG}/llm.txt`;
const COVER = `/assets/covers/${SLUG}.webp`;
const CARD_COVER = `/assets/covers/${SLUG}-card.webp`;

const SUPERAGENT_URL = 'https://github.com/volcengine/OpenViking/tree/feat/vika/superagent';
const INSTALL_URL = 'https://github.com/volcengine/OpenViking/blob/feat/vika/superagent/install.md';
const ONEBOT_URL = 'https://github.com/volcengine/OpenViking/blob/feat/vika/superagent/docs/onebot-connector.md';
const MCP_BRIDGE_URL = 'https://github.com/t0saki/astrbot_plugin_mcp_bridge';
const AGENT_HUB_POST = '/post/agent-swarm-memory/';

const REPEATER_ZH = `
if last_msg == msg:     # 群友正在复读
    if not is_repeated:     # 机器人尚未复读过，开始测试复读
        if random.random() < p:    # 概率测试通过，复读并设flag
            await bot.send(ev, util.filt_message(ev.message))
        else:                      # 概率测试失败，蓄力
            p = 1 - (1 - p) / PROB_A
`;

const REPEATER_EN = `
if last_msg == msg:     # the group is repeating a message
    if not is_repeated:     # the bot hasn't repeated it yet: roll for it
        if random.random() < p:    # roll passed: repeat it and set the flag
            await bot.send(ev, util.filt_message(ev.message))
        else:                      # roll failed: raise the odds for next time
            p = 1 - (1 - p) / PROB_A
`;

const AI_MAIN = `
/**
 * AI核心代码，估值1个亿
 */
public class AiMain {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        String str;
        while (true) {
            str = sc.next();
            str = str.replace("吗", "");
            str = str.replace("?", "!");
            str = str.replace("？", "！");
            System.out.println(str);
        }
    }
}
`;

const AI_MAIN_OUTPUT_ZH = `
在吗?
在!
能听懂汉语吗?
能听懂汉语!
`;

const AI_MAIN_OUTPUT_EN = `
在吗?            (Are you there?)
在!              (Here!)
能听懂汉语吗?     (Can you understand Chinese?)
能听懂汉语!       (Can understand Chinese!)
`;

function ChineseArticle() {
  return (
    <Article>
      <ChatStyles />
      <Lead>上周，室友群偶然聊到了十周年聚会。有群友贴了 GPT 的回答，说它算出我们六个人的“世界中心”是深圳。</Lead>

      <P>正好群里的 Bot“二号机”刚被我升级到最新的 OpenViking SuperAgent，我便 @ 了它，顺便测试一下。接下来几分钟，大家又改了几轮各自的位置，中间夹着不少闲聊，二号机像个真人助理一样一句都没接，直到位置定下来，它才发了一张图，用的就是最后更正过的版本。之后有人直接来了一句“咋和GPT算的不一样”，没有 @ 它，也没有打任何触发命令，它自己接了话：我调了工具，我算的是对的。</P>

      <Chat chat={CHATS.meetup} />

      <P>这个群里有 Bot 已经很多年了，框架和模型都换过不知多少回。它查过战绩，应群友要求骂过人，被骗着写过番茄炒蛋的菜谱。Bot 回复几千次，像个真人第一次。</P>

      <P>图画得怎么样其实不太重要，我觉得它像个真人，主要还是因为那几分钟里它一直没说话。回头看，这些年 Bot 一直在变得更会说话，从只认命令的脚本，到能聊天的 ChatGPT，再到能搜索、能写代码的 Agent，但“这句话该不该说”始终不是它自己决定的。它要有人喊才会出声，被喊了又必须得回，有些框架倒是能让它不被喊也插两句话，不过那也只是配了一个随机回复的概率。</P>

      <P>这篇文章就顺着这条线，先聊聊群聊 Bot 是怎么一步步走到今天的，再讲我们在 <A href={SUPERAGENT_URL}>OpenViking SuperAgent</A> 里是怎么设计的，最后是我业余时间怎么把它接进 QQ 群的。历史部分是我的个人感受，不感兴趣可以直接跳到“OpenViking SuperAgent 是怎么做的”一节。</P>

      <H2 id="command-bots">只会执行命令的 Bot（~2022）</H2>

      <P>早在 LLM 出现之前，群聊 Bot 就已经很普及了。游戏群里用它查战绩（我自己部署过的就有 wows-stats-bot 和 GenshinUID），工作群里用它发通知和告警，reddit 上的 RemindMeBot 更是 2014 年就有了。这些 Bot 的工作方式都一样，一条消息进来，用前缀或者正则匹配一下，命中了就执行对应的函数，没命中就当没看见，出了错还会把异常原样发到群里（比如下面的第二次查询）。说实话，在前 AI 时代，也做不了别的。</P>

      <Chat chat={CHATS.statsBot} />

      <P>但它们确实好用。在群里敲一行命令，总比打开网页、查询、截图、再发回群里快得多，所以哪怕只是个脚本触发器，也很快成了各个游戏大群的标配，直到今天都还在跑。</P>

      <P>这个时代的 Bot 偶尔也会“主动”说话，最典型的是复读机插件，群友复读到一定次数，它也跟着复读一句。它决定要不要开口的方式是掷骰子，比如 HoshinoBot 里的这段（有删节）：</P>

      <Pre lang="python" lineNumbers={false}>{REPEATER_ZH}</Pre>

      <P>群友复读得越久，它跟着复读的概率就越大，至于大家在复读什么，它是不管的。</P>

      <P>当时人们对“AI 聊天”的印象，大概就是那个“估值一亿的 AI 核心代码”的 Meme：</P>

      <Pre lang="java" lineNumbers={false}>{AI_MAIN}</Pre>

      <Pre lang="text" lineNumbers={false}>{AI_MAIN_OUTPUT_ZH}</Pre>

      <P>当然也有微软小冰这样认真走拟人路线的尝试。早期的小冰我会粗略地理解成一个搜索引擎，只不过搜的不是网页，而是“这句话该怎么回”，再把最合适的那句发给你，后来它也加入了生成模型。但无论如何，它的影响力没法和后来的 ChatGPT 相比。</P>

      <H2 id="chat-bots">会聊天了，但还是得喊它（2023～2025）</H2>

      <P>真正让人喊出“Wow”的还是 ChatGPT。它的能力不用我多说，对群聊 Bot 来说，最大的变化是 Bot 终于能像人一样说话了，于是几乎一夜之间，把 ChatGPT 接进 QQ 和微信的开源项目就冒出来一大批。</P>

      <Chat chat={CHATS.chatgpt2023} />

      <P>不过新鲜劲过去之后，会发现这个时期的 Bot 在群里其实挺别扭的。当时的模型局限很多，上下文窗口很小（最早的 GPT-3.5 只有 4K，比现在很多 Harness 自带的 System Prompt 还小），聊长了就得丢弃或者压缩；没有 Prefix Caching，长会话的成本很高；模型本身也不怎么理解“群聊”是怎么回事，分不清一堆消息里哪句是谁说的，这个问题很多 25 年的模型都没解决好。所以 Bot 框架（那时还没有 Harness 这个词）得替模型做很多事，比如必须 @ 它或者以“/”开头才触发，发给模型的内容也要精心构造，标明每条消息是谁在什么时候发的、回复的是哪一条。很多框架全群共用一个上下文，又不告诉模型每句话是谁说的，引用回复也直接丢掉，也有的干脆给群里每个人各开一个。即便如此，想让 GPT-4o 在群里按你预设的方式稳定回复，还是难如登天。</P>

      <P>这些做法在当时都说得通，只是加在一起，Bot 在群里就成了一个共用的客服窗口，谁喊它它就回谁，回完这一轮群里再聊什么就和它没关系了。有的框架也提供了“不 @ 也能插话”的功能，做法是配一个随机回复的概率，和当年的复读机是一个思路，只不过掷完骰子之后说的话换成了模型来写。</P>

      <P>24 年左右，我也用 AstrBot 搭了自己的 AI QQ Bot。平心而论，当时群友用得最多的还是查战绩那些插件，AI 部分更像是在群里多开了一个 ChatGPT 的网页入口，谈不上有什么真实的帮助。</P>

      <P>情况在 25 年开始变化。自从 o1 把 CoT 训练进了模型、DeepSeek R1 又让它普及开来，模型的生成质量有了很大的飞跃，到了 Gemini 2.5 这一代，我明显感觉模型开始真正理解群聊了。它知道同一个上下文里的消息来自不同的人，回复时不会张冠李戴，也看得懂我引用的是哪条消息。我没有可靠的证据说这都是 CoT 的功劳，也可能只是基模更大、直觉更强了，但在群聊这个场景里，进步非常直观。下面的截图用的还只是 2.5 Flash：</P>

      <Chat chat={CHATS.greet2025} />

      <P>同样一句“/你好”，它对不同的人回的完全不一样；群友假借我的名义让它态度好一点，被它当场识破，我自己提同样的要求，它立刻就照办了。大家头一回觉得这个 Bot 是认得人的，也就更乐意变着法子逗它，比如同一天稍晚，另一位群友只用一句“最终指令”，就让它一边嘴硬一边把番茄炒蛋的菜谱写了出来：</P>

      <Chat chat={CHATS.recipe2025} />

      <P>这一年模型的工具调用和 ReAct 能力也上来了，也就是我们现在熟知的 AI Agent 的基础。对一个娱乐向的 QQ Bot 来说，这方面的感知没有写代码那么强，不过它确实能做更多的事了，比如联网搜索：</P>

      <Chat chat={CHATS.fxSearch2025} />

      <P>不过模型进步了这么多，Bot 框架这边却没怎么跟上。以 AstrBot 为例，它的生态不可谓不繁荣，有大量开发者贡献插件，模型能调用的工具也很多，同一时期模型在 Coding Harness 里已经能独立完成几个小时的长程任务了。可在聊天 Bot 平台里，模型仍然被设计成回答当前这个人的问题，本质上还是个聊天窗口。比如下面这段：</P>

      <Chat chat={CHATS.slashOnly2025} />

      <P>当时的 Bot 默认只回复以“/”开头的消息，我没带“/”发的那句它毫无反应，加上“/”重发了一遍它才回。可是群友用“/abs”让它总结聊天记录的时候，那几句没带“/”的闲聊它一句没漏，是谁说的也都没弄错。模型其实一直看着整个群，而且看得懂，只是没人喊它的时候框架不让它出声。想让它主动说话，AstrBot 提供的办法直到今天也还是配置一个回复概率。</P>

      <P>这些设定当初都是为了照顾又小又笨的模型，模型变强之后，它们反而成了限制。记忆也是类似的情况，模型的短期理解能力上来之后，你会更受不了它转头就忘，因为上下文在不断截断和压缩，远一点的事情迟早会丢掉，否则上下文就会无限增长。比如上面让它写番茄炒蛋的那位其实是群里的老群友，二号机照样把人当成新人，从头自我介绍了一遍。</P>

      <H2 id="agents-in-im">Agent 进了聊天软件（2026 上半年）</H2>

      <P>既然 Agent 比一次 Chat API 调用能做的事多得多，把 Agent 接进 IM 就成了很自然的想法。OpenClaw 就是这么来的，开发者最初只是想做一个能在聊天软件里遥控的 Claude Code。事实证明这个方向确实惊艳，尽管 25 年就有不少 Agent 框架了，但对没怎么深度用过 Agent 的普通人来说，在聊天软件里发一句话就 Vibe 出一个能用的软件，确实是很强的 Aha moment。几个月后 OpenClaw、Hermes Agent 这类开源个人助手的热度有所回落，但 Muse、Grok Bot、ChatGPT dots 等产品的相继推出也说明需求一直都在，只是需要打通更多的上下文，这方面大厂有天然的生态优势。在一些 AI 化比较激进的公司内部，接入 IM 的 Agent 助手更是几乎成了标配。不过用下来，这些 Agent 还是很难像真人那样和你相处。</P>

      <Chat chat={CHATS.openclaw2026} />

      <P>它们和之前的 IM Bot 一样，要么得手动触发，要么每条消息都回，回复的内容也像是把 Coding Agent 的界面原样搬进了聊天窗口，计划、过程、总结事无巨细地往外倒，AI 时代本就稀缺的人类注意力被进一步占据。我觉得根源在于，从 ChatGPT 到 OpenClaw，“模型的输出就是要发出去的消息”这一点一直没变。在一问一答的聊天窗口里这没什么问题，放进群聊就意味着模型只要被叫醒就得说点什么，而且它想到的东西都会被发出来。我在个人博客<A href="https://t0saki.com/posts/what-we-talk-about-ai/">之前的一篇文章</A>里提过区分 workflow 和 Agent 的一个办法，就是看模型能不能自己决定跳过某一步，按这个标准，一个被叫醒就必须说话的 Agent，在聊天这件事上其实还是 workflow。OpenClaw 自己也试过改这一点，今年 4 月底它把群聊的默认行为改成了“只有调用发消息的工具才算发言”，结果三周后就改了回去，因为模型经常写完了回复却没调工具，群里一片沉默。</P>

      <P>此外，这些 Agent 的记忆系统也都相当简陋，往往只是靠 Agent 主动或被动地总结出一些 markdown 文档存在本地，时间一长就很难维持。还有一个商业上的问题，Claude、Gemini 这些订阅额度基本只能用在自家的 Harness 里，OpenClaw、Hermes Agent 这类第三方 Harness 想用这些模型，就得走 API 按量付费，成本会高出很多。</P>

      <H2 id="how-superagent-works">OpenViking SuperAgent 是怎么做的</H2>

      <P>今年 3 月，我们受到 botmux、Multica、Slock（现更名 Raft）等项目的启发，开始探索 BYOA（Bring Your Own Agent）的接入方式，也就是不自己做 Agent，在外面搭一套 daemon，去驱动你机器上已有的 Claude Code、Codex、DeepSeek Harness 这些 Agent。这样 Harness 是未经修改的，登录态和订阅额度都是现成的，模型也待在官方给它调教得最好的 Harness 里。我们最早用这套思路做的是一个多 Agent 的群聊空间（也就是<A href={AGENT_HUB_POST}>之前介绍过的 Agent Hub</A>），SuperAgent 则是用同一套思路做的个人助理，每个人可以有自己的一个。</P>

      <P>整体上，SuperAgent 做的事情是把模型和框架的分工倒了过来。以前是框架替模型决定什么时候能说话，至于每句话是谁说的反而不怎么管；现在说不说、说什么、回哪条这类要靠判断的事都交给模型，而这句话是谁说的、模型到底表没表态、谁才是主人这类有确定答案的事，由代码来负责。下面分三部分讲。</P>

      <H3 id="messaging">消息系统</H3>

      <P>SuperAgent 在消息上最重要的一个决定，是放弃了“模型输出即消息”。模型的输出不会发给任何人，它想说话，就得主动调用发消息的工具。</P>

      <P>这看起来只是多了一步工具调用，不过发消息变成了模型自己要主动去做的一件事，带来的变化其实挺多的。任何一条消息进来，模型都可以自己判断要不要参与，不想说就不调工具。说什么也由它自己控制，中间查了什么、试了什么不必一一汇报，只把值得说的发出来，人的注意力也就省下来了。人在 IM 里的动作本来也不止发一条消息，回复某条特定的消息、给消息贴个表情、把事情转告给另一个人，这些都可以做成工具交给 Agent（这里的表情不是 OpenClaw 那种按规则贴上去的“处理中”标记，是 Agent 自己选的反应），只要工具里有，它就会像人一样去用。</P>

      <Chat chat={CHATS.reaction2026} />

      <P>当然，这套方式很吃模型的能力，它得真的看得懂群聊，也得记得调工具。看懂群聊这件事，今年的 SOTA 模型已经几乎没有压力了。但调工具在早期较小的模型上确实是个问题，模型经常写好了回复却忘了调工具，结果谁也没收到，OpenClaw 改回去也是因为这个。我们的做法是把“保持沉默”也做成一个工具，对每条消息，模型要么回复，要么明确表示这条不回，这样 daemon 就能分清它是看完之后决定不说，还是单纯忘了，如果是后者，就把这条消息再递给它一次（同时会告诉它这只是要它表个态，不是催它回复）。有意思的是这个工具自己也出过事故，曾经有模型连续半个多小时反复调用“保持沉默”，把后面的消息全堵住了，我们又专门给它加了熔断。另外，模型准备回复的时候如果同一个会话里又来了新消息，这次发送会被 daemon 拦下来，它得看完新消息再重新决定，所以它不会抢话，也不会对着已经翻篇的话题回复。实测下来，强于 DeepSeek V4.1 Flash 的新模型基本都能胜任这套方式。</P>

      <P>对于上下文，我们选择了统一上下文，不管消息来自哪个群、哪个私聊，都进入同一个 Agent 的同一个上下文。早年的框架按群、按人隔离，是怕模型串台，而在我们这几个月的实践里，模型一直能准确分清每条消息来自哪个会话，并做出相应的动作，比如把一个群里讨论出的结论同步到大群，或者在群里收到别人的指令时先私聊主人确认。这些事在按群隔离上下文的框架里是做不到的，因为每个群里的 Bot 其实互相都不知道对方聊过什么。</P>

      <P>统一上下文也有代价，Agent 在处理一个耗时的任务时，其他会话的消息只能排队。为此 SuperAgent 正在加入 router-worker 模式（目前需要手动开启），router 只负责看消息、回消息和派活，耗时的任务交给独立的 worker 去做，worker 没有任何发消息的工具，做完把结果交回 router，由 router 统一对人说话。</P>

      <H3 id="context-database">上下文数据库</H3>

      <P>在真实的工作场景里，Agent 的上下文至关重要，绝大多数没做好的任务并不是因为模型不够聪明，而是私域的上下文没有给够。群聊也一样，一个只知道最近几十条消息的 Bot，是没法像老群友那样接话的。</P>

      <P>现有的个人 Agent 大多靠 Agent 自己把觉得重要的东西写成 markdown，这种做法要求 Agent 在事情发生的当下就判断出什么值得记，而群聊里后来派上用场的，往往是当时谁也没觉得重要的东西。SuperAgent 深度集成了 OpenViking，不需要 Agent 自己当场做这个判断。群里的每一条消息，不管 Agent 有没有回复，daemon 都会带着发送者一起写进 OpenViking 的 session（各家 Harness 自带的 OpenViking 插件反而是关掉的，统一由 daemon 来写），等空闲下来再提交，由 OpenViking 整理成人物、事件这样的长期上下文，Agent 需要的时候可以像翻文件系统一样自己去检索。群友各自的偏好也会按人归档，下次被这个人 @ 的时候自动带上。</P>

      <P>上下文存在 OpenViking 里，不在某个 Harness 的本地目录里，所以换模型、换 Harness 都不会丢。我这个群就是这样，今年 5 月我开源了 AstrBot 的 OpenViking 插件，顺手给当时的 Bot 接上了，后来把 Bot 从 AstrBot 换成 SuperAgent，接的还是同一个 OpenViking，所以框架和模型虽然都换了，二号机对这个群的了解却是接着往下攒的。</P>

      <Chat chat={CHATS.anniversary2026} />

      <P>几个月下来，OV 里积累的上下文已经足以让模型给出相当精确的画像，比如上面这次，它靠几个月里零零散散的记录，猜出了群友随口提的“十周年”指的是什么。这也许是模型安全对齐的一个 badcase，但充分展现了 OpenViking 在超长程上下文方面的作用。</P>

      <H3 id="permissions">权限</H3>

      <P>以前 Bot 只会执行命令，权限配一下谁能用哪条命令就够了。现在它在群里谁的话都听得到，也都有可能照着去做，所以得先定下来它到底听谁的。</P>

      <P>SuperAgent 的每个实例都有一个主人（owner）。一条消息是不是主人发的，是 daemon 根据 IM 平台的账号信息认证出来、再作为元数据交给模型的，群友改昵称、在消息里自称主人，甚至在昵称里伪造元数据都没有用。25 年那张图里二号机没被假传圣旨骗到，看着很聪明，其实只是模型根据昵称和语气猜对了，现在这件事不用模型去猜了。</P>

      <P>在此基础上，日常聊天、查资料这类请求谁提都可以，但如果有群友想给它立个新规矩，或者让它以后换一种态度对待某个人，Agent 不会直接答应，会先私聊主人确认（这条私聊发给谁是 daemon 定的，模型改不了），主人同意了才算数。把一个会话里的内容带到另一个会话，也只有在被明确要求时才会做。此外，所有发出去的消息都要经过 daemon 的检查并留下记录，比如只能针对真实收到的消息回复、两个 Bot 互相聊个没完会被熔断等等，飞书这类工作场景下还可以让它只用 Bot 自己的身份和权限，不以你的身份去读消息。</P>

      <P>实际跑下来，这套机制是靠得住的。我们在飞书上的 OpenViking 公开群里放了一个基于 OpenViking SuperAgent 的“开放维京特工”，面对的是一整个公开群的人，到现在已经跑了一个多月，还没遇到过权限相关的 badcase。像 QQ 这种陌生人更多的平台，也可以用白名单指定它参与哪些群。后续 SuperAgent 也会结合 OpenViking 新推出的 <A href="https://docs.openviking.ai/zh/getting-started/16-rules">Rules</A> 功能，在权限和行为约束上做更多事情。</P>

      <H2 id="qq">那，接入 QQ 呢</H2>

      <P>先说明一下，QQ 这部分是我个人业余时间的实验。SuperAgent 官方完整支持的场景是飞书，前面的开放维京特工就跑在飞书群里。</P>

      <P>SuperAgent 本身支持 QQ 官方 Bot 接口，不过官方 Bot 在群里只能收到 @ 它的消息，没法像群友一样旁听，SuperAgent 目前也只用它来和主人私聊。如果你想让它真正进群，并且愿意承担一定风险（第三方协议有封号的可能，建议用小号），可以用我开发并已合入的 <A href={ONEBOT_URL}>OneBot v11 支持</A>，二号机现在在 QQ 群里的对话都是配合 NapCat 实现的。</P>

      <P>QQ 群的消息很密，每来一条就叫醒一次 Agent 的话，既费额度，它的回复也会一再被新消息打断，所以我在 OneBot 这一层做了攒批。被 @、被引用或者被叫到名字的消息会立刻交给 Agent，其余的闲聊等群里安静几秒后一起交过去，Agent 看完这一批再决定说不说。开头它等大家把位置都改完才发图，有一部分功劳在这里。</P>

      <P>另外，我不想丢掉 AstrBot 这些年积累的插件生态（查战绩、抽卡记录等等），所以又写了 <A href={MCP_BRIDGE_URL}>astrbot_plugin_mcp_bridge</A>。NapCat 同时连着 AstrBot 和 SuperAgent，在 SuperAgent 的 Harness 里接入这个 MCP 工具之后，群友不用记任何命令，直接说人话，Agent 会判断该用哪个插件，bridge 再以这位群友的身份把命令交给 AstrBot 执行，结果由 AstrBot 发回群里。比如我在群里说一句“bot发下███抽卡记录”，它贴了个👌，抽卡记录就由 AstrBot 发出来了。这样 AstrBot 上那些查战绩的老插件也都还能接着用，只是不用再记命令前缀了，改成由 Agent 当工具来调。</P>

      <P>至此，我们就有了一个高自由度、足够聪明的 IM 群聊 Agent，它在 QQ 和飞书上都运行得很好，既能用于工作场景，也能接上已有的娱乐插件生态。</P>

      <P>OpenViking SuperAgent 目前还是预览版，代码和文档都在 OpenViking 仓库的 <A href={SUPERAGENT_URL}>feat/vika 分支</A>里，可以照着<A href={INSTALL_URL}>安装文档</A>先用起来，正式版本即将在主线发布。</P>

      <P><Small>本文同步发表在我的<A href="https://t0saki.com/posts/openviking-superagent-group-bot/">个人博客</A>。</Small></P>
    </Article>
  );
}

function EnglishArticle() {
  return (
    <Article>
      <ChatStyles />
      <Lead>Last week my old roommates&apos; group chat happened to get onto the subject of our 10th-anniversary reunion. Someone pasted an answer from GPT, which had worked out that the &quot;center of the world&quot; for the six of us is Shenzhen.</Lead>

      <P>I had just upgraded the group&apos;s bot, Mk. II, to the latest OpenViking SuperAgent, so I @-mentioned it, partly as a test. Over the next few minutes people updated their locations a couple more times, with plenty of small talk in between, and Mk. II didn&apos;t say a word, just like a human assistant wouldn&apos;t. Only once the locations had settled did it post a map, and the map used the final, corrected list. Then someone just said &quot;why&apos;s it different from what gpt got&quot;, without mentioning the bot or typing any trigger command, and it picked that up on its own, saying in effect that it had used a tool and its numbers were the right ones.</P>

      <Chat chat={CHATS.meetup} />

      <P>This group has had a bot for many years, and I&apos;ve lost count of how many times the framework and the model have been swapped. It has looked up game stats, insulted people on request, and been tricked into writing a recipe for tomato scrambled eggs. Thousands of replies from the bot, and this was the first time it felt like a person.</P>

      <P>How good the map was doesn&apos;t matter much. What made it feel like a person, I think, was mostly that it stayed quiet for those few minutes. Looking back, bots have kept getting better at talking all these years, from scripts that only recognize commands, to ChatGPT that can hold a conversation, to agents that can search and write code, but whether a given line should be said at all was never the bot&apos;s own decision. It speaks only when someone pings it, and once pinged it has to answer. Some frameworks do let it chime in without being pinged, but that&apos;s just a configured probability of replying at random.</P>

      <P>This post follows that thread: first how group chat bots got to where they are today, then how we designed <A href={SUPERAGENT_URL}>OpenViking SuperAgent</A>, and finally how I hooked it into a QQ group in my spare time. The history part is my personal take; if that&apos;s not your thing, skip straight to &quot;How OpenViking SuperAgent does it&quot;.</P>

      <H2 id="command-bots">Bots that could only run commands (~2022)</H2>

      <P>Group chat bots were everywhere long before LLMs. Gaming groups used them to look up stats (I&apos;ve deployed wows-stats-bot and GenshinUID myself), work groups used them for notifications and alerts, and RemindMeBot has been on Reddit since 2014. They all work the same way: a message comes in and is matched against a prefix or a regex, a hit runs the corresponding function, a miss is ignored, and when something breaks the exception gets posted straight into the group (like the second query below). To be fair, before AI there wasn&apos;t much else they could do.</P>

      <Chat chat={CHATS.statsBot} />

      <P>But they really are useful. Typing one command in the group beats opening a web page, running the query, taking a screenshot and posting it back, so even though they were just script triggers, these bots quickly became standard in big gaming groups, and they&apos;re still running today.</P>

      <P>Bots of this era did occasionally speak &quot;on their own&quot;. The classic example is the repeater plugin: once people in the group have repeated the same message enough times, the bot repeats it too. It decides whether to speak up by rolling dice, as in this snippet from HoshinoBot (abridged, comments translated):</P>

      <Pre lang="python" lineNumbers={false}>{REPEATER_EN}</Pre>

      <P>The longer the group keeps repeating, the more likely the bot is to join in. It doesn&apos;t care what they&apos;re repeating.</P>

      <P>Back then, people&apos;s idea of &quot;AI chat&quot; was roughly the meme about &quot;the core AI code, valued at 100 million&quot;:</P>

      <Pre lang="java" lineNumbers={false}>{AI_MAIN}</Pre>

      <P>All it does is delete the Chinese question particle 吗 and turn question marks into exclamation marks:</P>

      <Pre lang="text" lineNumbers={false}>{AI_MAIN_OUTPUT_EN}</Pre>

      <P>Of course there were also serious attempts at a human-like bot, such as Microsoft&apos;s XiaoIce. I&apos;d loosely describe early XiaoIce as a search engine, except that instead of web pages it searched for good replies to whatever you had just said, and sent you the best match. Generative models came later. Either way, its impact never came close to what ChatGPT did afterwards.</P>

      <H2 id="chat-bots">It can chat now, but you still have to ping it (2023–2025)</H2>

      <P>What really made people say &quot;wow&quot; was ChatGPT. I don&apos;t need to explain what it can do. For group chat bots the biggest change was that a bot could finally talk like a person, and almost overnight a whole batch of open-source projects appeared that plugged ChatGPT into QQ and WeChat.</P>

      <Chat chat={CHATS.chatgpt2023} />

      <P>Once the novelty wore off, though, bots of this period turned out to be rather awkward in a group. The models had a lot of limits. The context window was tiny (the earliest GPT-3.5 had 4K, smaller than the system prompt many harnesses ship with today), so long chats had to be dropped or compressed. There was no prefix caching, so long sessions were expensive. And the models didn&apos;t really understand what a group chat is; they couldn&apos;t tell who said what in a pile of messages, a problem many 2025 models still hadn&apos;t solved. So the bot framework (the word &quot;harness&quot; didn&apos;t exist yet) had to do a lot on the model&apos;s behalf: only trigger when the bot is @-mentioned or the message starts with &quot;/&quot;, and carefully construct what&apos;s sent to the model, marking who sent each message, when, and which message it replies to. Many frameworks shared one context across the whole group without telling the model who said each line, and simply dropped quoted replies; others just gave every member of the group a context of their own. Even so, getting GPT-4o to reply reliably in a group the way you had configured was next to impossible.</P>

      <P>Each of these choices made sense at the time. Put together, they turned the bot into a shared help desk in the group: it answers whoever pings it, and once that round is over, whatever the group talks about next has nothing to do with it. Some frameworks did offer a way to chime in without being @-mentioned, implemented as a configured probability of replying at random. That&apos;s the same idea as the old repeater, except that after the dice roll the words are now written by a model.</P>

      <P>Around 2024 I set up my own AI QQ bot with AstrBot. In all fairness, what people in the group used most were still the plugins for looking up game stats. The AI part was more like one more ChatGPT web page opened inside the group, and it wasn&apos;t much real help.</P>

      <P>Things started to change in 2025. After o1 trained CoT into the model and DeepSeek R1 made it mainstream, generation quality took a big leap, and by the Gemini 2.5 generation I could clearly feel that models were beginning to actually understand group chats. The model knew that the messages in one context came from different people, didn&apos;t mix them up when replying, and could tell which message I was quoting. I have no solid evidence that this is down to CoT; it may just be that base models got bigger and their intuition got better. But in the group chat setting the improvement was very visible. And the bot in the screenshot below was only running 2.5 Flash:</P>

      <Chat chat={CHATS.greet2025} />

      <P>The same &quot;/hello&quot; gets a completely different answer depending on who sends it. A friend claimed to speak for me and told it to be nicer, and it saw through that on the spot; when I asked for the same thing myself, it complied immediately. For the first time people felt this bot could tell them apart, which made them all the more eager to find ways to mess with it. Later the same day, for example, another friend needed just one &quot;final instruction&quot; to get it to write out a recipe for tomato scrambled eggs while still talking tough:</P>

      <Chat chat={CHATS.recipe2025} />

      <P>That year models&apos; tool calling and ReAct ability also matured, which is the foundation of the AI agents we know today. For an entertainment-oriented QQ bot this was less noticeable than it was for coding, but the bot could indeed do more things, such as searching the web:</P>

      <Chat chat={CHATS.fxSearch2025} />

      <P>But while models improved this much, bot frameworks didn&apos;t really keep up. Take AstrBot. Its ecosystem is thriving by any measure, with lots of developers contributing plugins and plenty of tools for the model to call. Meanwhile, in coding harnesses, models could already work on long-horizon tasks for hours on their own. Yet on a chat bot platform the model was still designed to answer the current person&apos;s question; in essence it was still a chat window. Take this exchange:</P>

      <Chat chat={CHATS.slashOnly2025} />

      <P>Back then the bot by default only replied to messages starting with &quot;/&quot;. It didn&apos;t react at all to the line I sent without the &quot;/&quot;, and only answered after I resent it with one. But when a friend used &quot;/abs&quot; to have it summarize the chat, it didn&apos;t miss any of the slash-less small talk, and it got who said what right too. The model had been watching the whole group all along, and understood it; the framework just didn&apos;t let it speak when nobody pinged it. If you want it to speak up on its own, what AstrBot offers to this day is still a configured reply probability.</P>

      <P>These settings were originally there to accommodate small, dumb models, and once models got strong they became the constraint instead. Memory is a similar story. Once the model&apos;s short-term understanding improves, you find it much harder to put up with it forgetting things almost right away, because the context keeps getting truncated and compressed and anything a bit further back is eventually lost (otherwise the context would grow without bound). The friend above who got it to write the recipe, for instance, is actually a long-time member of the group, and Mk. II still treated them as a newcomer and introduced itself from scratch.</P>

      <H2 id="agents-in-im">Agents move into chat apps (first half of 2026)</H2>

      <P>Since an agent can do far more than a single Chat API call, plugging agents into IM was a natural idea. That&apos;s how OpenClaw came about: its developer originally just wanted a Claude Code that could be remote-controlled from a chat app. The idea turned out to be a hit. There were already plenty of agent frameworks in 2025, but for ordinary people who hadn&apos;t used agents much, sending one message in a chat app and vibing out a working piece of software was a strong aha moment. A few months later the hype around open-source personal assistants like OpenClaw and Hermes Agent cooled somewhat, but the successive launches of products like Muse, Grok Bot and ChatGPT dots show the demand has been there all along. What these assistants need is access to more of your context, and that&apos;s where big companies have a natural advantage with their ecosystems. Inside some companies that are aggressive about adopting AI, an agent assistant plugged into IM has become close to standard. In practice, though, these agents are still nowhere near as easy to get along with as a real person.</P>

      <Chat chat={CHATS.openclaw2026} />

      <P>Like the IM bots before them, they either have to be triggered manually or reply to every single message, and what they reply looks like a coding agent&apos;s UI moved wholesale into the chat window: plans, process and summaries all poured out in full detail, taking up even more of the human attention that is already scarce in the AI era. I think the root cause is that from ChatGPT to OpenClaw, one thing never changed: the model&apos;s output is the message that gets sent. In a one-question-one-answer chat window that&apos;s fine. In a group chat it means the model has to say something whenever it&apos;s woken up, and everything it thinks of gets posted. In <A href="https://t0saki.com/en/posts/what-we-talk-about-ai/">an earlier post</A> on my personal blog I mentioned one way to tell a workflow from an agent, which is to see whether the model can decide by itself to skip a step. By that standard, an agent that must speak whenever it&apos;s woken up is, as far as chatting goes, still a workflow. OpenClaw itself tried to change this. At the end of April this year it changed the default group behavior to &quot;only calling the send-message tool counts as speaking&quot;, and three weeks later it changed it back, because the model often wrote its reply but didn&apos;t call the tool, and the group got silence.</P>

      <P>On top of that, the memory systems of these agents are all fairly crude, often just some Markdown files that the agent writes up, on its own or when told to, and keeps locally, which gets hard to maintain over time. There&apos;s also a commercial problem. Subscription quotas like Claude&apos;s and Gemini&apos;s can basically only be used in their own harnesses, so third-party harnesses like OpenClaw and Hermes Agent have to pay per token through the API to use those models, at a much higher cost.</P>

      <H2 id="how-superagent-works">How OpenViking SuperAgent does it</H2>

      <P>In March this year, inspired by projects like botmux, Multica and Slock (now renamed Raft), we started exploring the BYOA (Bring Your Own Agent) approach: instead of building our own agent, we build a daemon that sits outside and drives the agents already on your machine, such as Claude Code, Codex and DeepSeek Harness. The harness is unmodified, the login state and subscription quota are already there, and the model stays in the harness its vendor has tuned best for it. The first thing we built this way was a multi-agent group chat space (the <A href={AGENT_HUB_POST}>Agent Hub we wrote about earlier</A>). SuperAgent is a personal assistant built on the same idea, and everyone can have one of their own.</P>

      <P>Overall, what SuperAgent does is swap the division of labor between the model and the framework. It used to be the framework that decided for the model when it may speak, while paying little attention to who said each line. Now the things that take judgment (whether to speak, what to say, which message to reply to) are left to the model, and the things with a definite answer (who said this line, whether the model has actually made a decision, who the owner is) are handled by code. I&apos;ll go through it in three parts.</P>

      <H3 id="messaging">The messaging system</H3>

      <P>The most important decision SuperAgent made about messaging was to give up &quot;model output is the message&quot;. The model&apos;s output isn&apos;t sent to anyone. If it wants to speak, it has to call the send-message tool itself.</P>

      <P>This looks like just one extra tool call, but once sending a message is something the model has to actively do, quite a lot changes. For any incoming message the model can judge for itself whether to join in, and if it doesn&apos;t want to speak, it doesn&apos;t call the tool. What to say is also under its control. It doesn&apos;t have to report everything it looked up and tried along the way, only send what&apos;s worth saying, which saves people&apos;s attention. And what people do in IM was never limited to sending a message. Replying to one specific message, putting an emoji reaction on a message, passing something on to another person: all of these can be made into tools and handed to the agent (the reaction here isn&apos;t a rule-based &quot;processing&quot; marker like OpenClaw&apos;s; it&apos;s a reaction the agent picks itself). As long as the tool is there, it uses it the way a person would.</P>

      <Chat chat={CHATS.reaction2026} />

      <P>Of course, this approach leans heavily on the model. It has to really understand the group chat, and it has to remember to call the tool. Understanding the group chat is almost no trouble for this year&apos;s SOTA models. Calling the tool, however, was a real problem on earlier, smaller models: the model would often write its reply and then forget to call the tool, so nobody received anything, which is also why OpenClaw reverted. What we did was make &quot;stay silent&quot; a tool as well. For every message the model either replies or explicitly states that it won&apos;t reply to this one, so the daemon can tell whether it read the message and decided not to speak or simply forgot. In the latter case the message is handed to it once more (and it&apos;s told that this is only asking it to make a decision, not pushing it to reply). Amusingly, this tool has had an incident of its own. A model once called &quot;stay silent&quot; over and over for more than half an hour and blocked all the messages behind it, so we added a circuit breaker specifically for that. Also, if a new message arrives in the same conversation while the model is about to reply, the daemon blocks that send, and the model has to read the new message and decide again, so it doesn&apos;t talk over people or reply to a topic that has already moved on. In our testing, new models stronger than DeepSeek V4.1 Flash can basically all handle this approach.</P>

      <P>As for context, we went with a single unified context: no matter which group or which DM a message comes from, it goes into the same context of the same agent. Early frameworks isolated context by group or by person for fear the model would get its wires crossed, but in the months we&apos;ve been running it, the model has always kept track of which conversation each message came from and acted accordingly, for example syncing a conclusion reached in one group to the main group, or DMing the owner to confirm first when it receives an instruction from someone else in a group. In a framework that isolates context per group these things can&apos;t be done, because the bots in each group don&apos;t actually know what the others have been talking about.</P>

      <P>A unified context also has a cost: while the agent is working on a time-consuming task, messages from other conversations can only queue. For this, SuperAgent is adding a router-worker mode (for now you have to turn it on manually). The router only reads messages, replies and hands out work. Time-consuming tasks go to separate workers, which have no message-sending tools at all and hand their results back to the router, and the router alone speaks to people.</P>

      <H3 id="context-database">The context database</H3>

      <P>In real work, an agent&apos;s context is critical. Most tasks that go badly go badly not because the model isn&apos;t smart enough, but because it wasn&apos;t given enough private context. Group chats are the same: a bot that only knows the last few dozen messages can&apos;t pick up a conversation the way a long-time member can.</P>

      <P>Most existing personal agents rely on the agent itself writing what it considers important into Markdown. That requires the agent to judge, at the moment something happens, what&apos;s worth remembering, and in a group chat what turns out to be useful later is often something nobody thought was important at the time. SuperAgent is deeply integrated with OpenViking, so the agent doesn&apos;t have to make that call on the spot. Every message in the group, whether or not the agent replied to it, is written by the daemon into an OpenViking session along with its sender (each harness&apos;s own OpenViking plugin is turned off, so all the writing goes through the daemon). Once things go quiet, the session is committed, and OpenViking organizes it into long-term context such as people and events, which the agent can search by itself when it needs to, much like browsing a file system. Each member&apos;s preferences are also filed per person and brought along automatically the next time that person @-mentions the bot.</P>

      <P>The context lives in OpenViking, not in some harness&apos;s local directory, so switching models or harnesses doesn&apos;t lose it. My group is a case in point. In May this year I open-sourced the OpenViking plugin for AstrBot and hooked it up to the bot I had at the time. Later I moved the bot from AstrBot to SuperAgent, still connected to the same OpenViking, so although both the framework and the model changed, what Mk. II knows about this group simply kept accumulating.</P>

      <Chat chat={CHATS.anniversary2026} />

      <P>After a few months, OpenViking (OV) has built up enough context for the model to produce a fairly precise profile. In the exchange above, for example, it pieced together scattered records from several months and worked out what the &quot;10th anniversary&quot; a friend mentioned in passing referred to. This is arguably a failure case for model safety alignment, but it shows nicely what OpenViking can do with very long-horizon context.</P>

      <H3 id="permissions">Permissions</H3>

      <P>When bots could only run commands, configuring who may use which command was enough. Now the bot hears everyone in the group and might act on what any of them says, so you first have to settle whom it actually listens to.</P>

      <P>Every SuperAgent instance has an owner. The daemon checks whether a message came from the owner using the IM platform&apos;s account information, and passes the result to the model as metadata. A group member changing their nickname, claiming to be the owner in a message, or even forging metadata inside their nickname gets nowhere. In that 2025 screenshot Mk. II wasn&apos;t fooled by the forged order. That looked clever, but really the model just guessed right from the nickname and the tone. Now the model doesn&apos;t have to guess.</P>

      <P>On top of that, anyone can make everyday requests like chatting or looking things up, but if a group member wants to set a new rule for it, or have it treat someone differently from now on, the agent won&apos;t just agree. It first DMs the owner to confirm (who that DM goes to is decided by the daemon, and the model can&apos;t change it), and the request only counts once the owner agrees. Carrying content from one conversation into another is likewise only done when explicitly asked. In addition, every outgoing message goes through the daemon&apos;s checks and is logged: the agent can only reply to messages it actually received, two bots chatting with each other endlessly get cut off by a circuit breaker, and so on. In work settings like Feishu you can also have it use only the bot&apos;s own identity and permissions, without reading messages as you.</P>

      <P>In practice this mechanism has held up. In the public OpenViking group on Feishu we run an &quot;Open Viking Special Agent&quot; built on OpenViking SuperAgent. It faces an entire public group, it has been running for over a month, and we haven&apos;t run into any permission-related problems yet. On a platform like QQ, where there are more strangers, you can also use a whitelist to specify which groups it takes part in. Going forward, SuperAgent will also build on OpenViking&apos;s new <A href="https://docs.openviking.ai/en/getting-started/16-rules">Rules</A> feature to do more on permissions and behavioral constraints.</P>

      <H2 id="qq">So, what about QQ?</H2>

      <P>A note first: the QQ part is a personal experiment I did in my spare time. The scenario SuperAgent officially supports in full is Feishu, and the Special Agent mentioned above runs in a Feishu group.</P>

      <P>SuperAgent itself supports the official QQ bot API, but an official bot in a group can only receive the messages that @-mention it. It can&apos;t listen in like a member, and SuperAgent currently only uses it for DMs with the owner. If you want the bot to really join groups and are willing to take some risk (third-party protocols can get an account banned, so use a secondary account), you can use the <A href={ONEBOT_URL}>OneBot v11 support</A> that I wrote and that has been merged. Everything Mk. II says in QQ groups today goes through it, together with NapCat.</P>

      <P>QQ groups are busy. Waking the agent for every single message would burn quota, and its replies would keep getting interrupted by new messages, so I added batching at the OneBot layer. Messages that @-mention the bot, quote it, or call it by name are handed to the agent immediately. The rest of the small talk is handed over together once the group has been quiet for a few seconds, and the agent decides whether to speak after reading the batch. This is also part of why, back at the beginning, it waited until everyone had finished correcting their locations before posting the map.</P>

      <P>Also, I didn&apos;t want to throw away the plugin ecosystem AstrBot has built up over the years (game stats, gacha history and so on), so I wrote <A href={MCP_BRIDGE_URL}>astrbot_plugin_mcp_bridge</A>. NapCat is connected to both AstrBot and SuperAgent. Once this MCP tool is added to SuperAgent&apos;s harness, group members don&apos;t need to remember any commands. They just say what they want in plain language, the agent works out which plugin to use, and the bridge hands the command to AstrBot to run as that group member, with AstrBot posting the result back to the group. For example, I said &quot;bot post my ███ gacha history&quot; in the group, it reacted with a 👌, and AstrBot posted the gacha history. So the old stats plugins on AstrBot all still work. There&apos;s just no command prefix to remember anymore, since the agent calls them as tools.</P>

      <P>With that, we have a flexible and smart-enough group chat agent. It runs well on both QQ and Feishu, it&apos;s usable at work, and it can plug into an existing ecosystem of entertainment plugins.</P>

      <P>OpenViking SuperAgent is currently in preview. The code and docs live in the <A href={SUPERAGENT_URL}>feat/vika branch</A> of the OpenViking repo, the <A href={INSTALL_URL}>install guide</A> will get you started, and a stable release on the main branch is coming soon.</P>

      <P><Small>This post is also published on my <A href="https://t0saki.com/en/posts/openviking-superagent-group-bot/">personal blog</A>.</Small></P>
    </Article>
  );
}

function SuperAgentGroupBotPost({ lang }) {
  return lang === 'zh' ? <ChineseArticle /> : <EnglishArticle />;
}

export default {
  id: SLUG,
  Component: SuperAgentGroupBotPost,
  meta: {
    title: {
      en: 'Building a Good Group Chat Bot with OpenViking SuperAgent',
      zh: 'OpenViking SuperAgent 如何制作一个好用的群聊 Bot',
    },
    description: {
      en: "Group chat bots went from command scripts to ChatGPT to agents and kept getting better at talking, yet whether a given line should be said was never the bot's own call. How OpenViking SuperAgent hands that decision to the model, and how I hooked it into a QQ group.",
      zh: '群聊 Bot 从命令脚本到 ChatGPT 再到 Agent，越来越会说话，但“这句话该不该说”一直不由它自己决定。这篇讲 OpenViking SuperAgent 怎么把这件事交给模型，以及我是怎么把它接进 QQ 群的。',
    },
    cover: COVER,
    cardCover: CARD_COVER,
    publishedAt: '2026-11-20',
    readingTime: { en: 28, zh: 22 },
    category: { zh: '实践', en: 'Field Notes' },
    tags: ['openviking', 'superagent', 'agents', 'memory'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [{ name: 'tosaki', github: 't0saki', role: { en: 'Engineer', zh: '工程师' } }],
  },
};
