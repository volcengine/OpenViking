# 添加资源后的解析路由

本文描述当前 `add_resource` 从收到资源到落盘、建索引的真实执行链。重点回答四个问题：入口在哪里分流、资源类型在哪里确定、Understanding 与 Connector 分别做什么，以及 `wait` 到底等待什么。

## 先记住四条规则

1. 对外只有一个兼容入口：`ResourceService.add_resource`。SDK、HTTP API、MCP 最终都应调用它；内部 Parser 类和注册方法不是兼容接口。
2. Connector 是一条独立的端到端导入链；Understanding 只是标准链里的一个 Parser 后端。
3. 普通文件只做一次 Parser 选择。选择依据是 Accessor 获取资源后冻结的 `resolved_extension`，不是临时文件名，也不会在队列消费者里重新猜；原始飞书 URL 是一个显式例外，可在 Accessor 前按配置直达 Understanding。
4. 目录、网站目录以及内部 Parser 遍历出的子文件不逐个调用 Understanding，统一留在内置 Parser 链内处理。

## 总流程

```text
SDK / HTTP API / MCP
        |
        v
ResourceService.add_resource                  对外入口
        |
        v
ResourceService._add_resource                 阶段一：选择执行方式
        |
        +-- Connector 命中且参数受支持 ------> Connector doc/add
        |                                         |
        |                                         +--> 轮询 Connector task/info
        |                                         +--> Connector 自己解析并写入资源
        |
        +-- Git && wait=false ----------------> 预检 + AddResource 队列 --> 返回 task_id
        |                                                          |
        |                                                          +--> Worker
        |
        +-- HTTP 服务远程资源 && wait=false && Understanding 已启用
        |       |
        |       +-- 原始 URL 可直达 -----------> 提交 Understanding --> ExternalParse 队列
        |       |                                                        |
        |       |                                                        +--> 返回 task_id --> Worker
        |       |
        |       +-- 需要先确定本地类型 --------> Accessor 下载并识别
        |               |
        |               +-- 命中 Understanding --> 上传同一文件 --> ExternalParse 队列
        |               |                                             |
        |               |                                             +--> 返回 task_id --> Worker
        |               |
        |               +-- 未命中 ------------> 复用 LocalResource，进入当前请求标准链
        |
        +-- 其余场景（包括所有 wait=true） -----> 当前请求标准链

当前请求 / Worker
        |
        v
ResourceProcessor.process_resource             阶段二：标准解析链
        |
        v
UnifiedResourceProcessor
        |
        +-- 已有 understanding_response_id ----> ParserRouter --> Understanding 恢复任务
        |
        +-- 原始 URL 可直达 Understanding -----> ParserRouter --> Understanding 提交并等待
        |
        +-- 原始文本 --------------------------> 内置 ParserRegistry
        |
        +-- 路径 / URL --> AccessorRegistry.access（选择数据访问器）
                                  |
                                  v
                              LocalResource
                                  |
                                  +-- 目录 --> DirectoryParser（内置）
                                  |
                                  +-- 文件 --> ParserRouter.parse（选择 Parser，只选一次）
                                                  |
                                                  +-- 内置 ParserRegistry
                                                  +-- Understanding 上传并等待
        |
        v
     ParseResult --> TreeBuilder 落盘
        |
        v
阶段三：返回策略
        |
        +-- wait=true  --> 继续等待摘要 / 语义队列 / 向量索引后返回
        |
        +-- wait=false --> 普通标准链落盘后进入 AddResource 队列并返回
```

Understanding 不受 `wait=false` 限制。`wait=true` 在当前请求内完成 Understanding 提交、轮询、解析和 TreeBuilder，并继续等待后续队列；`wait=false` 的远程服务路径会先提交 Understanding、将 `response_id` 入队，再由 Worker 恢复任务。

分类发生的位置：

