# 管理员（多租户）

Admin API 用于多租户环境下的账户、用户和用户组管理。包括工作区（account）的创建与删除、用户注册与移除、用户组成员、角色变更、API Key 重新生成。

该 API 适用于 `api_key` 和 `trusted` 两种模式下的管理链路：
- 在 `api_key` 模式下，角色始终从 API Key 推导。
- 在 `trusted` 模式下，普通请求仍然不依赖 user key 注册流程；当请求 `/api/v1/admin/*` 并携带已配置的 `root_api_key` 时，受信上游会按 ROOT 授权。

对于 `/api/v1/admin/*`，`trusted` 模式允许不携带显式身份头；也允许携带与 URL 中 account/user 匹配的目标身份头。只要部署级 `root_api_key` 校验通过，这类请求都会按 ROOT 处理。普通 trusted 数据 API 的身份和角色仍然来自 `X-OpenViking-Account` + `X-OpenViking-User`。

## 角色与权限

| 角色 | 说明 |
|------|------|
| ROOT | 系统管理员，拥有全部权限 |
| ADMIN | 工作区管理员，管理本 account 内的用户 |
| USER | 普通用户 |

| 操作 | ROOT | ADMIN | USER |
|------|------|-------|------|
| 创建/删除工作区 | Y | N | N |
| 列出工作区 | Y | N | N |
| 注册/移除用户 | Y | Y（本 account） | N |
| 管理用户组和成员 | Y | Y（本 account） | N |
| 列出 agents（已废弃，返回空列表） | Y | Y（本 account） | N |
| 重新生成 User Key | Y | Y（本 account） | N |
| 将用户提升为 ADMIN | Y | Y（本 account） | N |

## CLI `--sudo` 选项

使用 `ov` CLI 执行需要 ROOT 权限的管理操作时，可以使用 `--sudo` 选项。该选项会使用配置文件 `~/.openviking/ovcli.conf` 中的 `root_api_key` 而非普通 `api_key`。

### 配置要求

在 `~/.openviking/ovcli.conf` 中配置 `root_api_key`：

```json
{
  "url": "http://localhost:1933",
  "api_key": "alice-user-key",
  "root_api_key": "your-root-api-key",
  ...
}
```

### 支持 `--sudo` 的命令

- `ov --sudo admin` - 账户和用户管理
- `ov --sudo system` - 系统工具命令
- `ov --sudo reindex` - 重建索引
- `ov --sudo admin migrate` - legacy agent/session 迁移和 cleanup
- `ov --sudo task status/list` - 查询 root/system 后台任务，例如迁移任务

### 使用限制

- `--sudo` 仅适用于上面的命令，用于普通数据命令会报错
- 必须配置 `root_api_key` 才能使用 `--sudo`

## 用户组

用户组属于单个 account，用于通过一个 ACL principal 授权多个用户。`group_id` 由调用者创建时指定，使用与 `user_id` 相同的标识符规则，是 account 内唯一且稳定的标识；不存在单独的组名。组内只能加入当前 account 已存在的用户，不支持嵌套组。

成员关系由服务端加入每次请求的 `RequestContext.group_ids`。添加或移除成员从下一次请求开始生效，不重写资源 ACL 或 context 记录。用户被删除时会自动退出所有组；用户组必须为空才能删除。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/admin/accounts/{account_id}/groups` | 创建空组，请求体为 `{"group_id":"engineering"}` |
| GET | `/api/v1/admin/accounts/{account_id}/groups` | 列出组 |
| DELETE | `/api/v1/admin/accounts/{account_id}/groups/{group_id}` | 删除空组 |
| GET | `/api/v1/admin/accounts/{account_id}/groups/{group_id}/members` | 列出成员 |
| PUT | `/api/v1/admin/accounts/{account_id}/groups/{group_id}/members/{user_id}` | 幂等添加成员；重复调用返回 `added=true` |
| DELETE | `/api/v1/admin/accounts/{account_id}/groups/{group_id}/members/{user_id}` | 移除成员；重复调用返回 `removed=false` |

```bash
ov --sudo admin create-group acme engineering
ov --sudo admin add-group-member acme engineering alice
ov acl grant viking://resources/project-a \
  --principal group:engineering --level read
ov --sudo admin remove-group-member acme engineering alice
ov --sudo admin delete-group acme engineering
```

Python SDK 提供对应的 `admin_create_group`、`admin_list_groups`、`admin_list_group_members`、`admin_add_group_member`、`admin_remove_group_member` 和 `admin_delete_group`；Go SDK 使用相同名称的 PascalCase 方法。

## API 参考

### get_agent_evolution_status

返回调用方所属 account 的 Agent 进化实时状态。ROOT 操作已配置的默认
account，ADMIN 仅操作自己所属的 account。

**HTTP API**

```
GET /api/v1/admin/agent-evolution
```

```bash
curl http://localhost:1933/api/v1/admin/agent-evolution \
  -H "X-API-Key: <root-key>"
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "enabled": false,
    "account_id": "default"
  },
  "time": 0.1
}
```

`enabled` 优先读取
`/local/{account_id}/_system/setting.json` 中的 account 级覆盖值；未配置时使用
`server.agent_evolution.enabled`。Session commit 会实时读取生效值，无需重启。

现有更新接口名保持不变：

```http
PUT /api/v1/admin/agent-evolution
Content-Type: application/json

