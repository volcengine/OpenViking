# 添加资源后的解析路由

本文描述当前 `add_resource` 从收到资源到落盘、建索引的真实执行链。重点回答四个问题：入口在哪里分流、资源类型在哪里确定、Understanding 与 Connector 分别做什么，以及 `wait` 到底等待什么。

## 先记住四条规则

1. 对外只有一个兼容入口：`ResourceService.add_resource`。SDK、HTTP API、MCP 最终都应调用它；内部 Parser 类和注册方法不是兼容接口。
2. Connector 是一条独立的端到端导入链；Understanding 只是标准链里的一个 Parser 后端。
3. 文件只做一次 Parser 选择。选择依据是 Accessor 获取资源后冻结的 `resolved_extension`，不是临时文件名，也不会在队列消费者里重新猜。
4. 目录、网站目录以及内部 Parser 遍历出的子文件不逐个调用 Understanding，统一留在内置 Parser 链内处理。

## 总流程

```text
SDK / HTTP API / MCP
        |
        v
ResourceService.add_resource                  对外入口，只负责顶层分流
        |
        +-- Connector 命中且参数受支持 ------> Connector doc/add
        |                                         |
        |                                         +--> 轮询 Connector task/info
        |                                         +--> Connector 自己解析并写入资源
        |
        +-- Git && wait=false ----------------> 预检仓库 + 预占目标 URI
        |                                         |
        |                                         +--> 后台 _add_resource(route_source=false)
        |
        +-- HTTP 服务请求 && wait=false && Understanding 已启用
        |       |
        |       +--> Accessor 下载并确定真实类型
        |               |
        |               +-- 命中 Understanding 扩展名 --> 预占 URI
        |               |                            --> EXTERNAL_PARSE 队列
        |               |                            --> Worker 强制 Understanding
        |               |
        |               +-- 未命中 ------------------> 复用已下载 LocalResource
        |
        +-- 标准执行链
                |
                v
        ResourceProcessor.process_resource
                |
                v
        UnifiedResourceProcessor
                |
                +-- 原始文本 -----------------------> 内置 ParserRegistry
                |
                +-- 路径 / URL --> AccessorRegistry --> LocalResource
                                          |
                                          +-- 目录 --> DirectoryParser（内置）
                                          |
                                          +-- 文件 --> ParserRouter（只选一次）
                                                          |
                                                          +-- 内置 ParserRegistry
                                                          +-- Understanding API
                |
                v
             ParseResult
                |
                v
        TreeBuilder 落盘 --> 可选摘要 --> 语义队列 / 向量索引
```

公开入口分流完成后，Git 后台任务会直接进入私有 `_add_resource(route_source=false)`，不会递归调用公开入口，因此不会再次触发 Connector 或 Git 顶层分流。

## 顶层路由表

| 输入或场景 | 获取数据 | 解析者 | 是否走标准 `ParseResult -> TreeBuilder` | 返回时机 |
|---|---|---|---|---|
| Connector 配置允许的 scheme，且参数受支持 | Connector 服务 | Connector 服务 | 否 | 提交成功立即返回 `task_id`，后台轮询状态 |
| `tos://` 但 Connector 不可用或参数不支持 | 不降级 | 不执行 | 否 | 直接报清晰的参数或配置错误 |
| Git，`wait=false` | GitAccessor 在后台 clone | 内置目录/代码仓库 Parser | 是 | 预检仓库并预占 URI 后返回 |
| Git，`wait=true` | GitAccessor | 内置目录/代码仓库 Parser | 是 | 解析、落盘及语义队列完成后返回 |
| HTTP 服务请求，`wait=false`，命中 Understanding | HTTPAccessor 先识别类型；Worker 再获取源 | Understanding | 是 | 类型识别、URI 预占和入队后返回 |
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

Accessor 的产物统一是 `LocalResource`，包含本地文件或目录路径、`source_type`、原始来源、是否需要清理，以及检测元数据。Parser 不再负责 clone、下载或识别飞书链接。

