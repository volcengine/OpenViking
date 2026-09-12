# add_resource 增量更新优化完整方案

> 实施时按本文阶段逐项执行；开始编码前使用 superpowers:executing-plans 细化并跟踪任务。

**Goal:** 在保留远程正式文件存储、支持分布式部署的前提下，减少增量导入的重复暂存、远程正文读取、未变文件上传及语义/向量重算。

**Architecture:** shared 上传直接作为 SOURCE 输入；解析产物通过统一接口写入 AGFS 或可选的本地目录。完整获取目标文件树和向量元数据后，在内存中规划 diff，按差异落库，并复用未变文件的向量摘要。

**Tech Stack:** Python asyncio、ParserRouter/ParserRegistry、VikingFS/AGFS、TOS/S3、本地向量后端、现有 SOURCE/POST_PROCESS/semantic/embedding 队列。

**状态:** P1-P5 核心链路已在独立 worktree 实施并完成 40 文件真实 S3 + 本地向量库验证；代码尚未推送。历史源码锚点仅供定位，行号随实施已发生变化。

---

## 1. 目标、已确认取舍与非目标

### 1.1 六项优化

1. `temp_upload` shared 模式直接引用已有共享对象，避免下载到 API 进程后再次上传到 SOURCE 暂存。
2. 新增可选的本地解析产物模式；保留 AGFS 模式，所有解析场景尽量共用逻辑。
3. 目标文件树与向量树的交集用于正常比较；差集单独做文件/索引修复。
4. 向量记录新增文件 `md5`，增量比较优先使用，缺字段回退读取正文。
5. 目录重新理解时，优先使用向量记录中的文件 `abstract`，不依赖从目录 Markdown 反解析文件摘要。
6. 同名、同类型文件修改直接覆盖文件并更新稳定 ID 的向量，不先删除文件和索引。

### 1.2 已确认的行为

| 问题 | 决策 |
|---|---|
| shared TTL | 假设足够长；过期或对象缺失直接失败，不续期、不 pin、不增加引用计数 |
| 本地产物适用范围 | 仅在相关任务链的 workers 能访问同一机器同一路径时启用；跨机器分布式消费用 AGFS 模式 |
| 本地产物丢失 | 直接失败，不自动重新解析恢复 |
| 更新失败后的不一致 | 为简化实现，允许正式文件、数据库 MD5、摘要及向量不一致并长期保留；失败必须明确报错 |
| 失败修复 | 不新增 index_pending，不为失败窗口增加 MD5 前置失效或自动补偿；后续增量比较不保证正确，调用方须先显式 reindex 修复 |
| 正常交集数据 MD5 相同 | 跳过内容、语义、向量处理，忽略 tags、模型配置等非内容差异 |
| 旧记录没有 MD5 | 回退读取新旧文件的最终正文进行比较 |
| 文件有、索引无 | 不进入正常交集 diff；新树有则同步正确内容并补语义/向量，新树无则删文件 |
| 索引有、文件无 | 删除孤立向量；新树有该路径时随后作为新增处理 |
| MD5 定义 | URI 对应最终存储文件字节的 MD5；不是源文件、摘要、embedding 输入或目录内容的 MD5 |
| 其他写入入口 | 正常成功路径同样维护 MD5 或显式使其失效；更新失败时允许残留旧值，适用同样的人工修复边界 |
| 必需解析产物失败 | 整次镜像更新终止，不进入同步、不误删目标；不做“其余文件先更新”的部分成功。合法过滤/跳过不算失败 |
| abstract 缺失兜底 | 不保留从目录 Markdown 反解析文件摘要的兜底；向量 abstract 有则复用，无则仅在变化目录汇总确需时重新生成 |
| 目录摘要刷新失败 | 保留现有 freshness 延迟刷新策略，但不为“刷新失败”新增恢复状态机；失败同样明确报错，由显式 reindex 修复 |

### 1.3 不做的事情

- 不把正式资源存储改成本地，也不根据“本地向量库”推断整个服务是单机。
- 不取消普通本地输入现有的 SOURCE 暂存；因此该入口的上传、重新下载成本仍存在。
- 不实现解析断点续跑、跨机器搬运本地产物或新的队列恢复框架。
- 不做源文件级解析缓存：本阶段仍会解析新输入，只避免重复远程产物上传和后续计算。
- 不因为模型、提示词或 tags 改变而强制重建同 MD5 文件；需要强制刷新时使用显式维护入口。
- 不承诺远程文件存储与向量库之间存在跨系统原子事务。
- 本文只制定方案，不提交、推送代码或修改生产配置。

## 2. 当前诊断与优化目标

### 2.1 已有评测环境

独立 worktree：`.worktrees/add-resource-incremental-profile`，独立 `.venv` 和本地 workspace，每轮使用独立远程测试 prefix。

语料是当前固定提交的 OpenViking 源码子集：`openviking/storage`、`parse`、`core`、`utils` 下已跟踪 Python 文件。共 223 个文件、69,947 行、2,633,282 字节；最小 Git 元数据另有 17 个文件，源暂存实际上传 240 个文件。不是完整 Git 仓库，也未测试大 Git 历史。

后端为本地向量库与远程 TOS/S3。真实模型配置及并发保持固定；代码文件主要使用 AST 摘要，文件摘要生成次数不等于 LLM 次数。

### 2.2 已测阶段耗时

| 阶段 | 第一轮 | 第二轮 | 主要工作 |
|---|---:|---:|---|
| 源文件暂存 | 106.88 秒 | 108.73 秒 | 本地源复制为任务可引用的远程 SOURCE 快照 |
| 物化到 worker 本地 | 55.68 秒 | 57.57 秒 | 枚举远程快照并下载输入 |
| 解析及临时产物上传 | 100.65 秒 | 99.64 秒 | 构造完整新树并逐文件写 AGFS temp |
| 同步和 diff | 124.04 秒 | 112.07 秒 | 遍历新旧树、stat、读双方正文、执行同步 |
| 语义 DAG | 68.02 秒 | 59.37 秒 | 文件摘要复用/重建、目录汇总及后代任务 |
| 清理 | 10.20 秒 | 12.88 秒 | 删除源暂存和解析临时目录 |
| 全队列完成总耗时 | 473.27 秒 | 452.07 秒 | 包含父目录刷新及队列完成等待 |

这些是失败样本的诊断，不是有效的 no-op 基线：两轮各发生一次产物上传失败，异常未充分上报，缺失文件随后被当作删除，最后只剩 222 个文件。API 仍返回 success。先修复这条正确性链路，才能比较性能。

另外发现：对 222 个相同文件，sync 执行了 444 次正文读取、891 次逻辑 stat、58 次 ls；这是客户端逻辑操作数，不等于 S3 物理请求数。本地向量 upsert 的时间并集不足 1 秒，不是首要瓶颈。

### 2.3 性能目标的表达方式

优先验收工作量，再观察墙钟耗时：

- shared SOURCE 不产生第二份源内容远程暂存；worker 只下载一次共享输入。
- 本地解析模式的解析临时产物不发生 AGFS 内容写入。
- 有效 MD5 覆盖完整时，正常 diff 不读取目标文件正文，不逐文件 stat。
- 完整健康的 no-op 不上传正式内容，不调用 LLM/embedding，不重写目录摘要。
- 小比例变更只上传变化文件，只更新变化文件及必要祖先目录。

不能将阶段耗时简单相加作为可节省时间；队列及模型阶段存在重叠。

## 3. 总体架构与流程

```text
输入
 ├─ shared temp_file_id → 校验、保存共享输入引用
 ├─ 普通本地源 → 原 SOURCE 暂存逻辑
 └─ Git/HTTP/其他来源 → 保留现有获取与路由逻辑
                  ↓
SOURCE worker 获取输入到本地
                  ↓
ParseContext + ParseOutputStore
 ├─ AgfsParseOutputStore：原 AGFS temp
 └─ LocalParseOutputStore：配置的本地根目录
                  ↓
ParseArtifactRef + 完整性状态
                  ↓
确定并锁定最终目标 URI
                  ↓
公共产物规范化：布局完成、图片引用转最终 URI
                  ↓
新树 manifest：相对路径、类型、最终字节 MD5
                  ↓
完整目标文件树 + 完整目标向量元数据
                  ↓
内存中构造 DiffPlan：正常 diff + 差集修复
                  ↓
应用计划：变化文件覆盖/新增，真正删除和类型冲突清理
                  ↓
明确的 changes + 修复集合 + 缓存摘要
                  ↓
增量语义 DAG → embedding → 稳定 ID upsert
                  ↓
报告成功/失败并清理产物
```

“目标库操作在内存进行”指枚举结果、比较、摘要查找和计划构造；真正的文件上传/删除、向量更新/删除仍要调用存储后端。文件内容不必全部装入内存，manifest 主要存元数据。

