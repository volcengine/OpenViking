# 数据组织指引

本指引规定 OpenViking 中 Resource 的目录组织方式。目录用于确定可见范围、检索范围和协作边界。

## 上下文类型

| 类型 | 内容 | 写入与组织方式 |
| --- | --- | --- |
| Resource | 文档、网站、代码仓库、数据集及编译后的知识库 | 通过 `add_resource`、`ov add-resource` 或 `ov compile` 写入，按本指引组织 |
| Memory | 从交互中提取的偏好、事实、事件和经验 | 通过 session commit 等记忆抽取流程写入，由 memory schema 和抽取系统组织 |
| Skill | 可重复执行的方法、约束、脚本和配套资料 | 通过 `add_skill` 或 `ov add-skill` 安装 |

本指引的目录约定仅适用于 Resource。不要在 `memories/` 下套用 Resource 目录结构。

## 可见范围

| 位置 | 可见范围 | 用途 |
| --- | --- | --- |
| `viking://resources/` | 当前 account 共享 | 多个身份共同使用的资料；细粒度权限由 ACL 控制 |
| `viking://~/resources/` | 当前认证身份私有 | 当前身份独立使用的资料 |
| `viking://~/peers/<peer>/resources/` | 当前身份下的指定 peer | 与稳定交互对象关联的资料 |

OpenViking `user` 是经过认证的上下文所有者，可以代表人、Agent、应用或服务身份。`viking://~` 始终指向当前请求身份自己的 user 空间。

`account_id` 已构成团队共享边界，无需再增加 `teams/<team>/`。密码、令牌、私钥等秘密不得作为普通 Resource 上传。

## 目录约定

`projects/<project>/` 是可选的协作容器。仅当项目名称稳定，且多个身份需要共享、检索和维护同一批上下文时使用。项目目录不替代 account 或 ACL 的可见性控制。

其余分类目录可以直接位于 Resource 根目录，也可以位于一个项目目录下并按需创建，不要求每个空间具备完整分类，只作为一种约定，避免不同的 Agent 由于理解不一致创建出诸如 `doc`, `docs`, `documents` 甚至 `pdfs` 这样的重复目录导致信息沟通不畅。我们在下表约定一些常用的目录名命名，其完整路径如 `viking://resources/repos`。

| 目录 | 内容 | 边界 |
| --- | --- | --- |
| `repos/` | 完整代码仓库 | 保留仓库内部结构；不要按编程语言拆分 |
| `docs/` | 手册、规范、产品文档、Office 文档、论文、预印本及其他文档快照 | 复现代码和独立数据集分别放入 `repos/` 与 `datasets/` |
| `sites/` | 以 URL、域名或 Feed 为来源，并按网页树导入或更新的内容 | 单个离线文档放入 `docs/` |
| `wiki/` | 经归纳、去重、重组并建立导航或链接的知识库 | 用作 LLM Wiki 等 `ov compile` 任务的稳定输出；原始来源保留在来源目录 |
| `datasets/` | 表格、语料、评测集、结构化归档、观测数据及训练或分析数据 | 点云、GIS 等观测数据属于数据集 |
| `media/` | 供直接观看或收听的图片、音频和视频成品 | 可编辑源文件和工程产物放入 `artifacts/` |
| `artifacts/` | 进入编辑、制造、渲染或运行流程的非文档产物 | 包括图纸、CAD、3D 模型、场景、视觉源文件、模板和品牌素材 |
| `items/` | 以商品、产品、SKU 或其他业务对象为粒度的实体集合 | 批量表格和训练数据放入 `datasets/` |
| `people/` | 以人物、客户、联系人或其他身份为粒度的实体集合 | 可见范围和敏感信息仍由 user 空间、account 与 ACL 控制 |
| `inbox/` | 当前身份尚未归类的 Resource | 仅建议位于 `viking://~/resources/`，不作为 account 共享空间的长期分类 |
| `projects/<project>/` | 项目协作上下文 | 可选容器；确有项目需要时，在其下继续使用本表中的分类目录 |

`items/` 和 `people/` 是实体集合目录的示例，不是保留名称。Agent 可以根据领域使用 `companies/`、`positions/` 或其他明确的复数名词。此类目录当前仍是普通 Resource 目录，用于与后续 Item 检索能力对齐；当前版本不提供专门的 Item 检索语义。

时间序列资料（日报、邮件归档、定期报告等）在分类目录下按日期分层即可，不要再为日期新建分类。URI 中可以直接使用服务端路径变量，例如 `viking://resources/docs/{calendar:ym}/` 会渲染为 `docs/2026/05/`；常用变量包括 `{calendar:today}`、`{calendar:ym}`、`{calendar:yq}`、`{calendar:yw}`，CLI、SDK 和 MCP 原样传递模板，由服务端解析。详见 [Viking URI 路径变量](../concepts/04-viking-uri.md#路径变量)。

目录名称不代表格式支持情况。导入前应检查[资源管理支持的格式](../api/02-resources.md#资源类型)。`artifacts/` 中尚未支持的文件应通过已启用的对象存储 Connector 保存，或等待相应的原始文件存储与解析能力。

## 添加和更新参数说明

1. 一个逻辑来源只保留一个稳定根目录，添加前可使用 `list(ls)` 或 `stat` 检查目标是否存在。
2. 新增来源使用建议使用 `parent` 指定其父目录位置，同步固定来源或希望直接指定目标目录则使用精确的 `to`，一般建议用 `parent`。
3. 预期父目录不存在时，CLI 使用 `--parent-auto-create`，SDK 和 API 使用 `create_parent=true`；MCP 的 `parent` 会自动创建目录。
4. 可重复读取且会变化的远程来源使用 watch 机制，避免手动重复导入副本。
5. `reason` 应说明资源添加意图和用途，`tags` 仅在请求或系统规范明确要求时添加。
6. 导入完成后返回最终 URI、可见范围和任务状态；处理完成后再检索。

Account 管理者可以通过 `user_config_defaults.add_targets.resource_uri` 设置默认添加目录；身份级 `add_targets.resource_uri` 可以覆盖该默认值。显式传入的 `to` 或 `parent` 始终优先。

## Agent 归档规则

1. 先区分 Resource、Memory 和 Skill。
2. 沿用目标空间已有的目录规范；存在 `RESOURCE_POLICY.md` 时优先遵循。
3. 有明确协作项目时使用 `projects/<project>/`，否则按来源或形态归档。
4. 无法确定可见范围时，写入当前身份的 `viking://~/resources/inbox/`，并返回最终位置。
5. 实体集合目录按领域命名，不创建无依据的实体分类。
6. 目标已有同一逻辑来源，或共享范围无法确定时，先确认再写入。
