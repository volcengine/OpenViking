# 知识共享

个人记忆帮助 Agent 延续用户的工作背景。项目约定和可复用经验需要被同事使用时，可以由记忆所有者整理出共享内容，发布到团队资料目录，再由其他成员的 Agent 检索、阅读和引用。

本页以 Alice 向 Bob 分享海岬项目评审约定为例，完成 **选择记忆 → 整理内容 → 授权发布 → Bob 检索并使用 → 后续更新**，并说明跨团队的处理方式。

## 选择共享方式

| 场景 | 操作方式 |
| --- | --- |
| 同一 Account 内共享资料 | 发布到 `viking://resources/...`，通过 ACL 授权用户或用户组 |
| 同一 Account 内共享个人经验 | 所有者选择记忆内容，整理成共享资料，再授权发布 |
| 不同 Account 之间交付知识 | 来源方整理可交付内容，目标方使用自己的凭据导入，并配置目标权限 |
| 多人共同积累长期团队记忆 | 由应用编排知识选择、发布和更新；当前没有跨 User 私有记忆自动合并的共享开关 |

个人记忆目录和 Peer 记忆目录不接受共享资料 ACL。发布后的内容作为共享资料维护，与原始记忆分别保存。

## 准备工作

先完成[认证](17-authentication.md)，准备普通用户 Alice、Bob，以及各自的 CLI 连接。按[访问控制](21-access-control.md)开启 `acme` 的 ACL，并创建 `viking://resources/team-knowledge`：Alice 为 `manage`，Bob 为 `read`。如果已体验撤销授权，由 Alice 先恢复 Bob 的 `read`。

Alice 应已有一份准备分享的项目记忆。首次体验可在 Alice 连接下完成[记忆使用](09-memory.md)中的“保存项目背景”和“查看生成的记忆”，获得海岬项目约定；本页不依赖 VikingBot，能执行 CLI 的 Agent 即可完成最后的问答。

导入、检索和 Agent 回答会使用相应模型。每位成员使用自己的凭据；共享流程由以下角色分工完成：

| 角色 | 负责的操作 |
| --- | --- |
| 团队管理员 | 开启 ACL、准备共享目录和初始权限 |
| Alice 或她的 Agent | 读取 Alice 的记忆、整理草稿、发布和维护 |
| Bob 或他的 Agent | 检索已授权资料、读取原文并用于任务 |

## Alice 选择记忆

在 Alice 的终端或 Agent 中使用她自己的连接：

```bash
ov config switch acme-alice
ov find "海岬项目的评审约定" --uri viking://~/memories
```

读取相关结果，确认哪些内容适合分享：

```bash
ov read "<记忆 URI>"
```

可以把下面的任务交给 Alice 的 Agent：

```text
请从我的海岬项目记忆中整理可以共享给 Bob 的评审约定。
读取原文，区分已确认的要求和待确定事项，生成 Markdown 草稿。
草稿附上供我核对的来源，先让我确认，再发布到团队资料目录。
```

由 Alice 确认要共享的内容。对外发布的正文应包含可理解的事实，或引用 Bob 有权读取的来源；Alice 的私有记忆 URI 可以留在个人核对记录中。

## Alice 整理草稿

如果使用前述海岬样例，核对记忆后，在本机创建 `cape-review.md`：

```markdown
# 海岬项目评审约定

## 评审要求

- 每次评审讨论两项风险。
- 摘要使用中文三条要点。
- 交付文件统一命名为 cape-review.csv。

## 待补充信息

- 本次评审的具体风险尚未提供。

## 维护信息

- 维护人：Alice
- 适用范围：海岬项目评审
```

这是与样例输入对应的共享正文。使用真实项目时，以读到的记忆和 Alice 确认的内容替换，并保留适用范围与待确定事项。

## Alice 发布资料

根据资料所在位置选择一种发布方式：已有资料可以在服务端直接复制；本机新整理的草稿可以使用 `add-resource` 导入。两种方式都使用 Alice 的连接，并发布到已为 Bob 授权的目录。

### 复制已有资料

如果待分享的内容已保存在 Alice 的个人资料目录，确认正文和引用适合共享后，可以直接复制，无需下载到本地。以下示例假设源文件为 `viking://~/resources/cape-review/cape-review.md`；执行时替换为实际 URI。

Alice 需要能够读取源资料，并对目标目录拥有 `write` 或 `manage` 权限。先在 `team-knowledge` 下创建一个新的发布目录，再复制文件：

```bash
ov config switch acme-alice
ov read viking://~/resources/cape-review/cape-review.md
ov mkdir viking://resources/team-knowledge/cape-review
ov cp viking://~/resources/cape-review/cape-review.md \
  viking://resources/team-knowledge/cape-review/cape-review.md -o json
ov read viking://resources/team-knowledge/cape-review/cape-review.md
```

这里的 `~` 指向当前连接用户 Alice。新建的共享资料继承目标目录权限，Bob 可以按后文步骤检索和读取；Alice 的原始资料仍保存在个人目录中。

如果整个源目录都适合共享，可以用下面的命令替代上面的 `mkdir` 和单文件 `cp`。目标父目录 `team-knowledge` 需已存在，目标 URI 就是复制后的目录位置：