| 分类内容 | 代码位置 | 输出 |
|---|---|---|
| Connector、异步 Git、异步 Understanding、标准链 | `ResourceService._add_resource` | 选定顶层执行链 |
| Feishu、Git、Feed、HTTP、本地文件 | `AccessorRegistry.access` | `LocalResource` |
| Understanding 能否直接接收原始 URL | `ParserRouter.should_use_understanding_directly` | 直达 Understanding 或继续 Accessor |
| Understanding 与内置 Parser | `ParserRouter.parse` | `ParseResult` |

后台资源任务统一使用可恢复的 `AddResourceMsg`，但按工作类型进入两个独立队列：Understanding 使用受外部解析并发限制的 `ExternalParse`；Git 和已落盘的本地后处理使用 `AddResource`。两个队列都由 `AddResourceProcessor` 消费，锁接管失败时仍回到原队列。Worker 以 `wait=true` 重新进入同步添加链，因此不会再次进入异步入队分支。

## 顶层路由表

| 输入或场景 | 获取数据 | 解析者 | 是否走标准 `ParseResult -> TreeBuilder` | 返回时机 |
|---|---|---|---|---|
| Connector 配置允许的 scheme，且参数受支持 | Connector 服务 | Connector 服务 | 否 | 提交成功立即返回 `task_id`，后台轮询状态 |
| `tos://` 但 Connector 不可用或参数不支持 | 不降级 | 不执行 | 否 | 直接报清晰的参数或配置错误 |
| Git，`wait=false` | GitAccessor 在后台 clone | 内置目录/代码仓库 Parser | 是 | 预检仓库并预占 URI 后返回 |
| Git，`wait=true` | GitAccessor | 内置目录/代码仓库 Parser | 是 | 解析、落盘及语义队列完成后返回 |
| 飞书 URL，`parser_api.enable_feishu_url=true` 且有 user/app 凭证 | Understanding 直接读取飞书 | Understanding | 是 | `wait=false` 时提交并入队；`wait=true` 时同步解析 |
| 飞书 URL，直达配置关闭或无可用凭证 | FeishuAccessor | 内置 Markdown Parser | 是 | Accessor 拉取、归一化后走标准链 |
| HTTP 服务请求，`wait=false`，命中 Understanding | HTTPAccessor 识别类型并上传同一份本地文件 | Understanding | 是 | 类型识别、Understanding 提交、URI 预占和入队后返回 |
| 其他 URL、文件、目录、原始文本 | 对应 Accessor；原始文本无需 Accessor | 内置 Parser 或同步 Understanding | 是 | 至少完成解析和落盘后返回 |

Connector 可处理的普通来源若带有它不支持的参数，会回退到标准链；`tos://` 没有标准 Accessor，不能回退，否则只会在更深处得到误导性的解析错误。

## Accessor：先把“数据在哪”变成“本地是什么”

`AccessorRegistry` 按优先级选择数据访问器，当前内置顺序是：

```text
FeishuAccessor (100)
GitAccessor (80)
WebFeedAccessor (60)
HTTPAccessor (50)
LocalAccessor (1)
```

Accessor 的产物统一是 `LocalResource`，包含本地文件或目录路径、`source_type`、原始来源、是否需要清理，以及检测元数据。标准 Parser 不再负责 clone 或下载；只有明确开启的飞书 Understanding 直达链会在 Accessor 前消费原始 URL。

### 飞书资源

飞书是“远程私有数据源”，不是一种本地文件格式。默认使用 `FeishuAccessor` 获取并归一化：

```text
飞书 URL
    |
    v
FeishuAccessor --调用飞书 OpenAPI--> Markdown + 下载后的图片
    |
    v
LocalResource(document.md)
    |
    v
ParserRouter --> MarkdownParser --> ParseResult --> TreeBuilder
```

当 `parser_api.enable=true`、`parser_api.enable_feishu_url=true`，并且本次请求有 `args.feishu_access_token` 或服务端已配置飞书应用凭证时，原始飞书 URL 会在 Accessor 前进入统一的 Understanding 链。否则进入上面的 FeishuAccessor 路径。

`enable_feishu_url` 默认是 `false`。Understanding 一旦被选中，提交、轮询或解析失败会直接返回错误，不会在运行时自动重试 FeishuAccessor。

`FeishuAccessor` 预期支持范围如下：

