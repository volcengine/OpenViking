# User

User 是 Account 内的数据归属身份。OpenViking 用它区分个人记忆、私有资料和会话，让同一团队中的 Alice 和 Bob 各自保留长期上下文，同时使用团队共享资料。

这里的 User 是访问服务时的身份，可以由应用映射到一个真实用户或独立的应用身份。多个 Agent 如果使用同一 User Key，就会使用同一个用户空间；需要各自独立的个人数据时，使用不同 User。

## 理解用户空间

Alice 的目录如下：

```text
viking://user/alice/
├── memories/    # Alice 的长期记忆
├── resources/   # Alice 的私有资料
├── skills/      # Alice 的私有技能
├── peers/       # Alice 的交互对象
└── sessions/    # Alice 的会话
```

`viking://~/` 是当前用户家目录的别名。Alice 使用时展开为 `viking://user/alice/`，Bob 使用时展开为 `viking://user/bob/`。共享资料位于同一 Account 的 `viking://resources/`。

## 准备工作

先完成[认证](17-authentication.md)，创建同属 `acme` 的普通用户 Alice 和 Bob，并保存 `acme-alice`、`acme-bob` 连接。本例创建一个空的私有目录和一个会话，不触发记忆提取。

## 创建私有内容

Alice 切换到自己的连接，核对身份并创建目录：

```bash
ov config switch acme-alice
ov health -o json
ov mkdir viking://~/resources/personal-check
ov ls viking://~/resources
```

目录列表中应出现 `personal-check`。再创建一个由 Alice 持有的会话，并关闭自动提交：

```bash
ov session new --session-id alice-user-check --no-auto-commit
ov session add-message alice-user-check --role user --content "这是 Alice 的个人项目讨论。"
ov session get alice-user-check
```

确认会话中能读到这条消息。会话消息已保存；提交后才进入记忆提取流程，后续操作见[记忆使用](09-memory.md)。

## 用 Bob 核对边界

Bob 使用自己的连接列出家目录：

```bash
ov config switch acme-bob
ov ls viking://~/resources
```

这里展示 Bob 的私有资料目录。继续尝试访问 Alice 刚创建的目录：

```bash
ov ls viking://user/alice/resources/personal-check
```

这次访问应被拒绝。相同 Account 允许团队协作，但个人空间仍按 User 划分。需要分享其中的知识时，由 Alice 将选定内容发布到共享资料区，见[知识共享](22-sharing.md)。

## User 与角色

User 决定数据属于谁，角色决定允许执行哪些管理操作：

| 用户角色 | 主要能力 |
| --- | --- |
| USER | 个人数据操作和已授权共享资料的访问 |
| ADMIN | 自己的用户空间、团队成员管理、当前 Account 共享资料的管理 |

管理员的日常数据请求仍使用自己的 User 身份。API Key 模式下，管理员不能通过修改身份请求头切换为 Alice 或 Bob。平台 ROOT 负责 Account 等平台操作，其 Root Key 不用于普通租户数据读取。

应用可以在多个会话中复用同一个用户身份，使长期记忆持续积累；会话 ID 用来区分对话，Peer 用来区分该用户下的稳定交互对象。

## 检查结果

Alice 能访问自己的目录和会话，Bob 的家目录解析到自己，并且不能读取 Alice 的私有目录。如果两人看到了同一个个人空间，先核对是否共用了同一个 Key。

## 下一步

阅读 [Peer](20-peers.md)，为同一用户面对的不同交互对象分别组织资料和记忆。用户注册、角色调整和密钥轮换见 [Admin API](../api/08-admin.md)。