```bash
ov cp -r viking://~/resources/cape-review \
  viking://resources/team-knowledge/cape-review -o json
ov tree viking://resources/team-knowledge/cape-review
```

复制会同时复制已有向量记录；目标父目录的摘要可能异步刷新。响应中的 `semantic_status: queued` 表示摘要刷新已入队，`failed` 表示需要查看 `semantic_error`。发布后由 Bob 检索并读取正文，确认可用。完整行为见[文件系统 API](../api/03-filesystem.md#cp)。

复制生成独立副本，源资料后续修改不会自动同步。再次复制同名文件会覆盖目标正文，目录复制会合并到已有目录并保留目标独有文件。首次发布使用新路径；后续更新前核对目标内容，并检查正文中引用的私有 URI 是否需要替换。

### 导入本地草稿

如果使用前面整理的本地 Markdown 草稿，在文件所在目录执行，使用一个新的发布路径：

```bash
ov add-resource ./cape-review.md \
  --to viking://resources/team-knowledge/cape-review --wait
```

发布目录继承 `team-knowledge` 的权限。处理完成后，展开目录并读取实际正文 URI：

```bash
ov tree viking://resources/team-knowledge/cape-review
ov read "<共享正文 URI>"
```

记录共享正文 URI，供 Bob 阅读及后续更新。若等待超时，保存返回的任务 ID，通过 `ov task status <task_id>` 检查；确认完成后再让 Bob 使用。

## Bob 检索并使用

Bob 使用自己的连接检索：

```bash
ov config switch acme-bob
ov find "海岬项目评审应采用什么格式和交付文件名？" \
  --uri viking://resources/team-knowledge/cape-review
```

读取结果中的共享正文 URI，核对内容。随后在 Bob 的 Agent 中发送：

```text
请使用我的 ov CLI 连接，检索 viking://resources/team-knowledge/cape-review，
读取相关原文，按海岬项目约定准备一份评审提纲。
列出摘要格式、风险讨论数量和交付文件名，并引用共享资料的完整 URI。
具体风险未提供时，请保留待填写项。
```

Agent 需要能够执行 CLI，并在检索流程中包含这个共享目录。资料授权成功后，仍需由 Agent 检索和读取，才能把内容用于回答。

| 核对项 | 预期结果 |
| --- | --- |
| 访问身份 | Bob 的 `ov health -o json` 显示 `acme` / `bob` |
| 来源 | 命中 `team-knowledge/cape-review` 下的共享资料 |
| 回答 | 两项风险、三条中文摘要、`cape-review.csv` |
| 未知信息 | 具体风险保留待填写，或明确说明未提供 |
| 私有边界 | Bob 仍不能读取 Alice 原始的私有记忆目录 |

## 维护共享内容

默认由 Alice 维护。通过服务端复制发布的资料，可以在源文件更新并确认后，再次执行同一条文件复制命令；这会覆盖共享副本的正文，并保留目标权限。

通过本地草稿发布时，修改本机 `cape-review.md` 后，可以更新前面记录的共享正文 URI：

```bash
ov config switch acme-alice
ov write "<共享正文 URI>" --from-file ./cape-review.md --mode replace --wait
```

这会替换该文件的完整正文，并重新处理；发布前核对本地文件包含需要保留的全部内容。完成后，Bob 重新检索、读取，确认使用的是更新后的约定。

Alice 的个人记忆后续发生变化时，共享资料不会自动同步。需要共同维护时，Alice 可以授予 Bob `write`；合作结束时可撤销授权，方法见[访问控制](21-access-control.md)。Bob 已经下载或复制的内容需要另外管理。

## 跨团队交付

如果 Bob 属于另一个 Account，例如 `beta`，acme 的 ACL 无法直接授权 beta 的用户。`user:bob` 只表示当前 Account 内的 Bob。

可由 Alice 将已确认的 `cape-review.md` 通过团队认可的渠道交给 beta 的资料维护人。beta 管理员先按[访问控制](21-access-control.md)在 **beta** 内开启 ACL，创建受限共享目录并授予目标成员权限；把示例 Account ID 和用户 ID 换成 beta 的实际成员。

获得目标目录写权限后，beta 的维护人使用自己的连接，在收到文件的目录中导入：

```bash
ov config switch beta-admin
ov add-resource ./cape-review.md \
  --to viking://resources/team-knowledge/cape-review --wait
```

这里的 `beta-admin` 是 [Account](18-accounts.md) 中创建的目标团队连接。目标目录、权限和成员由 beta 独立管理；导入后由目标成员检索并核对原文。来源与目标各自保存一份资料，后续更新需要再次交付或由应用同步。

批量迁移可参考[导入导出](15-transfer.md)，完成后重新核对目标身份、目录权限和正文中的来源链接。如果多个小组需要长期共同维护同一份资料，可以把协作成员放在同一 Account 下，通过用户组与 ACL 管理不同范围。

## 下一步

用一份真实项目约定重复这个流程，确认 Bob 的 Agent 能找到共享内容并用于任务。更完整的多资料问答流程见[资料检索](08-resources.md)。