| URL 类型 | Accessor 行为 | 当前边界 |
|---|---|---|
| `docx/{token}` | 拉取文档 block，转换标题、列表、代码、表格、图片和内嵌 sheet | 图片下载受 `download_images` 控制；内嵌 sheet 最多读取 100 行、A-Z 列 |
| `sheets/{token}` | 枚举所有 sheet；普通网格读取单元格，内嵌多维表格根据 `blockInfo` 转走 Bitable API，统一输出 Markdown 表格 | 普通网格最多读取 `max_rows_per_sheet` 行、A-Z 列；内嵌多维表格继承 Base 的记录和图片处理能力 |
| `base/{app_token}` | 无查询参数时枚举全部数据表；`table` 限定数据表，`table` + `view` 限定视图 | 表和字段完整翻页；每张表最多读取 `max_records_per_table` 条记录；附件字段中的图片会下载，其他附件保留文件名 |
| `wiki/{token}` | 先解析 wiki 节点的实际类型和 token，再进入上述 `docx`、`sheets` 或 `base` 处理器 | wiki 指向其他飞书对象类型时明确报不支持 |

四类入口都支持应用凭证获取的 tenant token；显式传入 `args.feishu_access_token` 时，同一 user token 会用于本次选中的飞书链路。Accessor 路径中，它用于 wiki 解析、正文、sheet/base 和图片请求；Understanding 直达路径中，它作为 `lark_file.user_access_token` 提交，随后从后台队列参数中删除。

飞书专有的本地归一化逻辑到 `FeishuAccessor` 为止，后面只处理 Markdown，因此不再保留并行的本地 `FeishuParser` 入口。即使 `parser_api.extensions` 包含 `md`，`SourceType.FEISHU` 的 `LocalResource` 也固定使用内置 Markdown Parser，不会把已经拉取的数据再次送 Understanding。传给 Understanding 的本地文件同样优先上传本地内容，`original_source` 只作为来源元数据，不会导致二次抓取原 URL。

Accessor 路径中的文档图片和 Base 附件字段图片使用同一条素材下载链路：先生成内部 `feishu://image/{file_token}` 引用；`download_images` 开启时，再通过飞书 Drive 素材接口下载到 `images/` 并改写 Markdown 相对路径。应用还必须具备 `docs:document.media:download` 权限；关闭下载、权限不足（例如 HTTP 403）或素材请求失败时，不会伪造本地图片文件，也不会把引用改成不存在的本地路径。Understanding 直达路径不调用这套素材接口，而是接收结果 ZIP 中已经包含的图片，并生成 artifact 图片映射供后续 URI 改写。

`UnifiedResourceProcessor.prepare` 随后冻结两个字段：

- `resolved_extension`：本次 Parser 路由唯一使用的扩展名。
- `resolved_name`：用于展示和默认资源命名，不参与 HTTP 资源的类型覆盖。

HTTP 资源以 HTTPAccessor 检出的 `meta.extension` 为准；本地文件或上传的临时文件可优先使用显式 `source_name` 的扩展名。这样既不会拿随机临时文件名选 Parser，也不会让用户提供的 URL 名称覆盖实际下载内容类型。

## 无后缀 URL 怎么判断类型

HTTPAccessor 按以下顺序收集和修正类型：

1. URL path 中受支持的显式扩展名。
2. HEAD 响应的 `Content-Disposition` 文件名。
3. HEAD 响应的 `Content-Type`。
4. GET 响应的 `Content-Disposition` 和 `Content-Type`，用于修正之前的模糊网页判断。
5. GET 内容的 magic bytes，例如 PDF、图片、音视频、Office/EPUB/ZIP 签名。
6. 仍无法识别时按网页处理。

最终扩展名写入 `LocalResource.meta.extension`，然后冻结为 `resolved_extension`。`ParserRouter` 只读取这个结果。URL 上已有明确扩展名时，不用 magic bytes 擅自覆盖它。