{"enabled": true}
```

### account_settings

ROOT 可管理任意 account，ADMIN 仅可管理自己所属的 account。通用配置接口仅允许
显式列入白名单的字段；当前允许修改 `agent_evolution.enabled` 和
`acl.enabled`。

```http
GET /api/v1/admin/accounts/{account_id}/settings
PATCH /api/v1/admin/accounts/{account_id}/settings
Content-Type: application/json

{
  "agent_evolution": {"enabled": true},
  "acl": {"enabled": true}
}
```

`acl.enabled` 默认为 `false`。关闭时，共享资源按原有规则完全共享，不执行 ACL
鉴权。开启后，账号内新增共享资源会写入 ACL，并对带 ACL 的共享资源执行鉴权；
已有且未设置 ACL 的内容不会迁移或改权。重新关闭后，已有 ACL 也不再参与访问判断。

```bash
ov --sudo admin set-account-settings acme --acl-enabled true
```

覆盖已有配置前，内核会先备份到
`/local/{account_id}/_system/setting.backup.json`。

### account_memory_templates

ROOT 可管理任意 Account；ADMIN 仅可管理自己 Account 的模板；普通 User 无权调用。
权限按管理员角色判断，不按 User 是否叫 `default` 判断。

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/v1/admin/accounts/{account_id}/memory-templates` | 列出六类开放模板、完整默认值及生效值 |
| GET | `/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}` | 查询单个模板 |
| PUT | `/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}` | 补齐并发布单个模板 |
| DELETE | `/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}` | 删除该模板覆盖，恢复部署默认值 |

内核接收原有 Memory YAML 结构对应的 JSON 对象，并在接口层强制校验以下白名单。
仅开放下列六类模板；Experience、Cases、Trajectories 等其他类型不开放查询或编辑，
不支持通过接口新增、删除或重命名 Memory Type。DELETE 仅移除自定义覆盖，不删除模板类型。

| 模板 | 可编辑项 | 用途 |
|------|----------|------|
| `profile` | `description`；`fields.content.description` | 稳定身份、背景和工作方式的抽取说明；正文内容、语言、Markdown 结构、长度和更新时间要求 |
| `events` | `description`；`fields.event_name.description`、`fields.summary.description`；`content_template` | 事件范围、原子性与排除项；名称语言、粒度和格式；摘要事实、日期和语言；Summary、时间、ChatLog 的标题、顺序和展示方式 |
| `preferences` | `description`；`fields.topic.description`、`fields.content.description` | 偏好、习惯、反感及与 Profile/Event 的边界；主题粒度、语言和命名；正文语义、条目和 Markdown 要求 |
| `entities` | `description`；`fields.category.description`、`fields.name.description`、`fields.content.description` | 实体与关系范围；分类法、语言和粒度；实体命名；卡片事实、章节、语言和长度 |
| `soul` | `description`；`fields.core_truths.description`、`fields.boundaries.description`、`fields.vibe.description`、`fields.continuity.description`；`content_template` | 核心原则、边界、气质和连续性的抽取表达；四个字段的标题、顺序和固定文案 |
| `identity` | `description`；`fields.creature.description`、`fields.name.description`、`fields.vibe.description`、`fields.avatar.description`、`fields.emoji.description`、`fields.introduction.description`；`content_template` | 身份信息范围；身份、名称、气质、头像、Emoji、自我介绍的字段要求；正文标签、顺序和固定文案 |

表中 `fields.<name>.description` 表示在 `fields` 数组中按 `name` 定位并修改
`description`，不是替换整个字段。JSON 属性名统一小写（`description`，不是
`Description`）。Profile 的 `fields.content` 仅开放其 description，不开放字段本身。

除白名单说明文字和三个正文模板外，所有配置均锁定为部署默认值，包括：
`memory_type`、`enabled`、`operation_mode`、`stage`、`peer_enabled`、
`directory`、`filename_template`、所有字段的名称/类型/`merge_op`/`init_value`、
`embedding_template` 和 `overview_template`，以及未开放的字段说明。
例如 Profile 保留 `profile.md` 和 content 的 `merge_op=patch`；Events 保留
`add_only` 以及 `goal/ranges` 的说明；Identity 的 Name immutable 规则不变。
Profile、Preferences、Entities 不开放 `content_template`。
改写 topic/category/name/event_name 的生成说明仍可能间接影响未来的目录或文件名，
但不允许修改目录/文件名模板本身。

例如，仅修改类型说明：

```bash
curl -X PUT "$OV_ENDPOINT/api/v1/admin/accounts/acme/memory-templates/profile" \
  -H "X-API-Key: $OV_ADMIN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"description":"只记住业务相关事实，使用 {{ language }}。"}'
```

PUT 从**部署默认模板**补齐未传入的配置，不从上一次 Account 自定义值补齐，最终保存
**完整 YAML 模板**。`fields` 按已有字段名合并，只覆盖白名单允许的说明文字，
未传入的字段和属性全部保留默认值；不能新增、删除或重命名字段，提交空列表不会删除字段。
完整 GET `effective` 对象可以回传：锁定字段值与默认值相同则接受，任何锁定值变更、
未知配置项、未知字段或重复字段名均返回 `INVALID_ARGUMENT`，当前生效文件不变。
若仅调整一个 description 且需保留其他自定义内容，应先 GET，修改 `effective` 对象后
整体 PUT。空对象会发布一份完整默认配置，状态仍为自定义；恢复系统默认应调用 DELETE。
DELETE 幂等。

返回包含 `memory_type`、`status`（`system_default` / `custom`）、
`updated_at`（UTC 发布时间，默认状态为 null），以及完整的 `defaults` / `effective`。
对象使用 YAML 字段名，例如 `fields[].type`。列表接口返回 `result.account_id` 和
`result.templates`；单模板操作返回 `result.account_id` 及上述模板结果。