## 4. 优化一：shared 上传直接作为 SOURCE 输入

### 4.1 当前路径与目标路径

当前 shared 消费先在 API 侧下载为本地文件，标准准备流程再将其复制到任务暂存，worker 又下载一次。

目标路径为：

```text
上传一次到 shared → API 校验引用 → SOURCE 消息入队 → worker 下载 → 解析
```

### 4.2 输入引用与所有权

新增明确的 shared SOURCE 引用类型，保留现有 StagedSource，不放宽其“任务私有 temp/source 目录”校验以兼容任意对象地址。

shared 引用保存服务端验证过的上传 ID、来源身份、原文件名/扩展名和定位所需元数据。不得允许客户端直接指定任意内部 AGFS URI。SOURCE 消息中的 shared、staged、prepared 输入互斥。

拆分“解析/校验上传引用”与“下载成 LocalResource”：API 只做前者，worker 才做后者。API 和 worker 均使用正确租户上下文，保留上传权限、命名和来源信息。

所有权明确区分：

- shared 远程对象由上传存储的 TTL 清理策略管理，任务不把它作为自己的 temp 删除。
- worker 下载产生的本地副本由任务清理。
- 任务自建 StagedSource 仍由原任务生命周期管理。

对象过期、元数据无效或内容缺失直接报错；不增加续期机制。普通异常失败与现有硬崩溃队列恢复语义不在此扩展。

### 4.3 代码锚点

- `openviking/server/temp_upload_store.py:247`、`:482`：引用解析与当前 shared 下载。
- `openviking/server/routers/resources.py:264`：上传 ID 到源输入的转换。
- `openviking/service/resource_service.py:904`、`:1053`、`:1168`：源计划、暂存、SOURCE 消息构造。
- `openviking/resource/staged_source.py:29`、`:76`、`:111`：保留现有任务自有源类型及下载逻辑。
- `openviking/storage/queuefs/add_resource_msg.py:25`：增加 shared 输入序列化及互斥校验。
- `openviking/storage/queuefs/add_resource_processor.py:172`：消费、失败传播和清理责任。

## 5. 优化二：统一解析产物接口，支持本地/AGFS

### 5.1 配置

建议新增独立配置，以下为拟议字段，当前版本尚不支持：

```json
{
  "storage": {
    "parse_output": {
      "mode": "local",
      "local_root": "/tmp/openviking-parse"
    }
  }
}
```

默认 `mode=agfs`；`local_root` 未指定时使用系统临时目录下的专用子目录。不能修改全局 `storage.agfs.backend` 来实现该功能。放在共享 StorageConfig，而非仅 HTTP server 配置，以覆盖直接 SDK/服务调用。

本地模式是一项部署约束，不做自动 worker 调度：所有可能接手 SOURCE、POST_PROCESS 和 semantic 同步任务的 workers 必须能访问相同本地路径。开启者负责满足约束，路径缺失即明确失败。配置检查验证模式和本地目录访问，不伪称能自动检测跨机器可达性。

### 5.2 三个核心对象

**ParseContext**：每次任务独立创建，显式传递输出 store 和产物管理范围。共享 Parser 实例上不保存任务专属目录，不切换全局 VikingFS 单例；子解析与 Understanding 并发任务继承同一输出环境。

**ParseOutputStore**：仅管理解析产物，提供以下小接口族：

| 方法族 | 契约 |
|---|---|
| create_artifact / open_artifact | 创建独立产物、按引用重开；不重建已经丢失的产物 |
| mkdir / stat / list / walk | 统一相对路径和条目类型，支持完整隐藏条目枚举 |
| read_bytes / write_bytes | 二进制为基准；文本读写是统一编码的便捷方法 |
| move / remove | 同一输出环境内部搬运及删除 |
| import_file / import_directory | 从受控本地输入导入；不能提前删除输入 |
| cleanup | 只删除本 store 拥有的产物，允许已清理后的重复调用 |

过滤、编码转换、目录扁平化、名称冲突和 sidecar 保留策略位于公共逻辑，两种后端不各自实现一套业务规则。新增模块拟为 `openviking/parse/output.py`，先保持单模块，避免不必要的目录层次。

**ParseArtifactRef**：可序列化内部引用，包含 backend、根位置和资源相对路径；产物完整性随 prepared 数据一并传递。运行时对象不写进队列。内部引用与正式 Viking URI 是不同类型，不将 `/tmp/...` 填入语义消息的正式目标 URI 字段。

### 5.3 覆盖矩阵

| 场景 | 公共汇合点/改动 |
|---|---|
| Markdown、Text | `parsers/markdown.py:457` 输出布局、`:816` 图片、`:1339` 正文；Text 委托 |
| HTML、PDF、Office/EPUB | 转换结果交给 Markdown；PDF 的显式参数列表必须补传 ParseContext，不能依赖 kwargs 自动透传 |
| 代码库 | `parsers/code/code.py:164` 与 `parsers/upload_utils.py:108`，保留过滤和编码转换，只替换输出目标 |
| 目录 | `parsers/directory.py:798` 子调用、`:820` 合并、`:934` 直接导入、`:978` flatten/冲突逻辑 |
| ZIP | `parsers/zip_parser.py:72` 解压后委托 Directory，嵌套 ZIP 同样继承上下文 |
| 图片 | `parsers/media/image.py:107` 起的原图/格式转换/预览/切片输出 |
| 音视频 | `parsers/media/audio.py:85`、`video.py:79`，写入实际产物文件 |
| Understanding API | `understanding_api.py:731`、`:782`，下载结果 ZIP 后通过公共导入接口构建产物 |
| 飞书、网页、远程 Git 等输入 | 不重复实现输出后端；输入获取之后走上述 ParserRouter/Directory/Understanding/Code 路由，并验证上下文不会丢失 |

真正语义阶段读取正式资源的 `parsers/media/utils.py` 等函数继续使用 VikingFS，不能机械替换所有 `get_viking_fs()`。

### 5.4 下游适配与生命周期

- `parse/base.py:285`：ParseResult 引用及完整性。
- `parse/parser_router.py:108`、`parse/registry.py:103`、`parsers/base_parser.py:102`：显式上下文传播。
- `parse/tree_builder.py:137`：通过 store 找产物根，正式 URI 解析仍走原逻辑。
- `utils/resource_processor.py:409`、`:509`、`:544`：首次落库、prepared 交接、单文件及非语义分支。
- `storage/queuefs/semantic_msg.py:25`：显式携带产物引用，目标 URI 保持正式 URI。
- `storage/queuefs/semantic_processor.py:833`、`:877`：打开产物、同步、统一清理。

解析工作目录、ZIP 解压目录、最终解析产物分别管理；解析函数返回后产物继续存活。子产物合并成功后清理子根，父产物由后续消费者接管。取消、失败、成功分别检查所有权，避免重复删除或提前清理。

硬崩溃可能留下本地孤儿目录；首版不按短 TTL 自动删除活跃产物。运维清理须在确认无关联活跃任务后执行，不把自动 GC 的实现混入本次性能改造。

路径必须限制在服务端专用根下，拒绝绝对子路径、`..` 越界、符号链接逃逸；保留现有安全解压与媒体路径校验。异步代码中的较大本地读写放在线程中，避免阻塞事件循环。

## 6. 产物规范化与完整性前置条件

### 6.1 最终字节再计算 MD5

`parse/image_rewrite.py:256` 当前在写入正式存储后重写图片链接。新流程将其改成“读取产物 + 传入最终目标 URI”：

1. 完成目录合并、flatten、文件命名与编码转换。
2. 确定实际目标 URI，包括自动避重后的名字，并取得相应目标锁。
3. 在产物上把图片引用转为最终 Viking URI；辅助映射在使用完后按原规则清理。
4. 对最终将写入的 bytes 计算 MD5。
5. 上传相同 bytes；manifest 完成后不再修改产物内容。

同一逻辑用于两种产物模式。MD5 是内容相等优化提示，不用于安全认证；不以对象存储 ETag 代替 MD5。

### 6.2 完整性门禁

必须先修复现有上传异常被丢弃的问题：`parsers/code/code.py:593` 的 `count, _` 会丢掉 `upload_directory` 的 warnings。公共产物层的失败应向上返回或抛出，并保留源相对路径和操作信息。

区分“按过滤规则跳过”与“必需文件处理失败”，不以全部 warnings 非空作为失败判断，也不以 temp 目录存在作为成功判断。

新树不完整、目标 tree 枚举失败、向量分页读取失败，都不能开始镜像删除。首版建议必需文件失败就终止该次镜像更新；不新增复杂的部分成功删改策略。该门禁是性能改造上线前提。

## 7. 优化三：目标快照、交集 diff 与差集修复

### 7.1 需要的数据

在现有目标锁保护下，完整取得：

