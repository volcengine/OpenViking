import React from 'react';
import {
  Article, Lead, P, H2, H3, Pre, Callout,
  Li, Ul, Table, Figure, Strong,
} from '../../blog-components';

const LLM_PATH = '/post/openviking-user-peer-model/llm.txt';
const IMAGE_BASE = '/post/openviking-user-peer-model/images';
const COVER = '/assets/covers/openviking-user-peer-model.png';

const SUPPORT_BOT_MODEL = `account = acme
user    = support-bot
peer    = customer-alice
peer    = customer-bob`;

const TREE_MODEL = `account
└── user
    ├── memories
    ├── resources
    ├── skills
    ├── sessions
    └── peers
        ├── customer-alice
        │   ├── memories
        │   └── resources
        └── customer-bob
            ├── memories
            └── resources`;

const SUPPORT_BOT_PATHS = `user = support-bot

support-bot/memories
support-bot/resources
support-bot/sessions

support-bot/peers/customer-alice/memories
support-bot/peers/customer-bob/memories`;

const PERSONAL_MODEL = `user = alice
peer = coding-agent
peer = life-agent`;

const PEER_URIS = `viking://user/{user_id}/peers/{peer_id}/memories
viking://user/{user_id}/peers/{peer_id}/resources`;

const SAFE_PEER_IDS = `customer-alice
telegram_12345
web-visitor-abc`;

const BAD_PEER_ID = `a/b`;