按 Account、按模板独立存储：

```text
/local/{account_id}/_system/memory_templates/
  profile.yaml
  preferences.yaml
  events.yaml
  ...
```

仅发布自定义时创建对应文件。文件包含完整 Schema 和内部 `_updated_at` 时间戳，
不再使用集中式 `memory_templates.json`。更新前备份至 `{type}.yaml.backup`。
读写经过 AGFS，沿用当前部署的加密和存储配置，不能直接编辑加密后的底层文件。
不修改 Account 的 `setting.json` 或 User 的 `user_config.json`。
个人版使用默认 Account；企业版使用指定 Account，内核不区分两套文件结构。

普通 Session 记忆抽取在筛选 Schema 和初始化记忆文件之前读取 Account 模板。
同一份 Registry 快照贯穿模型抽取、补丁合并和记忆文件更新；发布新模板不改变已开始
抽取的快照，不同快照的请求不会合并进同一批流式更新。排队任务按**抽取开始时**取值，
不是按 HTTP Commit 受理时间取值。同一 Account 下符合记忆策略的 User/Peer 共用模板，
不同 Account 不串用，也不修改共享的部署 Registry。发布或恢复默认不会主动重写历史
记忆，后续 Commit 可按生效规则更新已有记忆。

白名单内提交的说明和正文模板必须是非空字符串，并通过 Jinja 语法校验；单文件序列化后不超过 1 MiB。
每个可编辑 `description`（类型说明及 `fields[].description`）最多 50,000 个 Unicode 码点，按提交的原文计数，包含空格、换行和 Jinja 源码，不按 UTF-8 字节或渲染后的长度计数。各说明独立计数，不合并计算；整个配置仍受 1 MiB 上限约束。超过上限返回 400，不修改当前配置。
发布不调用 LLM。存储错误或文件损坏明确报错，不伪装成系统默认。本次不增加公共文件浏览目录、SDK/CLI 命令、草稿或历史版本 UI。

#### content_template 的编辑与执行边界

正文模板用于将已抽取/合并的字段组织为 Markdown，不是抽取 Prompt。
允许修改标题、顺序、固定文案，按条件显示/隐藏字段。不要求保留默认标题或输出全部字段；
但隐藏字段不等于停止抽取/删除该字段，也不会删除原始 Session 或系统保存的字段元数据。
Events 的默认 embedding 模板引用正文，因此正文变化也可能影响后续检索输入。
路径、文件名、字段定义、merge_op（包括 Identity name 的 immutable）仍锁定。

| 类型 | 正文中可引用的字段 |
| --- | --- |
| events | event_name、goal、summary、ranges |
| soul | core_truths、boundaries、vibe、continuity |
| identity | name、creature、vibe、emoji、avatar、introduction |

`language` 仍是说明字段的变量，不属于上述正文变量；正文不要引用其他 Account/User、请求上下文或任意 Python 对象。
仅 Events 可调用以下 `extract_context` 只读方法（位置参数）：

- `get_resource_event_content(ranges, summary)`：资源添加事件正文；非资源事件为空。
- `get_first_message_time_from_ranges(ranges)`：第一条来源消息日期。
- `get_first_message_time_with_weekday_from_ranges(ranges)`：日期及星期。
- `get_event_content(ranges, summary[, ratio_threshold])`：按已有逻辑选择 ChatLog/摘要；省略阈值为 0.2，显式 0 表示存在原文时优先原文。
- `get_year(ranges)`、`get_month(ranges)`、`get_day(ranges)`：来源日期分量。

首个参数使用 `ranges`（或 `ranges|default('')`），不能自行构造消息范围；阈值只能为 0～1 的数字字面量。
允许去掉 ChatLog 或资源事件分支，但去掉后不再自动展示这些正文/资源链接；原始 Session 仍保留。

支持的 Jinja 子集：

- `if/elif/else`、比较/布尔条件、`set` 局部变量（不能覆盖内置字段、extract_context、loop）。
- `for` 遍历模板中显式写出的列表/元组，最多 32 项；支持标题/字段二元组和 `loop.index/index0/first/last/length`。不支持嵌套/递归循环、range() 或遍历消息/长字符串。
- 过滤器：`default`、`trim`、`lower`、`upper`、`length`；测试：`defined`、`undefined`、`none`、`string`。
- 不支持模板导入/继承、宏、任意函数/对象属性访问、下标访问、算术或字符串倍增/拼接。不能注入系统保留的 `<!-- MEMORY_FIELDS ... -->` 元数据。

模板 UTF-8 大小 ≤ 64 KiB，AST 节点 ≤ 2048，渲染正文 ≤ 1 MiB（不含系统追加元数据）。
Account 覆盖在发布时和抽取加载时验证，运行时使用受限 Jinja 环境，只提供白名单字段/方法。
渲染失败会报告错误并停止该次文件写入，不走旧的空正文 fallback；部署内置模板的渲染路径不变。
这些保护不代替 Worker 的 CPU/内存配额，也不评估记忆效果或做前端 Markdown/HTML 安全过滤。
说明字段的既有渲染规则不在本次正文模板限制的改动范围内。

校验失败返回 `INVALID_ARGUMENT`，`error.details` 含 `field=content_template`、受控 `reason` 和可用时的 `line`。
失败不修改当前发布配置。此前保存的、结构有效但使用不支持 Jinja 的模板仍可读取、重新发布或恢复默认；
不会绕过新规则继续执行，抽取加载时提示修复。损坏 YAML 仍明确报错。