- `N`：新产物中的业务文件，相对路径、类型、大小、MD5、产物引用。
- `F`：正式目标中的业务文件及目录结构。优先单次逻辑 tree 调用，必须不限总节点/深度并处理完整结果。
- `V`：目标范围内文件的有效索引记录，及生成目录所需的目录元数据；使用完整分页过滤查询，不是相似度 top-k 检索。

向量投影包含至少 `id/uri/level/abstract/md5`；不新增索引待修复状态，不拉取无关向量值。文件正常交集优先以实际文件 URI 的 L2 记录为准，目录 L0/L1、chunk、sidecar 不能被当作独立源文件。

URI 规范化、租户和权限范围必须一致。已有索引转移的完整分页工具可复用，但不能照搬其扩大父目录以搜 chunk 的范围而误删相邻资源。对无权完整查看/修改的目标子树，不能用“查询不可见”推断“索引不存在”。

文件树中的摘要文件、锁、控制元数据和辅助文件必须按统一策略分类，不能进入普通文件差集后被误删。路径类型冲突单独处理。

### 7.2 决策表

以下只描述业务文件，删除都限制在授权的实际镜像目标范围。

| 旧文件 F | 旧索引 V | 新树 N | 动作 |
|---|---|---|---|
| 有 | 有 | 有 | 正常 diff：优先比较 MD5，相同即跳过，不检查历史失败；缺 MD5 则读正文回退 |
| 有 | 有 | 无 | 删除正式文件及对应索引，父目录标记变化 |
| 有 | 无 | 有 | 差集修复：使用新树正确内容，按选定 processing_mode 补语义/向量 |
| 有 | 无 | 无 | 删除孤立文件 |
| 无 | 有 | 有 | 先删除孤立向量，再按新增文件处理 |
| 无 | 有 | 无 | 删除孤立向量 |
| 无 | 无 | 有 | 新增 |

同名文件变目录或目录变文件，执行类型替换，保留必要的删除；不是“覆盖文件”优化的适用对象。完整展开新增/删除目录中的文件变化，不能只把目录 URI 放进 changes 而遗漏子文件索引维护。

### 7.3 计划与执行分离

建议在 `storage/viking_fs/_sync.py` 中拆分共享的 DiffPlan 规划与应用逻辑，而非另建独立、不同语义的 local sync。

计划包含 added、modified、deleted、unchanged、repair_files、orphan_vectors 和结构变化。交集记录 MD5 相同即直接 unchanged，不额外检查历史失败或指纹是否陈旧；MD5 缺失时只为相关文件读取正文，比较实际 bytes。失败后未经 reindex 修复的资源不保证此比较正确。

完成所有枚举、完整性检查和比较后再执行写入。只有已成功执行的变化才进入后续语义 changes；任一执行失败上报任务失败，不将“准备执行”冒充“执行成功”。

请求内以 manifest 构造目录邻接表及摘要缓存，后续比较不再重复远程 ls/stat。真实变更应用到内存快照，必要的存储确认不能因为“在内存里操作”而省略。

## 8. 优化四：MD5 的存储、维护与迁移

### 8.1 字段链路

在文件 Context、embedding context_data、向量 schema、读写投影和本地后端存储中贯通 `md5`，允许旧记录缺失。

**计算点：MD5 在“最终 bytes 上传出口”计算，绝不为算 MD5 额外回读远程文件。** 最终 bytes 在本地可得（本地产物模式在本地磁盘、AGFS 模式为即将写入的同一 buffer），在产物规范化、图片 URI 重写完成、上传那一刻顺带 `content_md5(bytes)`，随 manifest/context 向下游传递，`vectorize_file` 只消费传入的 `file_md5`，不调用 `read_file_bytes`。该出口的接入依赖 P2c/P4 的写入改造；P3 只打通字段链路，`vectorize_file` 接受可选 `file_md5`，未提供时留空，由 diff 回退读正文。

主要锚点：

- `core/context.py:58`、`:147`、`:195`：Context 与序列化。
- `utils/embedding_utils.py:500`：文件向量化入口。
- `storage/queuefs/embedding_msg.py:10`：context_data 传递。
- `storage/collection_schemas.py:602`、`:824`：消费及稳定 ID upsert。
- `storage/viking_vector_index_backend.py:59`、`:1643`：查询投影与批量元数据访问。

复用已有批量 L2 abstract 读取思路，增加专门的增量元数据查询，不依赖默认 LOOKUP_OUTPUT_FIELDS 自动返回新字段。检查所有支持的 schema/adapter 的字段可存取性，不只改 Python 数据类。

### 8.2 已确认取舍：允许失败后文件、MD5 与索引不一致

为简化实现，本期不增加 `index_pending` 或等价的持久化索引完成状态，也不为失败窗口增加 MD5 前置失效、跨存储事务或自动补偿。正常交集记录仍按数据库 MD5 判断，相同即跳过，不额外验证历史任务是否完成或该指纹是否仍对应实际文件。

接受两类失败窗口：

```text
情况一：文件 B，MD5=B，摘要/向量仍是 A
文件及 MD5 更新成功，但后续索引失败，本次任务报错
再次导入 B 因 MD5 相同而跳过，旧摘要/向量可能长期保留

情况二：文件 B，MD5=A，摘要/向量仍是 A
文件已从 A 写成 B，但 MD5/向量更新失败，本次任务报错
再次导入 A 因数据库 MD5=A 而跳过，文件仍为 B，恢复 A 没有发生
```

因此接受的不只是旧向量，而是失败后该资源的后续增量比较也可能错误。再次调用 `add_resource` 不保证发现或修复问题，即使该次返回成功，也不证明历史不一致已消除。健康数据上的 MD5 定义不变；失败后的数据库值可能陈旧，不能作为实际文件内容的可靠证明。

调用方或运维必须根据失败结果，在继续依赖增量比较前显式执行 reindex：以当前正式文件为准重新计算并覆盖 MD5，重新生成摘要和向量，并覆盖受影响目录的语义修复范围。修复不能被 MD5 相同的快速路径跳过；应验证 `semantic_and_vectors` 模式及递归范围，不能假设 `vectors_only` 一定修复旧摘要。该能力及修复用例是实施验收要求，不表示当前 reindex 已具备新增 MD5 的维护能力。reindex 不恢复原始输入；若正式文件不是用户期望的版本，修复当前指纹和索引后仍需重新导入期望内容。

文件写入、MD5 更新、摘要生成、embedding 或 upsert 失败仍必须通过最终任务状态或 `wait=true` 明确报错，不得吞掉异常。正常路径只在文件写入成功后提交对应 MD5；允许中途失败保留旧 MD5，不要求事前清空旧值或事后自动回滚。

无索引文件不为存 MD5 专门创建空向量记录，仍通过 F−V 识别并按原差集规则处理。若某 processing_mode 明确不要求建索引，按该模式执行，不偷偷开启向量化；后续要求建索引时再按缺索引修复。该取舍不取消现有目录 freshness 策略，也不放宽新树完整性和镜像删除门禁。

异步旧任务不能覆盖新任务的数据仍是独立约束，具体协议见第 16.3 节的操作代次方案：索引提交前在目标锁内比较代次，过期写回丢弃。该代次用于拒绝过期写回，不用于实现本期已放弃的失败自动修复，也不能以稳定 ID 当作并发保障。

目前 `partial_update` 是 get→merge→upsert，并非原子字段更新（`viking_vector_index_backend.py:406`）；未证明完整锁链或条件提交前，不允许据此宣称并发安全。跨进程竞争测试是上线阻断项，不在本文假设它已解决。

### 8.3 其他写入入口覆盖

| 入口 | MD5 处理要求 |
|---|---|
| add_resource 新增/覆盖 | 使用规范化产物的最终 bytes；与当前文件内容对应 |
| content/write replace/create/append | hash 最终实际写入内容，append 不能只 hash 追加片段 |
| batch_write | 每个成功文件独立维护；失败不把全批标记完成 |
| reindex | 读取当前正式文件 bytes，重算并覆盖缺失或陈旧 MD5；失败修复需重建摘要/向量，不得因 MD5 相同跳过；单纯补指纹不等于完成修复 |
| copy/move | bytes 不变可沿用有效指纹；如果修改内部链接或正文，重新计算；URI/稳定 ID 按原规则迁移 |
| memory、skill 等可写文件路径 | 同样覆盖实际写入及向量链路，不能只修资源入口而留下同类坏字段 |
| 空内容、摘要 sidecar、图片重写 | 空文件有合法 MD5；sidecar 若有文件记录则 hash 自身 bytes，目录 L0/L1 不冒充目录文件 MD5 |
| tags/ACL 等纯元数据写入 | 不改变文件 MD5；更新时不得用旧记录回写覆盖新的摘要/MD5 |
| 回滚 | 正常路径恢复或失效对应 MD5；失败允许内容与 MD5 不一致，明确报错后人工修复，不新增自动回滚协议 |