function UserPeerPost({ t }) {
  const T = t;

  const ownerClient = T({
    en: `import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<support-bot-user-key>",
)
client.initialize()`,
    zh: `import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<support-bot-user-key>",
)
client.initialize()`,
  });

  const sessionExample = T({
    en: `session = client.create_session(
    memory_policy={
        "self": {"enabled": True},
        "peer": {"enabled": True},
    }
)
session_id = session["session_id"]

client.add_message(
    session_id,
    role="user",
    content="Please issue invoices under Volcano Engine.",
    peer_id="customer-alice",
)

client.add_message(
    session_id,
    role="assistant",
    content="Got it. I will remember this invoice preference.",
    peer_id="customer-alice",
)

client.commit_session(session_id)`,
    zh: `session = client.create_session(
    memory_policy={
        "self": {"enabled": True},
        "peer": {"enabled": True},
    }
)
session_id = session["session_id"]

client.add_message(
    session_id,
    role="user",
    content="我的发票抬头需要开成火山引擎。",
    peer_id="customer-alice",
)

client.add_message(
    session_id,
    role="assistant",
    content="收到，我会记住这个发票偏好。",
    peer_id="customer-alice",
)

client.commit_session(session_id)`,
  });

  const peerViewExample = T({
    en: `alice_view = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<support-bot-user-key>",
    actor_peer_id="customer-alice",
)
alice_view.initialize()

results = alice_view.find("invoice preference")`,
    zh: `alice_view = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<support-bot-user-key>",
    actor_peer_id="customer-alice",
)
alice_view.initialize()

results = alice_view.find("发票偏好")`,
  });

  return (
    <Article>
      <Lead>{T({
        en: 'Long-term context systems first need to answer a simple ownership question: which OpenViking user owns this data space, and which object is merely interacting with it right now?',
        zh: '长期上下文系统里，最先要回答的不是“记什么”，而是“这片数据属于谁”。User / Peer 模型就是 OpenViking 对这个问题的重新建模。',
      })}</Lead>

      <P dropCap>{T({
        en: 'In single-user use, the answer feels obvious. Alice uses an assistant, so Alice is the data owner. Memories, resources, skills, and sessions all revolve around Alice.',
        zh: '如果只是单人使用，这个问题很简单：Alice 使用一个助手，Alice 就是数据 owner，记忆、资源、技能、会话都围绕 Alice 展开。',
      })}</P>
      <P>{T({
        en: 'Real agent applications are less tidy. A support bot may serve many customers. A bot service may talk with many group members. An IDE plugin may represent one fixed tool instance while interacting with different people, projects, and runtime agents every day.',
        zh: '但一到真实的 Agent 应用，问题就变复杂了。一个客服智能体可能同时服务很多客户，一个机器人服务可能接入很多群成员，一个 IDE 插件可能代表一个固定工具实例，却每天和不同项目、不同用户交互。',
      })}</P>
      <P>{T({
        en: 'That is why OpenViking should not treat every current speaker as an OpenViking user. In OpenViking, a user is a service-layer data subject. It may be a natural person, or it may be an agent, bot service, support desk, or fixed integration instance.',
        zh: '此时我们不能简单地把“正在说话的人”都当成 OpenViking 的 user。因为在 OpenViking 里，user 不是现实世界里的“自然人说话者”，而是 OV 服务层的用户，也就是这片数据空间的数据主体。',
      })}</P>

      <Figure
        src={`${IMAGE_BASE}/figure-01-user-data-subject.png`}
        frame="plain"
        size="lg"
        alt={T({
          en: 'User as the data subject, with memories, resources, sessions, and several peers around it.',
          zh: 'User 是数据主体，下面有 memories、resources、sessions，右侧连接多个 peer。',
        })}
        caption={T({
          en: 'The user owns the data space. Peers are interaction objects inside that user boundary.',
          zh: 'User 拥有数据空间；Peer 是这个 user 边界内的交互对象。',
        })}
      />

      <H2>{T({ en: 'User is not necessarily a person', zh: 'User 不一定是人' })}</H2>
      <P>{T({
        en: 'In OpenViking, user means the owner of a data space. That space can contain memories, private resources, installed skills, and sessions. A user can therefore be a natural person, but it can also be a service identity such as a support bot or workbench.',
        zh: '在 OpenViking 里，user 表示一片数据空间的 owner。这片空间里可以有 memories、resources、skills 和 sessions。因此，user 更准确的理解是“数据主体”。它可以是自然人，也可以是智能体、机器人服务、客服工作台，甚至一个固定接入实例。',
      })}</P>
      <P>{T({
        en: 'The analogy is close to the difference between a natural person and a legal person: both can be subjects. Here the subject is not about legal liability; it is an engineering boundary for data ownership.',
        zh: '这有点像“自然人”和“法人”都可以成为主体：这里不是法律意义上的责任主体，而是数据意义上的 owner。',
      })}</P>
      <P>{T({
        en: 'A peer is the object this data subject is interacting with. In a support scenario, the service bot can be the OpenViking user while each customer becomes a peer under that user.',
        zh: 'peer 则是这个数据主体正在交互的对象。客服场景里，客服智能体可以是 OpenViking user，每个客户则是这个 user 下面的 peer。',
      })}</P>
      <Pre lang="text" filename="support-bot-model" lineNumbers={false}>{SUPPORT_BOT_MODEL}</Pre>
      <P>{T({
        en: 'Here support-bot owns the OpenViking data space. Alice and Bob are the objects it serves. The bot may need to remember Alice’s preferences and Bob’s historical issues, but Alice and Bob do not have to become first-class OpenViking authenticated users with separate user keys.',
        zh: '这里 support-bot 是 OpenViking 的数据 owner。Alice 和 Bob 是它服务的对象。它需要记住 Alice 的偏好、Bob 的历史问题，但不一定需要把 Alice 和 Bob 都注册成 OpenViking user，也不一定要给他们各自分发 user key。',
      })}</P>

      <H2>{T({ en: 'Why the old User / Agent model felt awkward', zh: '旧的 User / Agent 为什么别扭' })}</H2>
      <P>{T({
        en: 'Earlier OpenViking usage was closer to an account / user / agent mental model. The user_id was the formal data identity and was usually bound by the user key. The agent_id was easier to pass as runtime context and could distinguish assistants, tools, or environments.',
        zh: '早期 OpenViking 的公开心智更接近 account / user / agent。user_id 是正式数据身份，通常由 user key 绑定；agent_id 更像运行时上下文，用来区分不同 assistant、不同工具实例、不同运行环境。',
      })}</P>
      <P>{T({
        en: 'That worked for one person using several agents. It became awkward when one agent service needed to serve many external people or objects and persist personal memory for each of them.',
        zh: '这在“一个人使用多个 agent”的场景里可以工作。但在“一套智能体系统服务很多外部对象，并为每个对象沉淀个人上下文”的场景里，就会出现两条都不舒服的路。',
      })}</P>

      <Figure
        src={`${IMAGE_BASE}/figure-02-old-user-agent-pain.png`}
        frame="plain"
        size="lg"
        alt={T({
          en: 'The old model either creates many users and keys or puts external people into agent_id.',
          zh: '旧模型要么创建很多 user 和 key，要么把外部对象塞进 agent_id。',
        })}
        caption={T({
          en: 'One-to-many agent services need a data-owner dimension and an interaction-object dimension.',
          zh: '一对多 Agent 服务需要把数据 owner 和交互对象拆成两个维度。',
        })}
      />

      <Table
        headers={[
          T({ en: 'Old path', zh: '旧路径' }),
          T({ en: 'Why it hurts', zh: '为什么别扭' }),
        ]}
        rows={[
          [
            T({ en: 'Register every external object as an OpenViking user', zh: '把每个外部对象都注册成 OpenViking user' }),
            T({ en: 'The semantics may look clean, but every user has its own key. Platforms now have to manage registration, distribution, rotation, delegated access, and permission boundaries for objects that may only be served participants.', zh: '语义上像是“每个客户拥有自己的数据”，但工程上会带来很多额外负担：每个 user 都有独立 key，平台要管理注册、分发、轮换、代管和权限边界。' }),
          ],
          [
            T({ en: 'Put the external object into agent_id', zh: '把外部对象塞进 agent_id' }),
            T({ en: 'It is lightweight to pass, but the shape is wrong. Customers and group members are not agents, and the data owner, interaction object, and retrieval scope become mixed together.', zh: '实现上更轻，因为 agent 上下文比较容易随请求指定。但语义反了：客户不是 agent，群成员也不是 agent；数据 owner、交互对象、检索范围也会混在一起。' }),
          ],
        ]}
      />

      <Callout type="info" title={T({ en: 'The rule', zh: '核心规则' })}>
        <P>{T({
          en: 'Do not ask one identifier to answer two questions. Ask separately: who owns this data space, and who is the current interaction object?',
          zh: '不要让一个标识同时回答两个问题。应该分开问：谁拥有这片数据空间？这次是在和谁交互？',
        })}</P>
      </Callout>

      <H2>{T({ en: 'The new model: user owns data, peer describes the interaction object', zh: '新模型：User 是数据 owner，Peer 是交互对象' })}</H2>
      <P>{T({
        en: 'User / Peer turns the boundary into one simple relationship. Account remains the tenant or workspace boundary. User is the data owner inside OpenViking. Peer is an interaction object under that user.',
        zh: 'User / Peer 模型把 OpenViking 的数据边界收敛成更简单的一层关系：account 是租户或工作区边界，user 是 OV 服务里的数据 owner，peer 是某个 user 下的交互对象。',
      })}</P>
      <Pre lang="text" filename="user-peer-tree" lineNumbers={false}>{TREE_MODEL}</Pre>
      <P>{T({
        en: 'A peer does not change the tenant, authentication identity, or user boundary. It narrows the content scope inside the current user’s data space.',
        zh: 'peer 不改变租户，也不改变认证身份。它只在当前 user 的数据空间里表达一对多对象。',
      })}</P>

      <Figure
        src={`${IMAGE_BASE}/figure-03-support-bot-peer-flow.png`}
        frame="plain"
        size="lg"
        alt={T({
          en: 'A support bot user receiving sessions from customer A and customer B and writing separate peer memory.',
          zh: '客服智能体作为 user，customer A 和 customer B 通过 peer_id 写入不同 peer memory。',
        })}
        caption={T({
          en: 'A support bot can own one data space while keeping each customer’s context isolated by peer_id.',
          zh: '客服智能体可以拥有一个数据空间，同时用 peer_id 隔离每个客户的上下文。',
        })}
      />

      <H3>{T({ en: 'Support bot as user, customers as peers', zh: '客服智能体作为 user，客户作为 peer' })}</H3>
      <Pre lang="text" filename="support-bot-paths" lineNumbers={false}>{SUPPORT_BOT_PATHS}</Pre>
      <P>{T({
        en: 'Alice’s invoice preference, contact style, and historical requests can be stored under customer-alice. Bob’s context is stored under customer-bob. Both still belong to the support-bot data owner.',
        zh: 'Alice 的偏好、联系方式、历史需求，可以沉淀到 customer-alice 这个 peer 下。Bob 的上下文则沉淀到 customer-bob 下。它们都属于 support-bot 这个数据 owner 的数据空间。',
      })}</P>

      <H3>{T({ en: 'A natural person can still be the user', zh: '自然人仍然可以是 user' })}</H3>
      <Pre lang="text" filename="personal-assistant-model" lineNumbers={false}>{PERSONAL_MODEL}</Pre>
      <P>{T({
        en: 'If Alice owns the data space, different agents can be represented as peers under Alice. The rule is not “user must be a person” or “peer must be an agent.” The rule is: identify the data owner first, then identify the interaction object.',
        zh: '如果数据 owner 是 Alice，那么不同 agent 可以作为 Alice 下面的 peer，分别保留和 Alice 交互时的上下文。关键不是“user 必须是人，peer 必须是 agent”，而是看谁拥有数据，谁只是当前交互对象。',
      })}</P>

      <H2>{T({ en: 'How developers use it', zh: '开发者怎么用' })}</H2>
      <P>{T({
        en: 'The common path has three steps: use a user key to establish the data owner, attach peer_id to session messages, and use actor_peer_id when you need a peer-restricted retrieval or filesystem view.',
        zh: '最常见的接入方式是三步：用 user key 确定当前数据 owner，写会话消息时传 peer_id，需要检索某个 peer 视图时使用 actor_peer_id。',
      })}</P>

      <Figure
        src={`${IMAGE_BASE}/figure-04-api-quickstart.png`}
        frame="plain"
        size="lg"
        alt={T({
          en: 'API quickstart: user key, peer_id on messages, actor_peer_id for retrieval.',
          zh: 'User/Peer API 速查：user key、消息 peer_id、检索 actor_peer_id。',
        })}
        caption={T({
          en: 'The user key selects the owner. peer_id marks captured messages. actor_peer_id filters peer-scoped data operations.',
          zh: 'user key 选择数据 owner；peer_id 标记消息归属；actor_peer_id 过滤 peer 视图。',
        })}
      />

      <H3>{T({ en: '1. Use a user key to select the owner', zh: '1. 用 user key 确定数据 owner' })}</H3>
      <Pre lang="python" filename="owner-client.py">{ownerClient}</Pre>
      <P>{T({
        en: 'The key can belong to a natural person or to an agent service. The server resolves the current account and user from that key, and subsequent data operations happen in that user space.',
        zh: '这里的 user key 可以属于自然人，也可以属于智能体或机器人服务。服务端从 key 解析出当前 account/user，后续数据都在这个 user 空间下发生。',
      })}</P>

      <H3>{T({ en: '2. Attach peer_id to session messages', zh: '2. 写消息时传 peer_id' })}</H3>
      <Pre lang="python" filename="session-peer-memory.py">{sessionExample}</Pre>
      <P>{T({
        en: 'The peer target is enabled explicitly through memory_policy. During commit, OpenViking can write relevant peer memory under the current user’s peer path.',
        zh: 'peer 目标需要通过 memory_policy 显式开启。commit 时，OpenViking 可以把相关个人上下文写到当前 user 的 peer 路径下。',
      })}</P>
      <Pre lang="text" filename="peer-uris" lineNumbers={false}>{PEER_URIS}</Pre>

      <H3>{T({ en: '3. Use actor_peer_id for a peer view', zh: '3. 用 actor_peer_id 读取某个 peer 视图' })}</H3>
      <Pre lang="python" filename="peer-view.py">{peerViewExample}</Pre>
      <P>{T({
        en: 'actor_peer_id filters the current user’s peer collection to customer-alice. It does not authenticate the request as Alice, and it does not switch account or user.',
        zh: 'actor_peer_id 会把当前 user 下的 peer 集合过滤到 customer-alice。它不会把请求变成 Alice 的认证身份，也不会切换 account 或 user。',
      })}</P>

      <H3>{T({ en: 'Keep peer_id path-safe', zh: 'peer_id 要是安全的单段标识' })}</H3>
      <P>{T({
        en: 'Use a stable single path segment as peer_id. Values containing path separators are rejected.',
        zh: '实际使用中，peer_id 必须是安全的单段标识。包含路径分隔符的值会被拒绝。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Good examples', zh: '推荐形式' }),
          T({ en: 'Rejected shape', zh: '会被拒绝的形式' }),
        ]}
        rows={[
          [<Pre lang="text" lineNumbers={false}>{SAFE_PEER_IDS}</Pre>, <Pre lang="text" lineNumbers={false}>{BAD_PEER_ID}</Pre>],
        ]}
      />

      <H2>{T({ en: 'What this changes for platforms and plugins', zh: '对平台和插件意味着什么' })}</H2>
      <P>{T({
        en: 'The biggest change is that platform integrations no longer need to debate whether every external object must become an OpenViking user.',
        zh: 'User / Peer 模型最大的变化，是让平台型接入不再纠结“外部对象到底是不是 OpenViking user”。',
      })}</P>
      <Table
        headers={[
          T({ en: 'Scenario', zh: '场景' }),
          T({ en: 'Recommended shape', zh: '推荐建模' }),
        ]}
        rows={[
          [
            T({ en: 'Support bot', zh: '客服 Bot' }),
            T({ en: 'OpenViking user = support-bot; customers = peers.', zh: 'OpenViking user = support-bot；外部客户 = peer。' }),
          ],
          [
            T({ en: 'Developer tool plugin', zh: '开发工具插件' }),
            T({ en: 'OpenViking user = a natural person or fixed tool instance; runtime speakers or agents = peers.', zh: 'OpenViking user = 某个自然人或固定工具实例；运行时对象 = peer。' }),
          ],
          [
            T({ en: 'Messaging bot', zh: '群聊或消息机器人' }),
            T({ en: 'OpenViking user = bot service; sender or group member = peer_id.', zh: 'OpenViking user = 机器人服务；sender 或群成员 = peer_id。' }),
          ],
        ]}
      />
      <Ul>
        <Li>{T({ en: 'The data owner becomes more stable.', zh: '数据 owner 更稳定。' })}</Li>
        <Li>{T({ en: 'Authentication and key management stay smaller.', zh: '认证和 key 管理更少。' })}</Li>
        <Li>{T({ en: 'One-to-many participants can be isolated naturally.', zh: '一对多对象可以自然隔离。' })}</Li>
        <Li>{T({ en: 'Retrieval and filesystem views follow the same peer filter.', zh: '检索和文件系统视图遵循同一套 peer 过滤规则。' })}</Li>
        <Li>{T({ en: 'The main data path becomes the user-scoped viking://user/... space.', zh: 'viking://user/... 成为主要数据路径。' })}</Li>
      </Ul>

      <Callout type="tip" title={T({ en: 'Mental model', zh: '心智模型' })}>
        <P>
          <Strong>{T({ en: 'user', zh: 'user' })}</Strong>
          {T({ en: ' = the OpenViking service-layer data owner. ', zh: ' = OV 服务里的数据 owner。' })}
          <Strong>{T({ en: 'peer', zh: 'peer' })}</Strong>
          {T({ en: ' = an interaction object under that owner.', zh: ' = 这个 owner 下的交互对象。' })}
        </P>
      </Callout>

      <H2>{T({ en: 'The takeaway', zh: '最后' })}</H2>
      <P>{T({
        en: 'User / Peer is not just another field on a message. It separates two responsibilities that long-term context systems must keep distinct: who owns the data, and who the current interaction is with.',
        zh: 'User / Peer 不是简单给消息加了一个字段。它真正解决的是长期上下文系统里一个更基础的问题：谁拥有数据，谁只是交互对象。',
      })}</P>
      <P>{T({
        en: 'Once that boundary is clear, memories, resources, skills, and sessions have stable ownership and can be reused safely by agents, plugins, and platforms.',
        zh: '当这个边界清楚以后，长期记忆、资源、技能和会话才有稳定的归属，也才更适合被不同 Agent、插件和平台长期复用。',
      })}</P>
    </Article>
  );
}

export default {
  id: 'openviking-user-peer-model',
  Component: UserPeerPost,
  meta: {
    title: {
      en: 'OpenViking User / Peer: Separating Data Owners From Interaction Objects',
      zh: 'OpenViking User / Peer：把数据主体和交互对象分开',
    },
    description: {
      en: 'Why OpenViking moved from a User / Agent mental model to User / Peer, and how one agent can serve many people without turning every participant into an OpenViking user.',
      zh: '为什么 OpenViking 从 User / Agent 心智走向 User / Peer，以及一个智能体如何自然服务很多人。',
    },
    cover: COVER,
    cardCover: COVER,
    publishedAt: '2026-06-15',
    readingTime: { en: 8, zh: 9 },
    category: { en: 'Engineering', zh: '工程' },
    tags: ['openviking', 'memory', 'multi-tenant', 'agents'],
    languages: ['en', 'zh'],
    llmPath: LLM_PATH,
    authors: [{ name: 'qin-ctx', github: 'qin-ctx' }],
  },
};