示例：只展示事件名称和摘要，不输出 ChatLog：

```json
{"content_template": "# {{ event_name }}\n\n## 事件摘要\n{{ summary }}"}
```

示例：Soul 的分节展示：

```jinja
{% for title, text in [('核心价值', core_truths), ('边界', boundaries), ('气质', vibe), ('连续性', continuity)] %}
{% if text %}
## {{ title }}
{{ text }}
{% endif %}
{% endfor %}
```

### user_settings

ROOT 可管理任意 User，ADMIN 仅可管理所属 account 内的 User。User 配置接口当前
仅允许修改 `memory_policy`。顶层统一的 `memory_types` 控制允许抽取的记忆类型。
用户记忆根据每条 Message 的 `peer_id` 自动写入 Self 或 Peer；Agent 记忆始终只写入
Self。

```http
GET /api/v1/admin/accounts/{account_id}/users/{user_id}/settings
PATCH /api/v1/admin/accounts/{account_id}/users/{user_id}/settings
Content-Type: application/json

{
  "memory_policy": {
    "memory_types": ["profile", "preferences", "events", "entities", "experiences"]
  }
}
```

响应直接返回 User 级 `memory_policy`，并展开默认记忆类型和 Agent 记忆依赖；配置
`experiences` 时会展开为 `cases`、`trajectories`、`experiences`；
该结果不受 account 级 Agent 进化开关影响，Account 开关由独立接口管理。
更新前会备份到该 User 的 `settings/user_config.backup.json`。未显式配置策略的
Session 在 commit 时读取该 User 最新策略；User 未覆盖时，依次回退到
`server.user_config_defaults.memory_policy` 和内核默认策略。若要清除已持久化的
User override 并重新继承上述默认值，请 PATCH `{"memory_policy": null}`。
`{"memory_policy": {}}` 表示显式策略，不会清除 override。

---

### create_account

#### 1. API 实现介绍

创建新工作区及其首个管理员用户。

**处理流程：**
1. 验证请求者具有 ROOT 权限
2. 使用 API Key Manager 创建账户和初始管理员用户
3. 初始化账户级目录结构
4. 初始化管理员用户的个人目录
5. 写入可选的初始管理员用户配置
6. 返回账户信息和用户密钥（非 trusted 模式下）

**代码入口：**
- `openviking/server/routers/admin.py:create_account` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.create_account` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_create_account` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| admin_user_id | str | 是 | - | 首个管理员用户 ID |
| seed | str | 否 | `null` | 可选的确定性 API Key seed。传入后，key secret 为 `sha256(user_id + "\0" + seed)` |
| user_config | object | 否 | `null` | 首个管理员用户的初始配置。支持 `add_targets.resource_uri`、`add_targets.skill_uri` 和 `memory_policy` |

**说明：**
- 在 `trusted` 模式下，响应中不会包含 `user_key` 字段
- 省略 `seed` 时使用默认随机 API Key。seed 应视为密钥材料；过短的 seed 会让 key 更容易被猜测。
- 不再支持 account 级 namespace 隔离配置。用户记忆使用 user-scoped namespace，一对多外部参与者通过 `peer_id` 表达。
- `user_config.add_targets.resource_uri` 必须是可写资源目录 URI：`viking://resources` 或 `viking://resources/...`、`viking://~/resources` 或 `viking://~/resources/...`、`viking://user/{user_id}/resources` 或 `viking://user/{user_id}/resources/...`、`viking://user/{user_id}/peers/{peer_id}/resources` 或 `viking://user/{user_id}/peers/{peer_id}/resources/...`。
- `user_config.add_targets.skill_uri` 只能是 `viking://~/skills` 或 `viking://agent/skills`。v1 不支持显式写成 `viking://user/{user_id}/skills`。
- 旧写法兼容：`viking://user/resources[/...]` 和 `viking://user/skills` 在这里仍会被接受，并归一化为 `viking://~/...` 形式（服务端会打印一条 info 日志）。在其他位置，无 uid 的写法会在请求入口被拒绝——新配置请直接写 `viking://~/...`。

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/admin/accounts
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice",
    "seed": "alice-seed"
  }'
```

`trusted` 模式示例：

```bash
# 首先，在 api_key 模式下注册网关管理员用户
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "platform",
    "admin_user_id": "gateway-admin"
  }'

# 然后在 trusted 模式下使用；管理权限来自 root_api_key
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -H "X-OpenViking-Account: platform" \
  -H "X-OpenViking-User: gateway-admin" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice"
  }'
```

`trusted` 模式也支持"不带身份头"的 ROOT 回退写法：

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice"
  }'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

result = client.admin_create_account(
    account_id="acme",
    admin_user_id="alice",
    seed="alice-seed",
)
print(f"Account created: {result['account_id']}")
print(f"Admin user: {result['admin_user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")

result = client.admin_create_account(
    account_id="acme-private",
    admin_user_id="alice",
    user_config={
        "add_targets": {
            "resource_uri": "viking://~/resources",
            "skill_uri": "viking://~/skills",
        }
    },
)
```

**TypeScript SDK**

```typescript
console.log(await client.adminCreateAccount("account-id", "admin-user-id"));
```

**Go SDK**

```go
result, err := client.AdminCreateAccount(ctx, "acme", "alice")
if err != nil {
    return err
}
fmt.Println(result["account_id"])

seed := "alice-seed"
result, err = client.AdminCreateAccountWithOptions(ctx, "acme-private", "alice", &openviking.AdminCreateAccountOptions{
    Seed: &seed,
    UserConfig: map[string]any{
        "add_targets": map[string]any{
            "resource_uri": "viking://~/resources",
            "skill_uri":    "viking://~/skills",
        },
    },
})
```