因此，`wait=false` 不等于“完全不碰源站就返回”。HTTP 服务收到无后缀远程 URL、且 Understanding 已启用时，必须先下载或探测一次，才能知道应进入外部解析队列还是内置 Parser。命中 Understanding 后，生产端直接上传这份已检测的本地文件并取得 `response_id`，然后才清理临时文件；Worker 只恢复该 response，不会重新下载可能已过期或内容已变化的 URL。

## Parser：只回答“本地内容怎么解析”

标准执行链分四类：

- 原始文本：直接进入内置 `ParserRegistry`。
- 可由 Understanding 直接接收的原始 URL：在 Accessor 前进入 `ParserRouter`。
- 本地目录：直接进入 `DirectoryParser`；Git 仓库由目录链委派给代码仓库 Parser。
- 本地文件：进入 `ParserRouter`，根据 `resolved_extension` 在内置 `ParserRegistry` 与 Understanding 之间选一次。

ParserRegistry 只注册项目内置 Parser，不再提供自定义 Parser 类、回调注册或可选模块注册入口。新增数据源优先实现 Accessor；新增文件格式则直接增加内置 Parser 和对应测试。

目录、网站和压缩包的“子文件遍历”属于当前内置 Parser 的内部实现，不回到顶层 `ResourceService`，也不会给每个子文件重新选择 Understanding。顶层压缩文件本身是否进入 Understanding，仍由其冻结扩展名和 `parser_api.extensions` 决定。

## Understanding 链路

Understanding 是“外部解析器”，不是“外部落盘器”：

```text
LocalResource / 远程源
        |
        v
Understanding API
        |
        v
解析结果 ZIP
        |
        v
解压到临时 Viking 目录
        |
        v
ParseResult
        |
        v
TreeBuilder + 标准摘要/索引链
```

同步路径中，Accessor 已下载的本地文件会直接上传给 Understanding，`original_source` 只保留作来源元数据。普通 HTTP 文件的异步路径也先上传已检测文件，再通过统一的 `AddResourceMsg` 持久化 `understanding_response_id`、冻结的 `resolved_extension` 和 `parser_backend="understanding"`；Worker 直接恢复 response，既不重新下载源 URL，也不因配置变化重新选择后端。这些冻结字段是内部任务字段，公共 `args` 不能指定，避免调用方绕过外部解析开关和扩展名白名单。

飞书直达的异步路径也冻结 `parser_backend="understanding"`。生产端统一调用 Understanding 提交：显式 user token 直接转换为 `lark_file`，应用凭证则在 UnderstandingAPI 内获取 tenant token。QueueFS 只持久化 `understanding_response_id`，不保存 token；Worker 从该 response 继续轮询，不重复提交也不重新判断配置。未显式指定资源名的飞书 artifact 会从 ZIP 单一根目录恢复真实标题，目标 URI 因而延迟到解析完成后确定。

Understanding 返回 ZIP 时，本地适配器会安全解压，并根据 Markdown 的相对图片引用生成受控的图片映射 sidecar。TreeBuilder 后续仍使用统一的图片 URI 改写链，不依赖飞书 Accessor 的 `feishu://` 占位符。

这条链的关键特征是：Understanding 只替代 Parser，后面的 `ParseResult`、URI 规划、TreeBuilder 落盘、摘要和索引仍属于 OpenViking。

## Connector 链路

Connector 是另一套端到端导入服务：

```text
ResourceService
    |
    +--> Connector doc/add
              |
              +--> Connector 获取源数据
              +--> Connector 解析
              +--> Connector 写入目标资源树
    |
    +--> 后台轮询 Connector task/info
              |
              +--> 更新 OpenViking TaskRecord
```

Connector 不返回本地 `ParseResult`，也不调用当前进程的 `TreeBuilder`。OpenViking 只负责校验这次请求能否无损委派、提交任务、返回 OpenViking `task_id`，再把 Connector 的终态同步到任务记录。

目前 Connector 不支持的语义包括精确 `to`、`wait=true`、watch、reason/instruction、关闭建索引、摘要、strict、include/exclude 等。普通来源会回退到标准链；Connector-only 来源会立即报错。

## `wait` 的准确含义