主要入口为 `storage/content_write.py:119`、`:205`、`:1108`，`service/reindex_executor.py`，`session/memory/memory_updater.py`、`streaming_memory_updater.py`，以及 `storage/viking_fs/_ops.py` 的写入/复制/移动操作。

实施时必须完成全写入调用点审计。不能让所有 VikingFS 临时文件写入都自动访问向量库；应区分临时、控制与正式可索引文件，在共享写入协调层传递最终 bytes/指纹。绕过 OpenViking 直接改远程对象不在本次保证内，需显式 reindex。

### 8.4 老数据

不要求上线前全库读回计算。旧记录缺 MD5 时执行原正文比较；相同内容可在锁下只补元数据，避免额外 embedding。补 MD5 不证明旧摘要/向量正确；不检查历史任务完成状态，历史失败须按第 8.2 节显式修复。

字段失效需要真实可清空的语义；当前 partial_update 忽略 None，不能用传 None 以为清除了旧 MD5。使用明确失效值或后端支持的删除字段操作并加回读测试。

新版本队列字段上线采用受控升级：先停止新接入、排空旧任务，再统一升级生产者和消费者，避免旧 worker 不识别本地产物引用。旧向量数据兼容不等于新旧代码可以任意混跑。

## 9. 优化五：摘要复用与增量 DAG

### 9.1 正确的判断顺序

先按 diff 判断内容差异和索引是否存在，后决定是否复用摘要；有 abstract 不代表文件没变。不新增历史失败检测，失败后未经修复的记录适用第 8.2 节风险。

- diff 判定未变的交集文件：复用已批量加载的 L2 abstract，不再做文件 embedding。
- 文件变化/新增：生成新摘要及向量，不使用旧 abstract 掩盖内容变化。
- 文件无索引：按差集规则修复。
- 未变文件的摘要缺失：仅在变化目录汇总确实需要该文件摘要时补摘要。优先读向量 abstract 字段；取不到就重新生成摘要，不从目录 Markdown 反解析文件摘要作兜底。摘要补齐不自动意味着必须重做健康文件的 embedding。
- 历史更新失败：不通过 pending 自动识别或补处理；可能复用旧 abstract，调用方须显式 reindex 修复。

现有 L2 abstract 会截断，不能假定与最初完整摘要完全相同；以目录汇总质量回归验证是否足够。本期不额外引入完整摘要存储。

### 9.2 只处理必要范围

diff 为空且没有孤儿修复或现有目录刷新策略要求的工作时直接返回，不启动全树 DAG；不额外检查文件的历史更新失败。

非空变化集合只调度变化文件、受影响目录及其必要祖先。未变子目录可复用目录摘要；变化目录由“变化文件的新摘要 + 未变文件的已存 abstract + 子目录摘要”重新汇总。目录采样及大小策略保持原配置，不能借性能优化偷偷改摘要覆盖范围。

`changes=None` 表示未知，`changes={added:[], modified:[], deleted:[]}` 表示明确无变化；修正因 bool(empty) 而退回自比较的路径。

保留现有目录 freshness 延迟刷新策略：达到阈值前记 pending、达到后触发刷新的既有逻辑照常运行，在文件落库前后按现有协议登记。但本期不为“刷新执行失败”新增恢复状态机；目录摘要刷新失败与文件索引失败同样明确报错，由第 8.2 节的显式 reindex 修复。不能因为文件向量已更新，就漏掉当次应触发的父目录摘要刷新。

主要锚点：`storage/queuefs/semantic_dag.py:608`、`:631`、`:650`、`:762`；`semantic_processor.py:448`、`:499`、`:1177`。`utils/resource_processor.py:669` 的 vectors_only 全树向量化也要改为消费变化/修复集合。

## 10. 优化六：同名文件覆盖与稳定 ID 更新

对路径和类型均不变的修改：

1. 在目标锁约束下，用原 URI 覆盖正式文件，而不是先 rm；不登记 pending，也不为失败窗口预先清空 MD5。
2. 从最终文件 bytes 维护 MD5，从变化内容生成摘要及 embedding。
3. 验证任务仍有效后，用原稳定记录 ID 更新内容相关字段，保留按原语义应保留的身份/ACL/元数据。
4. 中途失败明确报错，允许文件、MD5 和索引不一致；不自动补偿，由调用方或运维显式修复。

稳定 ID 已在 `storage/collection_schemas.py:824` 使用 `vector_record_id(account_id, uri, level)`，不需要再造 ID 体系。新增文件正常 upsert；真正删除、类型冲突、孤立向量清理仍删除。若格式路径包含派生 chunk 等记录，重建时还要清理失效派生记录，不能只覆盖一个主记录而遗留旧项。

失败时允许正式文件、MD5、摘要和向量长期不一致，但本次失败必须上报；再次增量导入不保证正确或自动修复，恢复边界见第 8.2 节。文件覆盖的完整性依赖后端操作保证，不能把对象级覆盖解释为整棵树或文件+向量事务。

## 11. 一致性、异常与可观测性

### 11.1 锁与执行边界

- 沿用目标资源的分布式锁和 handoff；文件目标使用合适的文件锁，目录目标使用树锁。
- 解析可在获得目标写锁前进行，避免长时间占锁；最终 URI 确定、目标快照、计划应用及对应索引状态提交遵守现有锁协议。
- 在锁内重新确认目标，而不是使用排队前的陈旧快照。
- 不能只保护文件写入、不保护异步向量完成；若需要追加代次防护，只覆盖该明确竞争点。
- 源快照和解析产物完整性都要检查；本次不提供用户正在修改本地源目录时的原子输入快照。

### 11.2 失败行为

| 失败位置 | 处理 |
|---|---|
| shared 过期/缺失 | SOURCE 终结失败，不额外复制、不续期 |
| 本地产物丢失 | 终结失败，不把缺失当空目录 |
| 解析/必要产物写入失败 | 不进入同步，不误删目标 |
| tree/向量分页失败或不完整 | 不生成删除计划，不把错误当空集合 |
| 正式文件写入/删除失败 | 报失败并记录失败路径及已发生变化；不新增文件待修复状态或自动补偿 |
| MD5/摘要/embedding/upsert 更新失败 | 本次失败明确上报，允许与正式文件不一致；不新增 pending，由调用方显式 reindex 修复后再依赖增量比较 |
| 清理失败 | 保留可定位日志和清理债务，不伪造正文回滚 |

HTTP 异步接入成功只表示任务被接受；`wait=true` 或最终任务状态必须反映后代失败。错误消息包含阶段和路径，不能记录文件全文、API key 或签名下载 URL。

### 11.3 观测指标

阶段计时：输入解析、stage、materialize、parse、artifact finalize/hash、target tree、vector snapshot、diff plan、apply diff、semantic、embedding、cleanup、queue wait。

计数：新/旧文件数、索引数、交集/差集数、MD5 hit/miss/fallback、abstract hit/miss、added/modified/deleted/unchanged/repair、文件上传/下载数量和正文 bytes、逻辑存储操作、模型调用、索引写入、失败数。

修正当前探针 write bytes 的口径：按请求正文计上传字节，不按 write 返回字符串长度计。并发阶段报告墙钟跨度/时间并集，不能把各调用 duration 相加冒充总耗时。

## 12. 实施顺序与改动拆分

所有阶段先补失败测试，再实现，再跑局部及回归测试；本方案不授权自动 commit/push。

| 阶段 | 主要工作和文件 | 完成条件 |
|---|---|---|
| P0 正确性与可信基线 | code/upload_utils 的错误传播、完整性门禁、任务结果；现有 benchmark | 注入任一产物写入失败时目标不误删；获得有效 no-op 基线 |
| P1 shared 引用 | temp_upload_store、resources router、resource_service、source/message/processor | shared 无二次 stage；身份/TTL/失败/清理测试通过 |
| P2 统一产物接口 | 新 output.py；StorageConfig；ParseResult/Router/各 parser；TreeBuilder/队列交接 | 先 AGFS 行为不变，再本地模式；两种模式输出一致、异常清理一致 |
| P3 指纹与写回防护 | Context/schema/projection/embedding/write/reindex/copy/move/memory | 正常路径 MD5 与最终字节一致；缺值兼容；失败报错且显式 reindex 可修复；不新增 pending 或失败前置失效；旧队列写回不能覆盖新内容 |
| P4 统一快照与 diff | _sync.py、vector metadata 查询、resource_processor、semantic_processor | 完整决策表、类型冲突、无索引修复、分页及权限边界通过 |
| P5 增量 DAG 与覆盖 | semantic_dag、summary/vectorization、stable upsert；图片重写前移与 P3/P4 联调 | 健康 no-op 零模型调用；小变更仅处理叶子和必要祖先 |
| P6 A/B 与上线验证 | benchmark runner、混合格式集成测试、故障与并发测试 | 正确性全通过、量化收益、AGFS 和本地模式均可交付 |