**CLI**

```bash
# 需要 ROOT 权限，使用 --sudo
ov --sudo admin create-account acme --admin alice
ov --sudo admin create-account acme --admin alice --seed alice-seed

ov --sudo admin create-account acme-private --admin alice \
  --user-config-json '{"add_targets":{"resource_uri":"viking://~/resources","skill_uri":"viking://~/skills"}}'
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "admin_user_id": "alice",
    "user_key": "7f3a9c1e..."
  },
  "time": 0.1
}
```

---

### list_accounts

#### 1. API 实现介绍

列出所有工作区（仅 ROOT）。

**处理流程：**
1. 验证请求者具有 ROOT 权限
2. 调用 API Key Manager 获取所有账户列表（按创建顺序排列）
3. 应用可选的 `name` 过滤
4. 应用可选的 `limit`/`page` 分页
5. 返回包含账户 ID、创建时间和用户数量的列表

**代码入口：**
- `openviking/server/routers/admin.py:list_accounts` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.get_accounts` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_list_accounts` - Python SDK

#### 2. 接口和参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| name | str | 否 | null | 按账户 ID 过滤（通配符 `*` 和 `?` 匹配） |
| limit | int | 否 | null | 每页数量（≥1）。省略则返回所有匹配项 |
| page | int | 否 | 1 | 从 1 开始的页码；仅在设置了 `limit` 时生效 |

结果按创建顺序返回。

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/admin/accounts
```

```bash
# 列出所有账户
curl -X GET http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: <root-key>"

# 带过滤条件（通配符 name 匹配）
curl -X GET "http://localhost:1933/api/v1/admin/accounts?name=*acme*" \
  -H "X-API-Key: <root-key>"

# 分页（每页 50，取第 2 页）
curl -X GET "http://localhost:1933/api/v1/admin/accounts?limit=50&page=2" \
  -H "X-API-Key: <root-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

accounts = client.admin_list_accounts(name="*acme*", limit=50, page=1)
for account in accounts:
    print(f"Account: {account['account_id']}, created: {account['created_at']}, users: {account['user_count']}")
```

**TypeScript SDK**

```typescript
console.log(await client.adminListAccounts({ name: "*acme*", limit: 50, page: 1 }));
```

**Go SDK**

```go
accounts, err := client.AdminListAccounts(ctx)
if err != nil {
    return err
}
fmt.Println(accounts)
```

**CLI**

```bash
# 需要 ROOT 权限，使用 --sudo
ov --sudo admin list-accounts

# 按通配符 name 过滤
ov --sudo admin list-accounts --name '*acme*'

# 分页
ov --sudo admin list-accounts --limit 50 --page 2
```

**响应示例**

```json
{
  "status": "ok",
  "result": [
    {"account_id": "default", "created_at": "2026-02-12T10:00:00Z", "user_count": 1},
    {"account_id": "acme", "created_at": "2026-02-13T08:00:00Z", "user_count": 2}
  ],
  "time": 0.1
}
```

---

### delete_account

#### 1. API 实现介绍

删除工作区及其所有关联用户和数据（仅 ROOT）。

**处理流程：**
1. 验证请求者具有 ROOT 权限
2. 级联删除账户下的所有 AGFS 数据（`user/` 和 `resources/`；sessions 位于 `user/` 下）
3. 级联删除向量数据库中该账户的所有记录
4. 最后删除账户元数据和所有用户密钥

**代码入口：**
- `openviking/server/routers/admin.py:delete_account` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.delete_account` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_delete_account` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 要删除的工作区 ID |

**说明：**
- 删除操作是不可逆的，会级联删除该账户下的所有数据
- 如果部分数据删除失败，会记录警告日志并继续删除其他数据

#### 3. 使用示例

**HTTP API**

```
DELETE /api/v1/admin/accounts/{account_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme \
  -H "X-API-Key: <root-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

result = client.admin_delete_account(account_id="acme")
print(f"Account deleted: {result['deleted']}")
```

**TypeScript SDK**

```typescript
await client.adminDeleteAccount("account-id");
```

**Go SDK**

```go
result, err := client.AdminDeleteAccount(ctx, "acme")
if err != nil {
    return err
}
fmt.Println(result["deleted"])
```

**CLI**

```bash
# 需要 ROOT 权限，使用 --sudo
ov --sudo admin delete-account acme
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "deleted": true
  },
  "time": 0.1
}
```

---

### register_user

#### 1. API 实现介绍

在工作区中注册新用户。

**处理流程：**
1. 验证请求者具有 ROOT 权限，或为本账户的 ADMIN
2. 调用 API Key Manager 注册新用户
3. 初始化新用户的个人目录
4. 写入可选的初始用户配置
5. 返回用户信息和用户密钥（非 trusted 模式下）

**代码入口：**
- `openviking/server/routers/admin.py:register_user` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.register_user` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_register_user` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 用户 ID |
| role | str | 否 | "user" | 要分配的角色。`ROOT` 和同 account 的 `ADMIN` 可直接注册 `"user"` 或 `"admin"`。ROOT 身份只来自 `server.root_api_key`。 |
| seed | str | 否 | `null` | 可选的确定性 API Key seed。传入后，key secret 为 `sha256(user_id + "\0" + seed)` |
| user_config | object | 否 | `null` | 新用户的初始配置。支持 `add_targets.resource_uri`、`add_targets.skill_uri` 和 `memory_policy` |

**说明：**
- 在 `trusted` 模式下，响应中不会包含 `user_key` 字段
- 省略 `seed` 时使用默认随机 API Key。seed 应视为密钥材料；过短的 seed 会让 key 更容易被猜测。
- ADMIN 只能在自己所属的 account 中注册用户
- 无法通过用户注册接口直接创建 `"root"` 角色
- `user_config.add_targets.resource_uri` 必须是可写资源目录 URI：`viking://resources` 或 `viking://resources/...`、`viking://~/resources` 或 `viking://~/resources/...`、`viking://user/{user_id}/resources` 或 `viking://user/{user_id}/resources/...`、`viking://user/{user_id}/peers/{peer_id}/resources` 或 `viking://user/{user_id}/peers/{peer_id}/resources/...`。
- `user_config.add_targets.skill_uri` 只能是 `viking://~/skills` 或 `viking://agent/skills`。v1 不支持显式写成 `viking://user/{user_id}/skills`。
- 旧写法兼容：`viking://user/resources[/...]` 和 `viking://user/skills` 在这里仍会被接受，并归一化为 `viking://~/...` 形式（服务端会打印一条 info 日志）。在其他位置，无 uid 的写法会在请求入口被拒绝——新配置请直接写 `viking://~/...`。

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>" \
  -d '{
    "user_id": "bob",
    "role": "user",
    "seed": "bob-seed"
  }'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_register_user(
    account_id="acme",
    user_id="bob",
    role="user",
    seed="bob-seed",
)
print(f"User registered: {result['user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")