| 路径 | `wait=false` | `wait=true` |
|---|---|---|
| Connector | 提交外部任务后返回 | 不支持；不会假装同步等待 |
| Git | 预检并预占 URI 后启动后台标准链 | 当前请求内完成标准链并等待队列 |
| 飞书直达 Understanding | 生产端提交后只将 response ID 放入 `ExternalParse`；无显式资源名时可延迟返回 `root_uri` | 当前请求内调用 Understanding，再等待后续队列 |
| HTTP 服务 + 异步 Understanding | 先识别类型，再预占 URI、入 `ExternalParse` 后返回 | 当前请求内调用 Understanding，再等待后续队列 |
| 普通标准链 | 解析和落盘完成后返回；摘要/语义处理由任务监控 | 解析和落盘完成后继续等待语义队列 |

所以普通 `wait=false` 不是“所有工作后台化”，而是“资源树已落盘，但不阻塞等待后续语义任务”。Git 与异步 Understanding 是两个明确的例外分支。

## 目标 URI、锁和失败边界

- 需要后台执行的 Git 与普通文件 Understanding 任务会先规划并预占目标 URI，避免返回的 URI 随后台竞态变化。未指定名称的飞书直达任务会延迟目标 URI 解析，以 artifact 的真实根标题作为最终名称，并在完成时回写 TaskRecord。
- 锁通过 handoff 交给后台任务或队列 Worker；入队失败时立即释放，并把任务标为失败。
- 临时 `LocalResource` 由拥有它的调用层清理；交给标准处理器后，清理责任随之转移。
- Parser 产生 `ParseResult` 后才进入 TreeBuilder。没有临时解析产物时标准链返回解析错误；目录允许带 warnings 的部分成功，`strict` 决定是否暴露这些警告。
- Connector 的失败边界在外部任务终态，OpenViking 不对其内部文件逐个回滚。

## 代码定位

| 职责 | 入口 |
|---|---|
| 公开分流与异步任务 | `openviking/service/resource_service.py`：`ResourceService.add_resource`、`_add_resource` |
| 标准解析与落盘编排 | `openviking/utils/resource_processor.py`：`ResourceProcessor.process_resource` |
| Accessor 与 Parser 两层衔接 | `openviking/utils/media_processor.py`：`UnifiedResourceProcessor` |
| 文件 Parser 单次选择 | `openviking/parse/parser_router.py`：`ParserRouter` |
| 内置 Parser 注册 | `openviking/parse/registry.py`：`ParserRegistry` |
| HTTP 类型识别 | `openviking/parse/accessors/http_accessor.py`：`HTTPAccessor`、`URLTypeDetector` |
| Understanding 同步适配 | `openviking/parse/understanding_api.py`：`UnderstandingAPI` |
| 飞书直达开关 | `openviking_cli/utils/config/open_viking_config.py`：`ParserApiConfig.enable_feishu_url` |
| 可恢复的后台资源消息与双队列 Worker | `openviking/storage/queuefs/add_resource_msg.py`、`add_resource_processor.py`、`queue_manager.py` |
| Connector 客户端 | `openviking/connector/client.py`：`ConnectorClient` |

## 快速自检

- 无后缀 PDF URL：HTTPAccessor 从响应头或 PDF 签名得到 `.pdf`，ParserRouter 再决定是否使用 Understanding。
- 飞书默认配置：走 FeishuAccessor；仅开启 `enable_feishu_url` 且有凭证时才绕过 Accessor。
- 飞书 user token 直达：队列中只有 response ID 和强制后端标记，不出现 token 明文。
- 已归一化飞书 Markdown：无论 `extensions` 是否包含 `md`，都走内置 Markdown Parser。
- Understanding 返回结果：先转成 `ParseResult`，仍由本地 TreeBuilder 落盘。
- Connector 返回结果：只返回任务标识，不经过本地 `ParseResult`。
- 普通 Markdown 且 `wait=false`：返回前 Markdown 已解析并落盘，只是不等待后续语义队列。
- 网站抓取出的目录：进入 DirectoryParser，页面子文件不会逐个调用 Understanding。