P2 先引入 AGFS adapter 再引入 local adapter，便于隔离“业务行为变化”和“存储后端变化”。P3 的字段兼容、失败上报、显式修复和并发写回防护就绪前，不启用 MD5 快速跳过；P4/P5 是最终联合收益，不能以某个单阶段完成代表整个方案可上线。

各阶段的接口契约、判定规则与文件级改动清单见第 16、17 章：P0 依据 16.2，P1 依据 16.1 引用类型与第 4 章，P2 依据 16.1，P3 依据 16.3/16.6，P4 依据 16.4/16.5，P5 依据 16.5 与第 9 章。图片 URI 重写前移（17.2）必须在启用 MD5 快速比较前完成。

## 13. 测试计划

### 13.1 单元和契约测试

优先扩展现有文件：

- `tests/test_upload_utils.py`：必要上传失败不被吞掉。
- `tests/parse/test_parser_router.py`、`test_parser_config_wiring.py`、`test_directory_parser_routing.py`：输出配置和上下文贯通。
- `tests/ingest/test_parsers.py`：代表性格式的产物兼容。
- `tests/server/test_resources_temp_upload_token.py`、`test_temp_upload_store_async_io.py`：shared 消费与本地副本生命周期。
- `tests/storage/test_semantic_dag_incremental.py`：零变化、摘要复用、必要祖先、摘要缺失不默认 embedding。
- `tests/storage/test_content_write_processing_mode.py`、`tests/utils/test_resource_processor_processing_mode.py`：不同模式和所有写入分支。
- `tests/benchmark/test_ingest_profile.py`：计时/bytes/diff 统计。

新增必要的针对性测试文件：

- `tests/parse/test_parse_output.py`：两种实现共用参数化契约，路径安全、读写、合并、cleanup、序列化重开。
- `tests/storage/test_resource_sync_plan.py`：F/V/N 决策表、类型替换、完整分页失败、控制文件排除。
- `tests/storage/test_file_md5.py`：最终 bytes、append、空文件、copy/move、旧数据回退、写失败和并发状态保护。

### 13.2 集成场景

- 普通目录/代码库/ZIP/嵌套 ZIP；保留结构和 flatten；同名冲突。
- Markdown 图片、HTML、PDF、Office、Understanding ZIP、图像切片及音视频。
- 单文件目标、目录目标、initial/noop/edit/add/delete/rename/type-change。
- 本地文件源与 HTTP shared 上传分别测；确认 shared ZIP 保持原名称、格式和路由。
- F−V 新树有/无、V−F 新树有/无、旧记录缺 MD5、MD5 相同但摘要缺失。
- 相同文件仅 tags/配置改变应跳过；显式 reindex 仍能刷新。
- 两个进程对同目标写入；旧 embedding 晚于新任务完成；覆盖后杀进程；索引失败；目录摘要失败。
- 本地产物交接后才删除；提前丢失直接失败；shared 过期直接失败。
- 大量文件使 tree/向量查询跨页；同名前缀相邻资源和不同租户不能被误删。

健康起点或已完成显式修复的资源，正常成功后文件清单和 bytes 等于预期，索引 URI 集合符合当前 processing_mode，MD5 对应实际文件；本次实际发生的失败不能被报告为完整成功，旧异步任务不能覆盖新结果。历史失败未经修复的资源不适用增量正确性保证，后续 success 不能证明历史不一致已消除。

故障测试覆盖两种已接受状态：文件 B/MD5 B/旧向量 A，以及文件 B/旧 MD5 A/旧向量 A。验证原失败明确上报、重复导入可能跳过而不自动修复；显式 reindex 必须从实际文件重算 MD5、重建摘要和向量，完成后再次导入才恢复正确比较。后一种状态应复现“导入 A 被误跳过、文件仍为 B”的风险，不将这种失败遗留样本计作健康性能基线。

### 13.3 执行命令

现有回归起点，在当前 worktree 执行：

```bash
PYTHONPATH="$PWD:$PWD/sdk/python" .venv/bin/python -m pytest \
  tests/benchmark/test_ingest_profile.py \
  tests/storage/test_semantic_dag_incremental.py \
  tests/utils/test_resource_processor_processing_mode.py \
  tests/storage/test_content_write_processing_mode.py \
  tests/test_upload_utils.py -q
```

新增测试实现后逐模块运行，再执行 parse、storage、server 的相关集成测试。外部模型、Understanding、媒体依赖分别记录可用性，不能把 mock 测试通过当作真实端到端通过。

## 14. 性能评测与验收

### 14.1 版本分组

- A0：原版本，只作历史诊断；失败结果不纳入速度比。
- A1：原算法 + 必需正确性修复，建立有效基线。
- B1：shared 去二次暂存。
- B2：统一接口 + 本地产物。
- B3：快照/MD5 diff + 覆盖。
- B4：全部优化 + abstract 复用 + 增量 DAG。

A1 与 B4 是主对照，中间组用于解释收益来源。相同语料提交、过滤规则、模型/并发、向量维度、网络位置、后端缓存策略、队列配置；每组单独 workspace 和远程 prefix，不共享目标数据。

### 14.2 场景与计时口径

1. initial 建库。
2. 内容完全不变的 no-op。
3. 修改一个函数正文但保持文件大小尽量相近，避免只测 size 快速分支。
4. 修改约 1% 文件；新增、删除、重命名分别测。
5. 10% 修改与混合格式语料用于观察收益衰减和解析开销。
6. 单独执行完整性/孤儿修复场景，不与健康 no-op 混合统计。
7. 本地目录调用与真实 HTTP shared ZIP 上传分别测。

每组至少 5 个有效重复样本，报告全部样本、中位数、范围、失败率；少量样本不宣称 P95/统计显著性。每轮 initial 后按固定修改脚本执行，重复测试重新初始化到相同状态。冷启动和进程内 warm cache 分开报告。

本地服务调用记录 API 返回与所有后代任务完成时间；HTTP shared 额外报告客户端打包/上传时间及完整端到端时间。不能拿“从 temp_file_id 开始”的 shared 用时与“包含上传”的其他入口直接比较。

保留现有 runner 作为起点，扩展模式配置和故障测试。现有可运行命令示例：

```bash
PYTHONPATH="$PWD:$PWD/sdk/python" .venv/bin/python benchmark/custom/ingest_profile.py \
  --config /Users/bytedance/github_openviking/OpenViking/ov.conf \
  --output .scratch/ingest-profile/baseline-20260912-unique \
  --cases noop,edit_one,edit_1pct --timeout 1800
```

每次换唯一 output 名；配置只读加载，不打印密钥；远程清理仅限确认过的测试 prefix，另行授权执行。优化配置参数以新增 runner 接口为准，不假定当前已有。

### 14.3 硬性验收

| 场景 | 必须满足 |
|---|---|
| shared SOURCE | API 不下载共享正文再上传，任务没有第二份源暂存 |
| local parse | 解析产物正文 AGFS temp 写入数为 0 |
| 健康 no-op，MD5 齐全 | diff 目标正文读取为 0、正式文件内容写入为 0、LLM/embedding 为 0 |
| k 个普通代码文件变更 | 正式内容上传与变化文件数相符，未变文件不重做 embedding；目录任务限于必要祖先 |
| 旧数据无 MD5 | 正确回退读取，结果正确；不要求首次就达到零正文读 |
| 失败注入 | 不完整新树不能触发镜像删除；本次失败明确上报；允许失败后 MD5/文件/索引不一致及后续 diff 误判，不要求自动修复，验证显式 reindex 修复路径 |
| AGFS 模式 | 功能保留，获得目标 MD5 diff 和摘要复用收益；允许仍上传完整解析临时树 |

目录摘要自身属于变化后的派生输出，不能把“只传 k 个文件”理解为连必要的目录摘要文件也不写。initial 本身仍需完整上传。

### 14.4 收益预估与上限

目前不能给出可信的实测加速比。按诊断工作量做工程预估：

- 普通本地目录 + 本地产物 + 完整指纹：仍保留约 163–166 秒的源 stage/materialize，原先讨论的 no-op 180–240 秒可作为实验目标，而非承诺。
- shared 输入：已完成第 14.5 节的真实 HTTP ZIP 上传诊断，首次导入成功，无修改二次导入完整性失败。该样本中重复 SOURCE 暂存与 worker 物化合计约 5.61 秒，远小于逐文件源暂存；取消重复暂存的收益不能再套用本地目录的约 163–166 秒。主要优化空间在解析产物上传、远程正文 diff 和派生处理；尚无有效优化前后 A/B，不给出秒级承诺。
- AGFS 产物模式：仍有完整产物上传，收益主要来自避免目标正文读取、减少模型计算和同名删除。
- 初次导入或大比例变更：仍需上传/解析/索引大部分文件，收益明显小于健康 no-op。
- PDF/Office/Understanding 等转换开销不因本地输出消失，不能直接套用代码库比例。