result = client.admin_register_user(
    account_id="acme",
    user_id="bob-private",
    role="user",
    user_config={"add_targets": {"resource_uri": "viking://~/resources/project-a"}},
)
```

**TypeScript SDK**

```typescript
console.log(await client.adminRegisterUser("account-id", "user-id", "user"));
```

**Go SDK**

```go
result, err := client.AdminRegisterUser(ctx, "acme", "bob", "user")
if err != nil {
    return err
}
fmt.Println(result["user_id"])

seed := "bob-seed"
result, err = client.AdminRegisterUserWithOptions(ctx, "acme", "bob-private", "user", &openviking.AdminRegisterUserOptions{
    Seed: &seed,
    UserConfig: map[string]any{
        "add_targets": map[string]any{"resource_uri": "viking://~/resources/project-a"},
    },
})
```

**CLI**

```bash
# ROOT 或本账户的 ADMIN 都可以执行
# 如果使用普通用户的 api_key 但该用户是 acme 的 ADMIN：
ov admin register-user acme bob --role user
ov admin register-user acme bob --role user --seed bob-seed
# 如果使用 root_api_key（--sudo）：
ov --sudo admin register-user acme bob --role user

ov admin register-user acme bob-private --role user \
  --user-config-json '{"add_targets":{"resource_uri":"viking://~/resources/project-a"}}'
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob",
    "user_key": "d91f5b2a..."
  },
  "time": 0.1
}
```

---

### list_users

#### 1. API 实现介绍

列出工作区中的活跃用户。正在删除中的用户不会返回。

**处理流程：**
1. 验证请求者具有 ROOT 权限，或为本账户的 ADMIN
2. 调用 API Key Manager 获取活跃用户列表（按创建顺序排列）
3. 应用可选的过滤条件（name、role）
4. 应用可选的 `limit`/`page` 分页
5. 返回用户列表（trusted 模式下不包含 user_key）

**代码入口：**
- `openviking/server/routers/admin.py:list_users` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.get_users` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_list_users` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| name | str | 否 | null | 按用户 ID 过滤（通配符 `*` 和 `?` 匹配） |
| role | str | 否 | null | 按角色过滤 |
| limit | int | 否 | null | 每页数量（≥1）。省略则返回所有匹配项 |
| page | int | 否 | 1 | 从 1 开始的页码；仅在设置了 `limit` 时生效 |

**说明：**
- 结果按创建顺序返回
- ADMIN 只能列出自己所属的 account 中的用户
- 在 `trusted` 模式下，响应中不会包含 `user_key` 字段
- 用户删除开始后，不再出现在该列表中

#### 3. 使用示例

**HTTP API**

```
GET /api/v1/admin/accounts/{account_id}/users
```

```bash
# 列出所有用户
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: <root-or-admin-key>"

# 带过滤条件（通配符 name 匹配）
curl -X GET "http://localhost:1933/api/v1/admin/accounts/acme/users?name=*ali*&role=admin" \
  -H "X-API-Key: <root-or-admin-key>"

# 分页（每页 50，取第 2 页）
curl -X GET "http://localhost:1933/api/v1/admin/accounts/acme/users?limit=50&page=2" \
  -H "X-API-Key: <root-or-admin-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

users = client.admin_list_users(account_id="acme", name="*ali*", limit=50, page=1)
for user in users:
    print(f"User: {user['user_id']}, role: {user['role']}")