### 飞书资源的 Accessor 链路

飞书是“远程私有数据源”，不是一种本地文件格式，因此统一由 `FeishuAccessor` 获取并归一化：

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

预期支持范围如下：

| URL 类型 | Accessor 行为 | 当前边界 |
|---|---|---|
| `docx/{token}` | 拉取文档 block，转换标题、列表、代码、表格、图片和内嵌 sheet | 图片下载受 `download_images` 控制；内嵌 sheet 最多读取 100 行、A-Z 列 |
| `sheets/{token}` | 枚举所有 sheet，把单元格转换成 Markdown 表格 | 每个 sheet 最多读取 `max_rows_per_sheet` 行、A-Z 列，截断会写入 Markdown 提示 |
| `base/{app_token}` | 枚举数据表、字段和记录，把每张表转换成 Markdown 表格 | 表和字段完整翻页；每张表最多读取 `max_records_per_table` 条记录，截断会写入提示 |
| `wiki/{token}` | 先解析 wiki 节点的实际类型和 token，再进入上述 `docx`、`sheets` 或 `base` 处理器 | wiki 指向其他飞书对象类型时明确报不支持 |

四类入口都支持应用凭证获取的 tenant token；显式传入 `args.feishu_access_token` 时，同一 user token 会用于 wiki 解析、正文、sheet/base 和图片请求。飞书专有逻辑到 `FeishuAccessor` 为止，后面只处理本地 Markdown，因此不再保留并行的 `FeishuParser` 入口。

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

因此，`wait=false` 不等于“完全不碰源站就返回”。HTTP 服务收到无后缀远程 URL、且 Understanding 已启用时，必须先下载或探测一次，才能知道应进入外部解析队列还是内置 Parser。当前外部队列 Worker 会再次获取远程源；这是为了不引入持久化临时输入的额外生命周期，后续只有在重复传输成为实际瓶颈时再优化。

## Parser：只回答“本地内容怎么解析”

标准执行链分三类：

- 原始文本：直接进入内置 `ParserRegistry`。
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

同步路径中，Accessor 已下载的本地文件会直接上传给 Understanding，`original_source` 只保留作来源元数据。异步路径中，`UnderstandingParseMsg` 携带冻结的 `resolved_extension`；Worker 使用 `parser_backend="understanding"` 强制选择该后端，避免下载后的临时路径或配置变化让任务重新路由。

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
| HTTP 服务 + 异步 Understanding | 先识别类型，再预占 URI、入外部解析队列后返回 | 当前请求内调用 Understanding，再等待后续队列 |
| 普通标准链 | 解析和落盘完成后返回；摘要/语义处理由任务监控 | 解析和落盘完成后继续等待语义队列 |

所以普通 `wait=false` 不是“所有工作后台化”，而是“资源树已落盘，但不阻塞等待后续语义任务”。Git 与异步 Understanding 是两个明确的例外分支。

## 目标 URI、锁和失败边界

- 需要后台执行的 Git 与 Understanding 任务会先规划并预占目标 URI，避免返回的 URI 随后台竞态变化。
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
| Understanding 异步消息与 Worker | `openviking/storage/queuefs/understanding_parse_msg.py`、`understanding_parse_processor.py` |
| Connector 客户端 | `openviking/connector/client.py`：`ConnectorClient` |

## 快速自检

- 无后缀 PDF URL：HTTPAccessor 从响应头或 PDF 签名得到 `.pdf`，ParserRouter 再决定是否使用 Understanding。
- Understanding 返回结果：先转成 `ParseResult`，仍由本地 TreeBuilder 落盘。
- Connector 返回结果：只返回任务标识，不经过本地 `ParseResult`。
- 普通 Markdown 且 `wait=false`：返回前 Markdown 已解析并落盘，只是不等待后续语义队列。
- 网站抓取出的目录：进入 DirectoryParser，页面子文件不会逐个调用 Understanding。