若有效 A1 基线与历史诊断不同，重新修订目标；失败率和数据一致性优先于延迟。

### 14.5 shared HTTP 测试方法及已测结果（2026-09-12）

本节将 shared 上传作为独立的收益评估入口，保留失败诊断，不将其作为成功 no-op 基线。

#### 环境与请求链路

- 生产代码仍为 `98f7dfe0e39333ee9d11f83c563d59fa3a3b6a71`，未实施本文优化，仅扩展 benchmark runner 和运行时计时探针。
- 独立 worktree、`.venv`、本地 workspace、本地向量库、远程 TOS/S3 prefix `bench/add-resource/shared-medium-20260912a/`。
- 启动仅监听本机回环地址的 HTTP 服务，在独立测试命名空间创建账号，以账号级 API key 走正常鉴权，不使用 ROOT key 调用数据接口。
- 先调用 `POST /api/v1/resources/temp_upload`，multipart 字段 `upload_mode=shared`，取得并检查 `shared_` 上传 ID；再调用 `POST /api/v1/resources`，传 `temp_file_id`、固定 `to`、`wait=true`、`processing_mode=semantic_and_vectors`。
- 使用第 2.1 节相同的 223 个 Python 文件，连同最小 Git 元数据打成 ZIP，压缩包为 687,497 字节。保留 Git 标记以通过 ZIP → Directory → CodeRepositoryParser 路径处理。
- 模型为 `doubao-seed-1-6-flash-250828`（并发 32）、`doubao-embedding-vision-250615`（并发 10），与既有诊断一致。
- 首次与第二次 ZIP 的 SHA256 均为 `cc29bbeeba3c410752a4e76e09505b28ca9902366f83247e99b3d616e0f62719`，没有文件修改。

#### 计时与校验方法

通过 `TempUploadStore.save_upload`、`_resolve_shared`、SOURCE stage/materialize、解析入口、正式树落地、sync、semantic DAG、模型与向量写入等探针记录调用区间；HTTP 上传、导入返回和所有队列完成分别记录墙钟时间。

主计时从发起上传到所有队列完成，不含服务初始化、客户端 ZIP 打包和事后校验。客户端打包单独报告。对全部预期文件检查存在性，对实际存在文件逐一比对 bytes，同时核对目标 L2 索引集合，不仅检查 API success。检查失败立即停止后续场景。

先跑 3 文件冒烟，首次及无修改导入均通过。正式测试计划为 initial、noop、noop；实际在第一次 noop 校验失败后停止，第三次导入未执行。正式结果为单组诊断，不满足第 14.2 节的多次有效重复要求。

#### 各阶段实测

| 阶段 | 首次导入 | 第二次：无修改 |
|---|---:|---:|
| 客户端 ZIP 打包，单独计时 | 0.45 秒 | 0.16 秒 |
| HTTP shared 上传，含服务端存储 | 0.37 秒 | 0.54 秒 |
| API 侧解析 shared 引用并下载 ZIP | 0.67 秒 | 0.89 秒 |
| SOURCE 再次暂存 ZIP | 6.21 秒 | 5.05 秒 |
| worker 下载 SOURCE ZIP | 0.52 秒 | 0.56 秒 |
| 解压、解析及上传完整 AGFS 临时树 | 133.54 秒 | 128.90 秒 |
| 目标 URI 整理 | 0.05 秒 | 0.04 秒 |
| 首次正式资源树落地 | 119.82 秒 | — |
| 增量树同步及逐文件 diff | — | 146.05 秒 |
| 增量图片引用重写 | — | 0.18 秒 |
| 语义 DAG，含父目录处理 | 73.59 秒 | 71.79 秒 |
| 清理任务临时目录，两次调用的时间并集 | 11.63 秒 | 10.12 秒 |
| `POST /resources` 返回耗时 | 337.32 秒 | 359.27 秒 |
| 从上传开始到全部队列完成 | 348.75 秒 | 370.87 秒 |

阶段存在并行和嵌套，不能直接求和。首次图片重写未单独埋点，表中未列不代表没有执行；清理项包含任务私有暂存和产物清理，不代表 shared 对象的全部 TTL 清理开销。HTTP 为本机回环测试，不代表远程客户端上传链路的网络延迟。

第二次的主要工作量：

- 解析产物写入 223 次，其中 1 次失败，写入区间时间并集 123.21 秒。
- sync 正文读取 444 次、113.51 秒；stat 891 次、27.51 秒；ls 58 次、4.26 秒。
- 重新生成文件摘要 119 次，目录概览 21 次。
- LLM 调用 23 次、时间并集 51.93 秒；embedding 调用 161 次、时间并集 8.90 秒；本地向量 upsert 时间并集 0.98 秒。这些子工作不另行加入总耗时。
- 存储操作数为 AsyncAGFS 逻辑调用，不等于 TOS/S3 物理请求；当前探针 `write.bytes` 是返回值大小，不能作为上传正文流量。

#### 正确性结论及收益解释

首次成功：223 个文件、223 条 L2 文件索引，全部文件正文一致，无缺失或多余项。

第二次失败：解析上传出现 `AGFSInternalError`，漏掉 `openviking/parse/accessors/git_accessor.py`；随后 sync 将其当作删除。最终为 222 个文件、222 条 L2 索引，剩余 222 个文件正文一致。diff 为新增 0、修改 0、删除 1，API 仍报告 success、队列 error_count 为 0，但 runner 校验失败并以非零退出。JSON 的 `content_checked_files=223` 是计划校验项数，实际读取比对 222 个已存在文件。

本结果支持以下收益判断，而不支持优化加速比结论：

1. 当前 shared 入口确实仍重复下载和暂存；但本次 SOURCE 处理的是一个 ZIP，不是原本 240 个独立源文件。两种入口的源处理成本差异不能归功于尚未实施的优化，也不能视为 shared 与 local 上传模式的严格 A/B。
2. 此次第二轮解析产物上传所在阶段与远程 diff 合计约 274.95 秒，占总耗时约 74%，应优先由本地产物模式、MD5 比较优化。
3. 模型重算仍有明显开销，但此次已经发生误删，不能把全部语义工作都归因于健康 no-op 的摘要复用失败。
4. 后续收益验收必须继续使用相同 shared ZIP 请求链路，在正确性修复后的 A1 与优化后的 B4 上重跑有效样本；同时保留普通本地目录组，分别报告，不混算。

#### 复跑与产物

runner 已支持 `--entry shared_http`；使用新的 output 名执行：

```bash
PYTHONPATH="$PWD:$PWD/sdk/python" .venv/bin/python benchmark/custom/ingest_profile.py \
  --entry shared_http \
  --config /Users/bytedance/github_openviking/OpenViking/ov.conf \
  --output .scratch/ingest-profile/shared-medium-<unique-run-name> \
  --cases noop,noop --timeout 1800
```

命令中的 `<unique-run-name>` 必须先替换为实际唯一名称。小规模冒烟可加 `--limit 3`；修改场景使用 `--cases noop,edit_one,edit_1pct`。当前未修复误删问题，仍可能因校验失败提前停止。

本次证据相对 worktree 路径：

- `.scratch/ingest-profile/shared-medium-20260912a/environment.json`
- `.scratch/ingest-profile/shared-medium-20260912a/00-initial.json`
- `.scratch/ingest-profile/shared-medium-20260912a/01-noop.json`
- `.scratch/ingest-profile/shared-medium-20260912a/report.txt`
- `.scratch/ingest-profile/shared-medium-20260912a.log`
- `benchmark/custom/ingest_profile.py`、`tests/benchmark/test_ingest_profile.py`

### 14.6 实施后有效样本（P1-P5）

在本地向量库、S3 正式文件存储、`temp_upload=shared`、`parse_output=local` 下，使用 40 个已跟踪 Python 文件（420,750 bytes、11,532 行）执行 initial、健康 no-op、单文件修改。每轮 shared 上传后由 benchmark 固定等待 5 秒再调用 `add_resources`，用于隔离测试对象存储传播时序；该等待只属于客户端评测准备，不计入 `upload_http_s`、`ingest_http_s` 或阶段耗时。正式 shared 消费代码不增加重试。

| 场景 | 端到端耗时（含 5s 评测等待） | shared 上传 | shared 物化 | 本地解析 | local persist/diff | 语义 DAG | 文件摘要 | overview | embedding |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| initial | 62.82s | 0.20s | 0.19s | 0.28s | persist 4.52s | 48.93s | 40 | 8 | 56 |
| no-op | 11.48s | 0.11s | 0.12s | 0.18s | diff 0.04s | 3.43s | 0 | 0 | 0 |
| edit-one | 42.33s | 0.52s | 0.10s | 0.17s | diff 0.13s | 30.85s | 1 | 4 | 9 |