```

**TypeScript SDK**

```typescript
console.log(await client.adminListUsers("account-id", { name: "*ali*", limit: 50, page: 1 }));
```

**Go SDK**

```go
users, err := client.AdminListUsers(ctx, "acme")
if err != nil {
    return err
}
fmt.Println(users)
```

**CLI**

```bash
# ROOT 或本账户的 ADMIN 都可以执行
# 如果使用普通用户的 api_key 但该用户是 acme 的 ADMIN：
ov admin list-users acme
# 如果使用 root_api_key（--sudo）：
ov --sudo admin list-users acme
# 按通配符 name 过滤
ov admin list-users acme --name '*ali*'
# 分页
ov admin list-users acme --limit 50 --page 2
```

**响应示例**

```json
{
  "status": "ok",
  "result": [
    {"user_id": "alice", "role": "admin"},
    {"user_id": "bob", "role": "user"}
  ],
  "time": 0.1
}
```

---

### remove_user

#### 1. API 实现介绍

从工作区中移除用户。用户 API Key 会立即失效，其拥有的数据清理异步执行。

**处理流程：**
1. 验证请求者具有 ROOT 权限，或为本账户的 ADMIN
2. 写入删除 fence，并使用户 API Key 失效
3. 提交一个持久化清理任务，删除该用户拥有的数据
4. 返回删除任务 ID

**代码入口：**
- `openviking/server/routers/admin.py:remove_user` - HTTP 路由
- `openviking/service/user_deletion.py:UserDeletionService.delete_user` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_remove_user` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 要移除的用户 ID |

**说明：**
- ADMIN 只能移除自己所属的 account 中的用户
- 不能删除账户的最后一个 admin 用户
- 删除开始后，用户 key 立即失效，list_users 不再返回该用户

#### 3. 使用示例

**HTTP API**

```
DELETE /api/v1/admin/accounts/{account_id}/users/{user_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme/users/bob \
  -H "X-API-Key: <root-or-admin-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_remove_user("acme", "bob")
print(f"User deletion task: {result['task_id']}")
```

**TypeScript SDK**

```typescript
await client.adminRemoveUser("account-id", "user-id");
```

**Go SDK**

```go
result, err := client.AdminRemoveUser(ctx, "acme", "bob")
if err != nil {
    return err
}
fmt.Println(result["task_id"])
```

**CLI**

```bash
# ROOT 或本账户的 ADMIN 都可以执行
# 如果使用普通用户的 api_key 但该用户是 acme 的 ADMIN：
ov admin remove-user acme bob
# 如果使用 root_api_key（--sudo）：
ov --sudo admin remove-user acme bob
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob",
    "status": "deleting",
    "task_id": "..."
  },
  "time": 0.1
}
```

---

### set_role

#### 1. API 实现介绍

将账户用户提升为 ADMIN。ROOT 可以操作任意账户；ADMIN 只能操作自己的账户。

**处理流程：**
1. 验证请求者具有 ROOT 或 ADMIN 权限，并限制 ADMIN 只能操作自己的账户
2. 调用 API Key Manager 更新用户角色
3. 返回更新后的用户信息

**代码入口：**
- `openviking/server/routers/admin.py:set_user_role` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.set_role` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_set_role` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 用户 ID |
| role | str | 是 | - | 固定为 "admin" |

**说明：**
- ROOT 和 ADMIN 可以将用户提升为 ADMIN；ADMIN 只能操作自己的账户
- 该接口不支持设置 "user" 或 "root"；ROOT 身份只来自 `server.root_api_key`

#### 3. 使用示例

**HTTP API**

```
PUT /api/v1/admin/accounts/{account_id}/users/{user_id}/role
```

```bash
curl -X PUT http://localhost:1933/api/v1/admin/accounts/acme/users/bob/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"role": "admin"}'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

result = client.admin_set_role(account_id="acme", user_id="bob", role="admin")
print(f"User: {result['user_id']}, new role: {result['role']}")
```

**TypeScript SDK**

```typescript
await client.adminSetRole("account-id", "user-id", "admin");
```

**Go SDK**

```go
result, err := client.AdminSetRole(ctx, "acme", "bob", "admin")
if err != nil {
    return err
}
fmt.Println(result["role"])
```

**CLI**

```bash
# 需要 ROOT 权限，使用 --sudo
ov --sudo admin set-role acme bob admin
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob",
    "role": "admin"
  },
  "time": 0.1
}
```

---

### regenerate_key

#### 1. API 实现介绍

重新生成用户的 API Key，旧 Key 立即失效。

**处理流程：**
1. 验证请求者具有 ROOT 权限，或为本账户的 ADMIN
2. 调用 API Key Manager 重新生成用户密钥
3. 旧密钥立即失效
4. 返回新的用户密钥

**代码入口：**
- `openviking/server/routers/admin.py:regenerate_key` - HTTP 路由
- `openviking/server/api_keys/new.py:APIKeyManager.regenerate_key` - 核心实现
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_regenerate_key` - Python SDK

#### 2. 接口和参数说明

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| account_id | str | 是 | - | 工作区 ID |
| user_id | str | 是 | - | 用户 ID |
| seed | str | 否 | `null` | JSON request body 中可选的确定性 API Key seed。传入后，key secret 为 `sha256(user_id + "\0" + seed)` |

**说明：**
- ADMIN 只能为自己所属的 account 中的用户重新生成密钥
- 旧密钥会立即失效，需要更新使用该密钥的客户端
- 省略 `seed` 时使用默认随机重新生成逻辑。

#### 3. 使用示例

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users/{user_id}/key
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>" \
  -d '{"seed": "bob-new-seed"}'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_regenerate_key(
    account_id="acme",
    user_id="bob",
    seed="bob-new-seed",
)
print(f"New user key: {result['user_key']}")
```

**TypeScript SDK**

```typescript
console.log(await client.adminRegenerateKey("account-id", "user-id"));
```

**Go SDK**

```go
result, err := client.AdminRegenerateKey(ctx, "acme", "bob")
if err != nil {
    return err
}
fmt.Println(result["user_key"])

seed := "bob-new-seed"
result, err = client.AdminRegenerateKeyWithOptions(ctx, "acme", "bob", &openviking.AdminRegenerateKeyOptions{
    Seed: &seed,
})
```