三轮均满足：40/40 正式文件可见、40/40 文件正文一致、40/40 L2 索引存在，无缺失、无多余文件/向量、队列 error_count 为 0。本地 `parse-out` 在任务终态为空。另以 8 文件重复 4 轮验证 S3 写后列举窗口：其中 3 轮上传后立即 `ls(root)=[]`，四轮仍全部成功并得到 8/8 L2，证明 local 模式的语义文件发现不再依赖刚写入 S3 的目录列举。

no-op 相对同一代码版本下普通本地入口（仍有 SOURCE 暂存和 worker 物化）的 50.92s 降至 11.48s，减少 39.44s（77.5%）；11.48s 包含人为等待，扣除后约为 6.48s。其中普通本地入口的 SOURCE 暂存约 29.97s、物化约 11.11s。该对比的入口不同，用于量化 shared 对源暂存/物化的收益，不是“仅 local parse_output”的隔离 A/B。对历史 8 文件 AGFS 产物 no-op 样本 81.98s，当前 shared+local 40 文件 no-op 仍只需 11.48s；两者文件规模、代码版本和运行时缓存不同，只能说明量级变化，不能作为严格 A/B 加速比。当前每个场景仅 1 个有效样本，证明功能与工作量收敛，不宣称中位数、P95 或统计显著性；严格 A/B 仍需在相同代码版本、相同 shared 入口、相同语料下分别运行 AGFS/local 产物模式至少 5 次。

此次还确认并修复 shared SOURCE 的身份问题：API 校验上传引用时使用内部 ROOT，同一引用交给 SOURCE worker 后原先却用普通 USER 身份读取 `viking://upload/...`，稳定触发 `PermissionDeniedError`。worker 现在使用同账户、同用户的内部 ROOT 上下文读取上传区；没有在正式代码中增加重试。

实施后证据目录：

- `.scratch/ingest-profile/shared-local-final40-v2-20260913-010502/environment.json`
- `.scratch/ingest-profile/shared-local-final40-v2-20260913-010502/00-initial.json`
- `.scratch/ingest-profile/shared-local-final40-v2-20260913-010502/01-noop.json`
- `.scratch/ingest-profile/shared-local-final40-v2-20260913-010502/02-edit_one.json`

当前样本说明文件级增量已经收敛：no-op 不重算文件摘要和向量，单文件修改只重新生成 1 个文件摘要。单文件修改仍有 4 次目录 overview LLM 调用及父级刷新，约 30.85s 的语义 DAG 是下一阶段最明显的优化空间。

## 15. 风险和发布条件

| 风险 | 影响 | 控制措施 |
|---|---|---|
| 产物不完整被视为删除 | 数据丢失 | 完整性门禁，异常传播，先计划后执行 |
| 路径抽象混淆 | 本地路径泄漏、越界、错误清理 | 类型化引用、相对路径、根目录约束 |
| MD5 不是最终 bytes | 每次误判变化或错误跳过 | 图片重写/编码转换前移，最终字节契约 |
| 文件变更而 MD5/索引更新失败 | 旧向量可能长期复用；陈旧 MD5 可能使后续导入错误跳过文件修改 | 已接受以简化实现；不新增 pending、前置失效或自动补偿，失败报错，调用方须显式 reindex 后再依赖增量比较 |
| 旧异步任务覆盖新记录 | MD5/摘要/向量错配 | 目标锁、任务身份/代次核对及跨进程测试 |
| 分页/权限导致假缺失 | 误删向量或文件 | 完整快照、范围校验、错误不当空集合 |
| 本地目录不被下游共享 | 消费失败 | 默认 AGFS，local 为明确部署配置 |
| 摘要截断或缺失 | 目录理解质量变化 | 代表性摘要质量回归，按需补摘要 |
| 新旧 worker 混跑 | 队列反序列化或状态协议不一致 | 停接入、排空、统一升级后启用新模式 |

发布顺序：完成 P0 并保存可信基线；统一产物接口先验证 AGFS 行为；指纹、失败上报、显式修复、并发写回防护与写入覆盖通过后启用快速比较；最后在指定单机 worker 部署开启 local。切换回 AGFS 需要先排空已有 local 产物任务，不能在任务中途删除本地目录。

验收交付应包含代码与测试、有效 A/B 数据、阶段/操作计数、失败率、语义正确性抽查和配置示例。开始实施不自动代表获准访问生产资源、升级线上或推送代码。

## 16. 技术规格细化（编码前需落实）

本章把前文的原则细化为可实现规格。除非本章另有约束，行为遵循第 1.2 节的已确认取舍。

### 16.1 统一解析输出接口契约

单模块 `openviking/parse/output.py`，包含引用类型、抽象基类、两种后端实现和公共规范化逻辑。业务规则（过滤、编码、flatten、名称冲突、sidecar 保留）只写一份，两种后端仅提供字节级读写与目录操作。

**ParseArtifactRef（可序列化）字段：**

| 字段 | 含义 |
|---|---|
| `backend` | `"agfs"` 或 `"local"` |
| `root` | 后端内根位置：AGFS 为 `viking://temp/<uuid>`，local 为受控根下子目录绝对路径 |
| `resource_rel` | 产物根相对 `root` 的资源相对路径（如 `repository`） |
| `root_type` | `"dir"` 或 `"file"` |
| `integrity` | 见 16.2；随 prepared 数据传递，不写运行时对象进队列 |

运行时对象（打开的 store、文件句柄、锁）不进入 `ParseArtifactRef`，也不进入队列消息。队列只携带 `ParseArtifactRef` 与正式目标 URI；`/tmp/...` 或 `viking://temp/...` 不得填入语义消息的正式目标 URI 字段。

**ParseOutputStore 抽象方法（二进制为基准）：**

```text
create_artifact(root_type) -> ParseArtifactRef
open_artifact(ref) -> None            # 不重建已丢失产物，缺失即抛
stat(rel) / list(rel) / walk(rel)     # 条目类型统一：dir/file/symlink；隐藏项全枚举
read_bytes(rel) / write_bytes(rel, data)
mkdir(rel) / move(src_rel, dst_rel) / remove(rel)
import_file(local_path, rel) / import_directory(local_path, rel_base)
cleanup()                             # 只删本 store 拥有产物，可重复调用
```

文本便捷方法 `read_text/write_text` 用统一编码，内部走 `*_bytes`。`import_*` 不得提前删除输入源。较大本地读写用线程执行，避免阻塞事件循环。

**公共规范化函数（后端无关）：** 目录扫描与过滤复用 `directory_scan` 与 `upload_utils.should_skip_file` 的现有语义，不复制一套；扁平化、名称冲突避重、sidecar 保留、编码转换在此统一实现，两种后端调用同一函数。

**大小写与冲突：** macOS 大小写不敏感文件系统上，`a.py` 与 `A.py` 视为冲突，规范化阶段显式检测并报错，不依赖后端静默覆盖。

**路径安全：** local 后端所有 `rel` 经 `sanitize_relative_viking_path` 同源校验，拒绝绝对路径、`..` 越界、符号链接逃逸；根目录限制在配置的 `local_root` 下。

### 16.2 完整性状态与门禁判定

产物完整性用结构化状态，不用 `warnings` 是否为空判断。

```text
ArtifactIntegrity:
  required_failures: list[FailedItem]   # 必需产物处理失败（致命）
  filtered: list[FilteredItem]          # 按规则跳过（正常，不致命）
  produced_count: int                   # 实际成功产物数
FailedItem: { rel_path, op, error }
```

判定规则：

- `required_failures` 非空 → 门禁失败，抛出并终止本次镜像更新，不进入 diff/同步。
- `filtered` 非空、`required_failures` 空 → 正常，可继续。
- `produced_count == 0` 且期望非空 → 门禁失败。
- 不以 temp 目录是否存在作为成功判断。

**改造点：** `upload_utils.upload_directory` 已返回 `(count, warnings)`，其中 `warnings` 混合了“跳过”和“写失败”。新增区分：写失败进 `required_failures`，过滤进 `filtered`。`code.py:593` 的 `count, _` 必须接收并向上传递失败项，不能丢弃。子 parser 失败经 `directory.py` 的 `failed_files` 汇总为父级 `required_failures`，保留 `rel_path`。

### 16.3 异步写回防护协议

目标：正常执行的新任务结果不被过期旧任务覆盖；删除后旧任务不得复活索引。仅解决过期提交，不做失败自动补偿。

**操作代次（op generation）：**

- 每次对某 URI 的写入操作，在目标锁内读取并递增一个持久化的 `op_gen`（随向量记录存储，缺失视为 0）。
- 该次派生的 embedding 消息携带 `expected_op_gen`。
- embedding/upsert 提交时，在目标锁内重新读取当前 `op_gen`；仅当等于 `expected_op_gen` 才写入并保持代次，否则丢弃本次写回（记日志，不报错）。
- 删除操作同样递增 `op_gen`；删除后到达的旧写回因代次不匹配被拒绝，不会复活记录。

**临界区：** “读代次 → 比较 → upsert” 必须在同一目标锁持有期内完成。当前 `partial_update`（`viking_vector_index_backend.py:406`）是 get→merge→upsert 非原子，不能作为并发保障；代次比较必须由锁保证，不能仅靠稳定 ID。

**范围：** 只覆盖“同一 URI 的并发写入”这一竞争点，不追加其他同步机制。跨进程竞争测试是上线阻断项（见 16.7）。

### 16.4 完整目标快照约束

**文件树 F：** 单次逻辑 tree 调用必须显式 `node_limit=None`、`level_limit=None`，处理完整结果。默认 1000 节点/3 层（`viking_fs/_ops.py:1345`）不可沿用。若后端返回被截断标志或达到隐式上限，视为快照不完整，禁止生成删除计划。

**权限隐藏：** 若 tree/ACL 过滤（`_access.py:501`、`:574`）可能隐藏部分后代，不能把“查询不可见”当作“不存在”。镜像删除范围仅限调用方对整棵子树有完整可见与可改权限的情况；无法确认完整性时，按 16.2 门禁失败处理，不删除。

**缓存新鲜度：** 拿到目标锁后再取快照；若 ragfs 缓存（`cache/wrapper.rs:121`、`:1249`）可能返回锁前陈旧视图，需按缓存模式确认或绕过缓存重取。不能假设“持锁即最新”。

**向量快照 V：** 用完整分页过滤查询（非 top-k 相似检索），投影 `id/uri/level/abstract/md5`。分页失败或不完整 → 快照失败，不当空集合。复用已有分页工具但收窄到本资源范围，不扩大父目录搜 chunk，避免误删相邻资源。

**控制文件分类：** `.overview.md`、`.abstract.md`、锁、控制元数据、sidecar 单独分类，不进入业务文件差集；路径类型冲突（文件↔目录）单独处理。

### 16.5 processing_mode 覆盖矩阵

diff 与快照优化必须覆盖所有落库路径，不能只优化 semantic 队列：

| 路径 | 落库入口 | diff 应用点 |
|---|---|---|
| 目录 semantic_and_vectors | `resource_processor.finish_prepared_resource` → semantic DAG | 统一 DiffPlan |
| 目录 vectors_only | `resource_processor.py:669` 全树向量化 | 改为消费 DiffPlan 的变化/修复集合，不再全树 |
| 单文件目标 | `resource_processor.py:544` 单文件分支 | 单文件 diff（比对 MD5/正文） |
| 不建索引模式 | 按模式跳过向量 | 只做文件 diff，不偷偷建索引 |

各路径复用同一 DiffPlan 构造与应用函数（16.6 / 第 7.3 节），仅在“是否派生语义/向量”上分叉。

### 16.6 MD5 字段链路与迁移

**写入时机：** MD5 随向量记录字段一起提交，不新增独立数据库往返。文件写入成功后计算最终 bytes 的 MD5，注入 embedding `context_data`，最终由 upsert 落库。无向量更新的写入入口（如纯 content/write）按 8.3 表各自维护。

**schema 分级：**

- 集合缺 `md5` 字段：local/cuvs 后端走现有 `update_collection_schema`（`collection_schemas.py:333`）自动加列；volcengine 数据面（`:298`）需带外预建，启动时校验字段存在，缺失则明确报错而非静默降级。
- 记录缺 `md5` 值（字段已存在）：按第 8.2 节回退正文比较，允许旧记录长期缺值。

**投影：** 增量元数据查询显式列出 `md5`，不依赖默认 `LOOKUP_OUTPUT_FIELDS`。

**字段失效：** `partial_update` 忽略 `None`（`viking_vector_index_backend.py:438`），不能用传 `None` 清除旧 MD5；用明确失效值或后端删除字段操作，并加回读测试。

### 16.7 显式修复入口（reindex）验证

失败后修复依赖 reindex，必须验证其真正重建而非复用旧摘要：

- 修复须走 `semantic_and_vectors`（`reindex_executor.py:948`）并覆盖受影响目录递归范围；`vectors_only` 只重嵌旧摘要，不足以修复内容变化。
- `reindex_executor.py:1798` 的 `_best_file_summary` 优先读旧目录 overview / 旧向量 abstract；修复场景必须以当前正式文件正文为准重算 MD5 与摘要，不能被旧摘要短路。
- reindex 完成后，MD5 与正式文件一致、摘要与向量对应当前内容，再次增量导入恢复正确比较。
- 上线阻断测试：两进程并发写同一 URI；旧 embedding 晚于新任务完成（16.3 代次拒绝）；覆盖后杀进程；跨进程竞争。

## 17. 代码组织与设计

目标：清晰、可复用、避免重复改动。核心是“公共逻辑一份、后端差异最小化、diff 规划与执行分离”。

### 17.1 新增文件

| 文件 | 职责 |
|---|---|
| `openviking/parse/output.py` | ParseArtifactRef、ParseOutputStore 抽象、AgfsParseOutputStore、LocalParseOutputStore、公共规范化与完整性状态（16.1/16.2） |
| `openviking/resource/shared_source.py` | shared SOURCE 引用类型与校验（与现有 `staged_source.py` 并列，不改其语义） |
| `openviking/storage/viking_fs/_diff_plan.py` | DiffPlan 数据结构与纯规划函数（无 I/O），供 `_sync.py` 调用 |

配置字段 `storage.parse_output.{mode,local_root}` 加入现有 StorageConfig 模型，不新建配置体系。

### 17.2 改动文件与最小接口

| 文件 | 改动要点 |
|---|---|
| `parse/base.py` | `ParseResult` 增加 `artifact_ref: ParseArtifactRef | None`、`integrity: ArtifactIntegrity | None`；`success` 改为依据 `integrity.required_failures` 而非 `warnings` |
| `parse/parser_router.py`、`registry.py`、`parsers/base_parser.py` | `parse(...)` 显式透传 `ParseContext`，不靠 kwargs 自动带 |
| `parsers/code/code.py`、`parsers/upload_utils.py` | `upload_directory` 返回结构化 `(produced, filtered, required_failures)`；`code.py` 接收并传递失败项 |
| `parsers/directory.py`、`zip_parser.py`、`markdown.py`、`media/*` | 输出目标改为经 store 写；业务过滤/flatten 调用 16.1 公共函数 |
| `parse/image_rewrite.py` | 图片 URI 重写前移到“最终 URI 确定后、MD5 计算前”，两种后端共用 |
| `parse/tree_builder.py` | 经 `ParseArtifactRef` 定位产物根，正式 URI 解析逻辑不变 |
| `utils/resource_processor.py` | prepared 交接携带 `artifact_ref/integrity`；接门禁；vectors_only/单文件分支接 DiffPlan |
| `storage/viking_fs/_sync.py` | 拆分规划（调用 `_diff_plan.py`）与执行；先枚举完再执行，只有已执行变化进 changes |
| `storage/collection_schemas.py`、`viking_vector_index_backend.py` | `md5` 字段、投影、代次比较提交 |
| `core/context.py`、`storage/queuefs/embedding_msg.py`、`utils/embedding_utils.py` | `md5` 与 `expected_op_gen` 贯通 context→msg→upsert |
| `server/temp_upload_store.py`、`server/routers/resources.py`、`service/resource_service.py`、`storage/queuefs/add_resource_msg.py`、`add_resource_processor.py` | shared 引用直连 SOURCE，API 只校验、worker 才下载 |

### 17.3 复用与防重复原则

- diff 决策表（第 7.2 节）只在 `_diff_plan.py` 实现一次；`_sync.py`、resource_processor、reindex 都调用它，不各写一份比较逻辑。
- 过滤/编码/flatten/skip 规则只在 16.1 公共函数实现；parser 与两种后端不重复。
- AGFS 与 local 后端只在字节读写、mkdir/move/remove 上有差异；任何业务判断出现在后端实现里都属于设计错误，应上移到公共层。
- MD5 计算封装为单一 helper（输入最终 bytes，输出十六进制），所有写入入口调用同一个，不各自 `hashlib`。
- 代次比较封装为单一“锁内条件提交”helper，写路径复用。

### 17.4 实现顺序内的设计校验

每个 P 阶段合入前自查：是否引入了与已有函数重复的过滤/比较/hash 逻辑；后端实现里是否混入业务规则；是否有临时/正式路径类型被字符串拼接混用。发现即上移或合并，不留“先复制后统一”的债务。