**CLI**

```bash
# ROOT 或本账户的 ADMIN 都可以执行
# 如果使用普通用户的 api_key 但该用户是 acme 的 ADMIN：
ov admin regenerate-key acme bob
ov admin regenerate-key acme bob --seed bob-new-seed
# 如果使用 root_api_key（--sudo）：
ov --sudo admin regenerate-key acme bob
```

**响应示例**

```json
{
  "status": "ok",
  "result": {
    "user_key": "e82d4e0f..."
  },
  "time": 0.1
}
```

---

### migrate_legacy_data

#### 1. API 实现介绍

将 0.3.x legacy `viking://agent/...` / `viking://session/...` 数据迁移到 0.4.0 的 user / peer namespace，或在确认迁移结果后清理旧 namespace。该接口仅 ROOT 可调用，并以后台 task 执行。

**处理流程：**
1. 验证请求者具有 ROOT 权限
2. `action=migrate` 时执行 preflight，检查 account registry、session owner 等前置条件
3. 创建 root 级后台 task
4. 迁移时复制文件和已有向量记录；cleanup 时先删除旧向量记录，再删除旧 AGFS 目录

迁移不会自动调用 `reindex`。如果迁移后的检索结果不符合预期，需要用户对新路径手动执行 reindex。

**代码入口：**
- `openviking/server/routers/admin.py:migrate_legacy_data` - HTTP 路由
- `openviking/service/legacy_migration.py:LegacyDataMigration` - 迁移实现

#### 2. 接口和参数说明

**HTTP API**

```
POST /api/v1/admin/migrate
```

**参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| action | str | 否 | migrate | `migrate` 执行迁移；`cleanup` 清理旧 namespace |

**迁移结果字段**

| 字段 | 说明 |
|------|------|
| migrated.files / migrated.directories | 复制的文件和目录数量 |
| migrated.vector_records | 复制的已有向量记录数量 |
| migrated.skipped_vector_records | 因没有向量 payload 而跳过的旧记录数量 |
| migrated.operations | 按迁移类别统计的操作数量 |
| skipped / warnings / created_users | 跳过项、告警、自动创建的用户 |

**Cleanup 结果字段**

| 字段 | 说明 |
|------|------|
| cleanup.directories | 删除的 legacy 目录数量 |
| cleanup.vector_records | 删除的旧向量记录数量 |
| cleanup.targets | 已清理的 legacy scope |
| skipped / warnings | 跳过项和告警 |

#### 3. 使用示例

**HTTP API**

```bash
# 执行迁移
curl -X POST http://localhost:1933/api/v1/admin/migrate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"action": "migrate"}'

# 清理旧 namespace
curl -X POST http://localhost:1933/api/v1/admin/migrate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"action": "cleanup"}'
```

**Python SDK**

```python
print(client.admin_migrate(cleanup=False))
```

**TypeScript SDK**

```typescript
console.log(await client.adminMigrate(false));
```

**Go SDK**

```go
result, err := client.AdminMigrate(ctx, &openviking.AdminMigrateOptions{
    Cleanup: false,
})
if err != nil {
    return err
}
fmt.Println(result["task_id"])
```

**CLI**

```bash
ov --sudo admin migrate --output json
ov --sudo admin migrate --cleanup --output json
```

**响应示例**

```json
{
  "task_id": "legacy_migration_..."
}
```

---

<a id="用户添加位置设置"></a>

## 完整示例

### 典型管理流程

```bash
# 步骤 1：ROOT 创建工作区，指定 alice 为首个 admin（需要 --sudo）
ov --sudo admin create-account acme --admin alice
# 返回 alice 的 user_key

# 步骤 2：alice（admin）注册普通用户 bob
# 配置文件中的 api_key 设为 alice 的 user_key，不需要 --sudo
ov admin register-user acme bob --role user
# 返回 bob 的 user_key

# 步骤 3：查看账户下所有用户
ov admin list-users acme

# 步骤 4：ROOT 将 bob 提升为 admin（需要 --sudo）
ov --sudo admin set-role acme bob admin

# 步骤 5：bob 丢失 key，重新生成（旧 key 立即失效）
# alice 作为 admin 可以执行，不需要 --sudo
ov admin regenerate-key acme bob

# 步骤 6：移除用户
ov admin remove-user acme bob

# 步骤 7：删除整个工作区（需要 --sudo）
ov --sudo admin delete-account acme
```

### HTTP API 等效流程

```bash
# 步骤 1：创建工作区
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"account_id": "acme", "admin_user_id": "alice"}'

# 步骤 2：注册用户（使用 alice 的 admin key）
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>" \
  -d '{"user_id": "bob", "role": "user"}'

# 步骤 3：列出用户
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: <alice-key>"

# 步骤 4：将用户提升为 admin
curl -X PUT http://localhost:1933/api/v1/admin/accounts/acme/users/bob/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>" \
  -d '{"role": "admin"}'

# 步骤 5：重新生成 key
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>"

# 步骤 6：移除用户
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme/users/bob \
  -H "X-API-Key: <alice-key>"

# 步骤 7：删除工作区
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme \
  -H "X-API-Key: <root-key>"
```

---

## 相关文档

- [多租户](../concepts/11-multi-tenant.md) - 多租户模型、角色和共享边界
- [API 概览](01-overview.md) - 认证与响应格式
- [会话管理](05-sessions.md) - 会话管理
- [系统](07-system.md) - 系统和监控 API
