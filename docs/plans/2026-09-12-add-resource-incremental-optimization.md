# add_resource 增量更新优化完整方案

> 实施时按本文阶段逐项执行；开始编码前使用 superpowers:executing-plans 细化并跟踪任务。

**Goal:** 在保留远程正式文件存储、支持分布式部署的前提下，减少增量导入的重复暂存、远程正文读取、未变文件上传及语义/向量重算。

**Architecture:** shared 上传直接作为 SOURCE 输入；解析产物通过统一接口写入 AGFS 或可选的本地目录。完整获取目标文件树和向量元数据后，在内存中规划 diff，按差异落库，并复用未变文件的向量摘要。

**Tech Stack:** Python asyncio、ParserRouter/ParserRegistry、VikingFS/AGFS、TOS/S3、本地向量后端、现有 SOURCE/POST_PROCESS/semantic/embedding 队列。

**状态:** P1-P5 核心链路及第 18 章的 SemanticPlan、同步前移、最小增量 DAG 已在独立 worktree 实施，并完成 shared HTTP + 真实 S3 + 本地向量库验证。远程分支已有 P1-P5 提交 `a1e56aca9`，SemanticPlan 阶段在最终验证后追加提交；历史源码锚点仅供定位，行号随实施已发生变化。

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
| 更新失败后的不一致 | 为简化实现，允许正式文件、数据库 MD5、摘要及向量不一致并长期保留；文件树提交和计划入队失败必须明确报错，队列内语义节点失败沿用原行为记录并跳过 |
| 失败修复 | 不新增 index_pending，不为失败窗口增加 MD5 前置失效或自动补偿；后续增量比较不保证正确，调用方须先显式 reindex 修复 |
| 正常交集数据 MD5 相同 | 跳过内容、语义、向量处理，忽略 tags、模型配置等非内容差异 |
| 旧记录没有 MD5 | 回退读取新旧文件的最终正文进行比较 |
| 文件有、索引无 | 不进入正常交集 diff；新树有则同步正确内容并补语义/向量，新树无则删文件 |
| 索引有、文件无 | 删除孤立向量；新树有该路径时随后作为新增处理 |
| MD5 定义 | URI 对应最终存储文件字节的 MD5；不是源文件、摘要、embedding 输入或目录内容的 MD5 |
| 其他写入入口 | 正常成功路径同样维护 MD5 或显式使其失效；更新失败时允许残留旧值，适用同样的人工修复边界 |
| 必需解析产物失败 | 整次镜像更新终止，不进入同步、不误删目标；不做“其余文件先更新”的部分成功。合法过滤/跳过不算失败 |
| abstract 缺失兜底 | 不保留从目录 Markdown 反解析文件摘要的兜底；向量 abstract 有则复用，无则仅在变化目录汇总确需时重新生成 |
| 目录摘要刷新失败 | 保留现有 freshness 延迟刷新策略和容错语义；记录失败并跳过当前节点，不升级为整次 `add_resources` 失败，由显式 reindex 修复 |

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
- `V`：目标目录范围内全部 L0/L1/L2 索引记录；使用完整分页过滤查询，不是相似度 top-k 检索。第一次 inventory 只取 `id/uri/level/md5`，diff 和裁剪后再按必要 ID 查询摘要及其他非向量标量。

第一次向量投影为 `id/uri/level/md5`，必须覆盖 L0/L1/L2：L2 用于文件 diff，L0 ID 用于后续获取未变化子目录摘要，L0/L1 ID 用于受影响目录的更新与删除。第二次 hydration 才投影 `abstract` 等必要非向量字段；两次都不拉取 dense/sparse vector。文件正常交集只以实际文件 URI 的 L2 记录为准，目录 L0/L1、chunk、sidecar 不能被当作独立源文件。

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
文件及 MD5 更新成功，但后续索引失败；队列记录失败，调用方不应把接入成功视为索引完整
再次导入 B 因 MD5 相同而跳过，旧摘要/向量可能长期保留

情况二：文件 B，MD5=A，摘要/向量仍是 A
文件已从 A 写成 B，但 MD5/向量更新失败；队列记录失败，允许不一致保留
再次导入 A 因数据库 MD5=A 而跳过，文件仍为 B，恢复 A 没有发生
```

因此接受的不只是旧向量，而是失败后该资源的后续增量比较也可能错误。再次调用 `add_resource` 不保证发现或修复问题，即使该次返回成功，也不证明历史不一致已消除。健康数据上的 MD5 定义不变；失败后的数据库值可能陈旧，不能作为实际文件内容的可靠证明。

调用方或运维必须根据失败结果，在继续依赖增量比较前显式执行 reindex：以当前正式文件为准重新计算并覆盖 MD5，重新生成摘要和向量，并覆盖受影响目录的语义修复范围。修复不能被 MD5 相同的快速路径跳过；应验证 `semantic_and_vectors` 模式及递归范围，不能假设 `vectors_only` 一定修复旧摘要。该能力及修复用例是实施验收要求，不表示当前 reindex 已具备新增 MD5 的维护能力。reindex 不恢复原始输入；若正式文件不是用户期望的版本，修复当前指纹和索引后仍需重新导入期望内容。

解析完整性检查、正式文件树提交和 `SemanticPlan` 消息入队失败必须通过最终任务状态或 `wait=true` 明确报错；这些失败意味着安全的增量任务尚未建立。消息成功入队后的文件摘要、目录摘要、embedding 或 upsert 则沿用现有节点级容错：记录失败并跳过该节点，不反向回滚正式文件，也不保证整次 `add_resources` 因该节点失败而失败。正常路径只在文件写入成功后提交对应 MD5；允许中途失败保留旧 MD5，不要求事前清空旧值或事后自动回滚。

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

上述为目标行为，不代表当前实现已经完成目录级剪枝。当前 `SemanticDagExecutor` 在 `recursive=True` 时仍会枚举并调度所有子目录及其直接文件；`changes` 主要用于在文件节点内部跳过正文、LLM 和 embedding。因此当前增量虽然能将模型调用收敛到变化文件，目录 `ls`、DAG 节点创建、未变摘要读取和状态维护仍与整棵资源树规模相关。第 18 章的带状态裁剪 tree snapshot 必须补齐真正的受影响路径最小 DAG。

`changes=None` 表示未知，`changes={added:[], modified:[], deleted:[]}` 表示明确无变化；修正因 bool(empty) 而退回自比较的路径。

保留现有目录 freshness 延迟刷新策略：达到阈值前记 pending、达到后触发刷新的既有逻辑照常运行，在文件落库前后按现有协议登记。但本期不为“刷新执行失败”新增恢复状态机；目录摘要刷新失败记录后跳过该目录节点，由第 8.2 节的显式 reindex 修复。不能因为文件向量已更新，就漏掉当次应触发的父目录摘要刷新。

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
| 文件或目录语义请求失败 | 记录失败并跳过当前 DAG 节点，其他可执行节点继续；允许摘要、sidecar 和目录传播不完整，不新增 pending，由调用方显式 reindex 修复 |
| embedding/upsert/delete/update-fields 失败 | 沿用队列节点容错并允许与正式文件不一致；不新增 pending 或回滚，由调用方显式 reindex 修复后再依赖增量比较 |
| `SemanticPlan` 消息入队失败 | 清理解析 artifact、释放目标锁并使 add_resources 任务失败；不能把“正式树已提交但无后续任务”报告为成功 |
| 清理失败 | 保留可定位日志和清理债务，不伪造正文回滚 |

HTTP 异步接入成功只表示任务被接受。`wait=true` 或最终任务状态必须反映解析、正式树提交和计划入队失败；消息入队后的语义/向量节点按上述容错边界可能只记录节点错误并继续。错误消息包含阶段和路径，不能记录文件全文、API key 或签名下载 URL。

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

健康起点或已完成显式修复的资源，在所有异步节点成功时文件清单和 bytes 等于预期，索引 URI 集合符合当前 processing_mode，MD5 对应实际文件。解析、正式树提交或计划入队失败不能被报告为成功；语义/向量节点失败允许被跳过，因此任务接入成功不等于索引完整。历史失败未经修复的资源不适用增量正确性保证，后续 success 不能证明历史不一致已消除。

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

### 14.7 shared-only 严格基线对比

为回答“只启用 shared、没有 P2-P5 时的耗时”，建立独立 worktree 固定在 `6e04f20ed`（仅 P1 shared SOURCE 引用）。该提交原有 shared worker 以普通 USER 身份读取 `viking://upload/...`，会稳定触发 `PermissionDeniedError`，因此基线只带同租户内部 ROOT 读取这一项正确性补丁；未引入 local parse output、MD5 diff、显式文件快照或向量 abstract 复用。

两组使用同一 benchmark runner、同一冻结 fixture、同一 shared HTTP 入口、S3 正式存储、本地向量库、模型与并发配置。`environment.json.manifest` 按路径排序后字节级相同：40 文件、420,774 bytes、11,534 行，每个文件 SHA-256 一致。两组每轮都在 shared 上传后等待 5 秒，端到端时间均包含该等待。

| 场景 | shared-only 基线 | P1-P5 优化 | 节省 | 降幅 | 加速比 |
|---|---:|---:|---:|---:|---:|
| initial | 100.85s | 54.90s | 45.95s | 45.6% | 1.84x |
| no-op | 77.07s | 11.05s | 66.02s | 85.7% | 6.97x |
| edit-one | 75.02s | 38.06s | 36.97s | 49.3% | 1.97x |

扣除两组共同的 5 秒 benchmark 等待后：initial 为 95.85s → 49.90s（47.9%），no-op 为 72.07s → 6.05s（91.6%），edit-one 为 70.02s → 33.06s（52.8%）。

#### 阶段耗时对照

下表使用 profiler 的 `wall_union_s`。阶段可能并行或嵌套，不能把各项直接相加还原端到端时间。

| 场景/阶段 | shared-only 基线 | P1-P5 优化 | 说明 |
|---|---:|---:|---|
| initial / shared 上传 | 1.38s | 0.15s | 单次网络波动较大，不作为核心算法收益 |
| initial / shared 物化 | 0.14s | 0.52s | 两组都只下载一次 shared ZIP |
| initial / 解析产物写入 | 11.60s | 0.21s | AGFS temp 全量写入改为 local artifact |
| initial / 正式落库 | 10.38s | 2.76s | 基线 persist temp tree；优化组 local→正式 S3 |
| initial / 语义 DAG | 66.98s | 42.93s | 首次导入仍需全量摘要/向量 |
| no-op / shared 上传 | 0.13s | 0.15s | 基本一致 |
| no-op / shared 物化 | 0.13s | 0.14s | 基本一致 |
| no-op / 解析产物写入 | 11.97s | 0.28s | 避免 AGFS temp 全量上传 |
| no-op / tree diff/apply | sync 9.86s | local diff 0.04s | MD5 内存比较，正式内容零上传 |
| no-op / 语义 DAG | 41.92s | 3.06s | 向量 abstract 复用；文件摘要/embedding 均为 0 |
| no-op / 清理 | 2.21s | local cleanup 未单独计时 | 优化组本地产物终态清理后目录为空 |
| edit-one / 解析产物写入 | 10.91s | 0.26s | local artifact |
| edit-one / tree diff/apply | sync 14.86s | local diff 0.13s | 只上传一个变化文件 |
| edit-one / 语义 DAG | 33.92s | 27.07s | 文件摘要 15→1，embedding 27→9；仍有目录刷新 |

工作量对照：

| 场景 | 指标 | shared-only 基线 | P1-P5 优化 |
|---|---|---:|---:|
| no-op | 文件摘要生成 | 20 | 0 |
| no-op | overview 生成 | 7 | 0 |
| no-op | embedding | 34 | 0 |
| edit-one | 文件摘要生成 | 15 | 1 |
| edit-one | overview 生成 | 6 | 4 |
| edit-one | embedding | 27 | 9 |

所有六轮均为 `status=success`，40/40 正式文件、40/40 L2、全文逐文件校验通过，无缺失、无多余项、无正文不一致。每个场景目前各 1 个有效样本，结果用于严格同条件功能/工作量 A/B，不宣称 P95 或统计显著性。

基线证据：`/Users/bytedance/github_openviking/OpenViking/.worktrees/add-resource-shared-baseline/.scratch/ingest-profile/shared-only-baseline40-20260913-012946`。优化组证据：`.scratch/ingest-profile/shared-local-strict-ab40-20260913-013449`。

### 14.8 完整 OpenViking Python 源码规模严格 A/B

进一步扩大到 OpenViking 开源仓库中 Git 跟踪的全部 `openviking/**/*.py`：642 文件、7,061,967 bytes、188,387 行。冻结 fixture manifest SHA-256 为 `f43b08d9d8173879fb9d57c80e62cc63db95bbecf6f081e71528674875b6f9e6`；基线与优化组的 `environment.json.manifest` 逐项比较完全一致。

两组仍使用 shared HTTP、S3 正式存储、本地向量库及相同模型/并发。基线固定在 `6e04f20ed` 并仅补 shared 上传区内部 ROOT 读取权限，不带 P2-P5。两组上传后都有相同 5 秒 benchmark 等待。

| 场景 | shared-only 基线 | P1-P5 优化 | 节省 | 降幅 | 加速比 |
|---|---:|---:|---:|---:|---:|
| initial | 397.71s | 143.12s | 254.59s | 64.0% | 2.78x |
| no-op | 439.58s | 15.85s | 423.72s | 96.4% | 27.73x |
| edit-one | 429.06s | 44.87s | 384.19s | 89.5% | 9.56x |

扣除共同的 5 秒 benchmark 等待后：initial 为 392.71s → 138.12s（64.8%，2.84x），no-op 为 434.58s → 10.85s（97.5%，40.04x），edit-one 为 424.06s → 39.87s（90.6%，10.64x）。

#### 完整仓库阶段耗时

| 场景/阶段 | shared-only 基线 | P1-P5 优化 |
|---|---:|---:|
| initial / shared 上传 | 0.55s | 0.63s |
| initial / shared 物化 | 0.23s | 0.56s |
| initial / 解析及产物写入 | 148.78s | 1.00s |
| initial / 正式落库 | persist 151.99s | local persist 41.80s |
| initial / 语义 DAG | 83.15s | 90.55s |
| no-op / shared 上传 | 0.61s | 0.54s |
| no-op / shared 物化 | 0.28s | 0.35s |
| no-op / 解析及产物写入 | 149.89s | 0.86s |
| no-op / diff/apply | sync 191.60s | local diff 0.27s |
| no-op / 语义 DAG | 79.93s | 4.09s |
| no-op / 清理 | 4.34s | local cleanup 未单独计时 |
| edit-one / 解析及产物写入 | 147.42s | 1.20s |
| edit-one / diff/apply | sync 195.35s | local diff 0.20s |
| edit-one / 语义 DAG | 66.73s | 32.04s |
| edit-one / 清理 | 4.44s | local cleanup 未单独计时 |

阶段时间使用 `wall_union_s`，阶段间存在嵌套/并行，不能直接求和还原端到端。initial 的语义 DAG 波动由真实模型时延主导；两组工作量完全相同（642 文件摘要、77 overview、132 LLM、796 embedding），因此不把 83.15s 与 90.55s 的差异解释为算法回退。

#### 完整仓库增量工作量

| 场景 | 指标 | shared-only 基线 | P1-P5 优化 |
|---|---|---:|---:|
| no-op | 文件摘要生成 | 324 | 0 |
| no-op | overview 生成 | 53 | 0 |
| no-op | LLM | 67 | 0 |
| no-op | embedding / upsert | 430 / 430 | 0 / 0 |
| edit-one | 文件摘要生成 | 305 | 1 |
| edit-one | overview 生成 | 52 | 3 |
| edit-one | LLM | 64 | 3 |
| edit-one | embedding / upsert | 409 / 409 | 7 / 7 |

六轮全部 `status=success`；642/642 正式文件、642/642 L2、逐文件正文校验通过，无缺失、无多余项、无队列错误。优化组 `parse-out` 终态为空。

#### 新旧方案数据等价性检查

正确性校验分为两层。每个 initial、no-op、edit-one 场景结束时，benchmark 都独立校验正式文件集合、逐文件正文和 L2 索引集合；六轮结果均为 642/642，且 missing、unexpected、content mismatch、vector missing 和 queue error 全部为 0。跨方案的完整字段级离线 diff 使用两组 edit-one 终态快照；initial 与 no-op 没有额外保存全量向量/sidecar 快照，因此不能宣称这两个中间时点的生成文本逐字段相等。优化组 no-op 的操作计数为 0，可以确认该轮没有重新生成摘要、embedding 或 upsert。

| 检查项 | 结果 | 判定 |
|---|---|---|
| 正式业务文件 | 两组都是 642 个，URI 集合和最终正文逐文件一致 | 符合预期 |
| 向量记录结构 | 两组都是 794 条：L0=76、L1=76、L2=642；`(uri, level)`、稳定 ID、类型、ACL、tags、维度等结构字段无差异 | 符合预期 |
| MD5 | 基线 642 条 L2 均无 MD5；优化组 642 条 L2 均有 MD5，且与各自 edit-one 最终正式文件逐项一致 | 符合新增字段设计 |
| 修改文件 | `connector/client.py` 两组稳定 ID 和 L2 abstract 相同；优化组额外写入正确 MD5 `25844a23d9544029ae171a52ffef56d3` | 符合预期 |
| L2 abstract | 587/642 精确相同；55 条不同全部是代码解析器无法产出稳定 skeleton、转入 LLM fallback 的文件；确定性 AST 摘要差异为 0 | 符合模型非确定性预期 |
| L2 dense vector | 149/642 字节级相同；其余虽有浮点差异，但跨组 cosine 最小 0.99731、均值 0.99944，低于 0.99 的为 0 | 符合 embedding 非确定性预期 |
| sparse vector | 794/794 精确相同 | 符合预期 |
| 目录及 sidecar 结构 | 两组都是 76 个目录、152 个 sidecar、869 个总树条目；URI 集合一致，全部 sidecar 可解析，`directory/source/generated_by/freshness` 元数据无差异 | 符合预期 |
| L0/L1 正文与 dense vector | 75/76 个 abstract 和 76/76 个 overview 正文不同；L0 cosine 均值 0.87664，L1 为 0.95813 | 两次独立 LLM 生成的预期差异，不属于文件树或索引结构回归 |

目录正文还暴露出一个既有质量问题：虽然两组 `freshness` 都声明 717 个直属条目全部采样、没有 unsampled，实际 Markdown 链接未覆盖所有直属条目；基线有 18 个目录共缺 69 个链接，优化组有 20 个目录共缺 77 个链接，且 24 个目录的链接集合不同。两组都没有错误或越界链接，目录/文件真实清单也完全一致，因此该现象来自生成式输出及后续长度截断没有保证“全量列出”，不是 local diff 漏同步；但目录 overview 不能据此宣称字节或条目覆盖严格等价。后续若把“所有直属条目必须在 overview 出现”作为产品契约，应由确定性后处理补链接或校验后重试，不能只依赖模型输出。

因此，本轮结论是：持久化文件、索引拓扑和可确定计算字段等价，新增 MD5 正确；生成式摘要和 dense embedding 只满足结构与语义合理性，不满足字节级等价。这个结论足以排除 P1-P5 导致漏文件、漏索引、错误删除或 MD5 错配，但不等价于证明 LLM 输出文本完全一致。

#### 单文件摘要不变时停止目录传播

在已有 diff 快照携带旧 L2 abstract 的基础上，同路径 `modified` 文件生成新摘要后执行非空精确比较。若新旧 abstract 完全相同，文件本身仍按原流程更新 L2 向量和 MD5，但将该文件对子目录语义标记为未变化；直接父目录复用现有 sidecar，祖先目录也不再刷新。新增、删除、结构替换、旧摘要缺失、空摘要、新旧摘要不同以及目录存在 freshness 欠账时，仍按原流程传播。该规则不限定文件类型，也不做 embedding 相似度判断。

在完整 642 文件 fixture 上重新运行 shared+local initial→edit-one，修改 `connector/client.py` 并保持其确定性 skeleton 不变。edit-one 结果为 642/642 正式文件、642/642 L2、全文一致、全部 L2 MD5 与最终正文一致、队列错误为 0。与应用该剪枝前的同规模 edit-one 样本相比：

| 指标 | 剪枝前 | 剪枝后 |
|---|---:|---:|
| 端到端（含 5s 等待） | 44.87s | 23.22s |
| 语义 DAG | 32.04s | 5.38s |
| 文件摘要 | 1 | 1 |
| overview / LLM | 3 / 3 | 0 / 0 |
| embedding / upsert | 7 / 7 | 1 / 1 |

本次端到端降低 48.3%，语义 DAG 降低 83.2%。两轮远程服务时延并非严格受控，因此耗时只作为单样本量级；调用数收敛是确定性的功能证据。保留的 1 次 embedding 用于更新修改文件的 L2 记录和 MD5，尚未引入“复用旧向量、仅更新标量”的额外写入协议。证据目录：`.scratch/ingest-profile/shared-local-abstract-stop-full-20260913-1026`。

证据目录：

- 基线：`/Users/bytedance/github_openviking/OpenViking/.worktrees/add-resource-shared-baseline/.scratch/ingest-profile/shared-only-openviking-full-20260913-015003`
- 优化：`.scratch/ingest-profile/shared-local-openviking-full-20260913-021245`
- 冻结 fixture：`.scratch/fixtures/openviking-python-full`
- fixture manifest：`.scratch/fixtures/openviking-python-full.sha256`
- 向量字段 diff：`.scratch/ingest-profile/openviking-full-vector-diff.json`
- L2 摘要分类：`.scratch/ingest-profile/openviking-full-l2-abstract-classification.json`
- sidecar 结构/覆盖 diff：`.scratch/ingest-profile/openviking-full-sidecar-semantic-diff.json`

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

**向量快照 V：** 第一次用完整分页过滤查询（非 top-k 相似检索）获取目标范围内全部 L0/L1/L2，只投影 `id/uri/level/md5`；分页失败或不完整 → 快照失败，不当空集合。diff 和语义闭包裁剪完成后，第二次按必要 record IDs 获取 `abstract` 等非向量标量。复用已有分页工具但收窄到本资源范围，不扩大父目录搜 chunk，避免误删相邻资源。

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

## 18. 语义规划前移与最小增量 DAG

本章记录 P1-P5 之后已经实施的设计，**本轮范围只覆盖目录型 `add_resources` 的 `semantic_and_vectors` 路径**。该路径在入 SemanticQueue 前完成 local/AGFS 文件树提交和业务规划，向队列传递明确、后端无关的语义计划，并让增量计划只构造受影响路径的最小 DAG。单文件 add_resources、`vectors_only`、不建索引路径，以及 resource/skill `write`、`batch-write`、`reindex`、memory 和通用手工 `summarize` 本轮保持现有接口和行为；新接口为它们保留未来接入空间，但不把迁移这些入口列为本轮交付条件。

### 18.1 当前问题与设计边界

`SemanticQueue` 本身主要负责消息存取和 coalesce，真正影响 `add_resources` 解耦的逻辑集中在 `SemanticProcessor.on_dequeue`：AGFS 临时树同步、根据 `uri/target_uri/changes` 推断增量模式、恢复 parse artifact，以及拼装 DAG 参数。队列反序列化、stale、熔断重试、身份和锁恢复、ACK、request tracking、父目录 freshness 等执行时职责本轮保留。

| 入口 | 当前文件状态 | 当前语义调用方式 | 主要问题 |
|---|---|---|---|
| `add_resources` local 产物 | 入队前已完成 diff 和正式树更新 | 携带 `changes/file_md5s/file_abstracts/artifact_ref` 入队 | 参数分散，仍可能递归遍历无变化子树 |
| `add_resources` AGFS 产物 | 入队时可能仍是临时树 | consumer 内 `_sync_topdown_recursive` 后再跑 DAG | 文件写入业务和语义队列耦合 |
| 其他入口 | 各自保持现状 | 继续构造旧 `SemanticMsg` 或直接调用现有服务 | 明确不在本轮迁移 |

目标职责边界：

```text
add_resources / ResourceProcessor
  - 完成文件写入、diff、删除、图片 URI 规范化和正式树提交
  - 准备变化条目、最终 MD5、旧 L2 abstract
  - 构造 SemanticPlan

现有 Summarizer / SemanticQueue enqueue
  - 将 SemanticPlan 放入现有 SemanticMsg
  - 处理 request tracking 和锁 handoff

SemanticQueue / worker adapter
  - 序列化、持久化、stale、熔断、重试、ACK、telemetry 和锁生命周期
  - 新 add_resources 消息不再触发临时树同步或增量模式推断
  - 旧消息继续走现有兼容分支

SemanticDagExecutor
  - 将 add_resources plan 中带状态的 tree snapshot 编译为 DAG 并执行
  - 生成/复用文件摘要，聚合目录 L0/L1，按 outputs 投递 embedding
```

本轮不为抽象层次而新建空壳服务。优先在现有 `SemanticProcessor` 与 `SemanticDagExecutor` 边界引入一个清晰 plan；只有当后续第二个入口实际接入时，再根据重复代码决定是否抽出独立 `SemanticExecutionService`。队列 worker 仍负责真正执行时才能决定的事情：消息是否 stale、熔断和重试、锁的接管与最终释放、ACK，以及执行结果驱动的父目录 freshness。

### 18.2 add_resources 语义参数模型

不使用 `MEMORY_SCHEMA` 与 `HIERARCHICAL` 作为同一个 `strategy` 枚举。前者描述业务和生成方式，后者描述遍历结构，维度不一致。本轮 plan 只表达 add_resources 所需的“已提交正式树、处理哪些对象、产生哪些结果、如何取文件向量文本、是否向父级传播”。每个 tree entry 自带状态，不再用额外 selection 重复记录相同路径。

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


@dataclass(frozen=True)
class IndexedRecordSnapshot:
    record_id: str
    level: int
    abstract: str | None = None
    md5: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    active_count: int | None = None
    name: str | None = None
    description: str | None = None
    tags: str | None = None
    search_tags: tuple[str, ...] | None = None


@dataclass(frozen=True)
class SemanticTreeEntry:
    relative_path: str
    kind: Literal["file", "directory"]
    state: Literal["unchanged", "added", "modified", "deleted"]
    md5: str | None = None
    indexed_records: tuple[IndexedRecordSnapshot, ...] = ()


@dataclass(frozen=True)
class SemanticTreeSnapshot:
    entries: tuple[SemanticTreeEntry, ...]


@dataclass(frozen=True)
class VectorRecordRef:
    record_id: str
    uri: str
    level: int


@dataclass(frozen=True)
class SemanticOutputs:
    vectorize: bool = True


class FileVectorSource(str, Enum):
    CONTENT = "content"
    SUMMARY_WHEN_AVAILABLE = "summary_when_available"


@dataclass(frozen=True)
class ParentPropagation:
    enabled: bool = True
    use_freshness: bool = True


@dataclass(frozen=True)
class SemanticPlan:
    root_uri: str
    context_type: str
    tree: SemanticTreeSnapshot
    orphan_vector_deletes: tuple[VectorRecordRef, ...]
    outputs: SemanticOutputs
    propagation: ParentPropagation
    file_vector_source: FileVectorSource = FileVectorSource.CONTENT
    ingest_options: IngestOptions = field(default_factory=IngestOptions)
    source_metadata: dict[str, str] | None = None
```

语义约束：

- 完整 `ResourceTreeManifest` 只在 ResourceProcessor 内部用于 diff、正式树提交和语义裁剪，不直接进入队列。`SemanticPlan.tree` 是带状态的裁剪语义快照：首次导入的当前节点均为 `added`；增量只包含变化节点、可能执行聚合的目录，以及这些目录的全部直接子项。路径均相对 `root_uri`，不包含 `.abstract.md`、`.overview.md` 及控制文件。
- tree snapshot 不重复保存 `direct_children`。Semantic worker 按 `relative_path + kind` 一次性构造 `parent -> direct files/direct directories` 邻接表；本轮裁剪必须保证每个候选聚合目录的全部当前直接子项均作为 entry 保留。裁剪阶段不做 sampling，也不复制 freshness/overview 的采样策略；Semantic DAG 在目录真正被激活时继续调用现有唯一的 `deterministic_sample()`。
- `indexed_records` 是构造 diff 时已查询到的轻量旧索引快照。文件条目通常携带 L2，目录条目按目录聚合需要携带 L0；除 `record_id/level/abstract/md5` 外，只携带 full upsert 必须保留的非向量业务标量，如 `created_at/active_count/name/description/tags/search_tags`，不携带 dense `vector` 或 `sparse_vector`。`uri/context_type/account_id/owner_user_id` 等身份字段由 `root_uri`、entry、当前请求上下文重新推导并校验；ACL 延续现有 materialization 逻辑，不信任 plan 中可伪造的权限字段。
- entry 的 `state` 是 Semantic 的唯一节点状态来源，不再维护一份重复的 `ChangeSelection`。`added/modified` 文件需要生成摘要并维护 L2；`added/deleted` 节点表示目录成员或类型变化；`unchanged` 节点只作为候选目录的直接聚合输入。执行器根据这些状态构造 DAG，不得再扫描整棵子树寻找变化。
- deleted entry 保留第一阶段 inventory 中属于该节点的旧 `indexed_records`，Semantic 据 `state=deleted` 直接生成精确 DELETE；不在 plan 顶层重复记录。DiffPlan 的 `repair` 归一化为 `state=modified` 且没有旧 L2；Semantic 据此完整重建 L2，并因旧摘要缺失保守决定目录聚合。`structural` 归一化为当前新类型的 `state=added`，同路径旧类型记录保留在该 entry 的 `indexed_records`，Semantic 删除与新 kind 不匹配的旧 level。只有纯 `orphan_vectors` 没有对应树节点，因此单独进入 `orphan_vector_deletes`。`needs_body_compare` 必须在 plan 生成前归并为 `unchanged/modified`。
- add_resources 只在需要生成语义 sidecar 时创建 plan；`outputs.vectorize=False` 表示生成文件/目录语义但不投递 L0/L1/L2 embedding，对应 `summarize=True, build_index=False`。`vectors_only` 不创建 plan。
- 语义 repair 与 `outputs.vectorize` 解耦：`summarize=True, build_index=False` 时，缺 L2 摘要的文件及缺 L0/L1 的目录仍标记为 `modified` 并按需进入 DAG，只禁止向 EmbeddingQueue 投递。若目标范围完全没有语义索引，则保守重建当前完整目录语义，不能把无索引误判为健康 no-op。
- 已存在记录但聚合必需的 L2/L0 `abstract` 为空时，也不能当作可复用依赖；plan builder 将该文件或子目录定向提升为 `modified`。目录 repair 会补入其全部直接子项用于原采样和聚合逻辑，不递归扫描无关子树。
- entry 相对路径必须规范化且不得越过 root，同一路径只能出现一次。`added/modified` 必须存在于当前正式树；`deleted` 是唯一允许不存在于当前树的 tombstone，保留旧 kind 和待删 `indexed_records`，但不得读取正文。构造当前树邻接表时排除 deleted tombstone；它只作为父目录结构变化和索引删除信号。
- DAG 拓扑编译规则是纯内存操作：根目录用空相对路径 `""` 表示；entry 的父路径由规范化相对路径计算，不允许用“最近存在祖先”代替真实直接父目录。首次导入的裁剪树包含完整节点，可还原原全量 DAG；增量裁剪树只还原受影响路径的最小 DAG，这是预期差异。
- entry 的 `md5` 必须对应正式树最终 bytes；`indexed_records[].md5/abstract` 是 plan 生成时读到的旧索引状态。修改文件通过“本次生成摘要与旧 L2 abstract”比较决定是否停止目录传播，旧摘要不能掩盖文件内容变化。DAG 运行中的新摘要保存在运行时结果缓存，不回写 frozen plan。

不再维护独立的 `file_md5s` 或 `previous_abstracts` 映射；同一路径的拓扑、最终 MD5 和旧索引快照放在一个 entry 中，避免多张表按 URI 对齐。当前 `get_l2_diff_records_under_uri()` 只返回 L2，不能满足新计划；需新增或扩展为目标前缀下全层级 inventory。完整 inventory 查询结束后再裁剪，不能为了少查数据而破坏孤儿检测、目录记录定位和 diff 完整性。

向量库读取拆成两个阶段，避免第一次目录级全量扫描返回大量最终不会使用的摘要和业务标量：

1. **Diff inventory 查询：** 在目标目录锁内按目录前缀完整分页查询 L0/L1/L2 全部记录，只投影 `id/uri/level/md5`。`uri` 是 N/F/V 按路径对齐和校验查询范围的必要字段；`level` 用于区分同 URI 的 L0/L1/L2，不能只返回 `id/md5`。该结果用于文件 MD5 比较、各层索引存在性、孤儿记录和删除记录识别，并为第二次 hydration 提供精确 ID；此时不提供目录聚合正文。
2. **Plan hydration 查询：** diff 完成并计算语义依赖闭包后，收集裁剪快照实际需要的 record IDs。优先分批使用严格 DSL `In("id", batch_ids)` 查询并显式投影非向量字段；只对 DSL 正常返回后仍缺失的 IDs 调用严格主键 `fetch/get` 回填。最终统一投影并填入 `SemanticTreeEntry.indexed_records`，不查询不在闭包中的记录，且不把 `vector/sparse_vector` 写入 plan。

第二阶段 ID 集合直接从第一次 inventory 中选择，包括：modified 文件旧 L2、候选目录全部 unchanged 直接文件的 L2、全部 unchanged 直接子目录的 L0，以及可能重建目录自身的旧 L0/L1。父目录聚合依赖子目录 L0 abstract，不依赖其 L1；L1 仍需 hydration，因为受影响目录重新生成 overview 时要保留必要标量并覆盖 L1。added 文件和 repair 缺失记录没有旧 ID；仅用于删除的记录已有第一阶段 `id/uri/level`，无需查询其他字段。若预期的目录 L0/L1 未出现在第一次 inventory，则直接按“旧目录索引不存在”处理，不凭稳定 ID 猜测记录存在。

第二阶段不是无约束的“整条记录 fetch”。DSL 使用显式非向量投影：`id/uri/level/abstract/md5/created_at/active_count/name/description/tags/search_tags` 等当前完整 upsert 必须保留的字段；旧 `content` 对 modified 文件已经过期，目录内容也由新 sidecar 生成，原则上不读取。身份与 ACL 字段继续由当前 URI/ctx 推导和 materialize，不直接信任队列快照。

DSL 按 ID 分批时，每批不得超过后端数据面限制（当前 VikingDB 类后端按 100 控制），查询 limit 至少覆盖该批 ID 数；使用会传播异常的 strict query，不能调用把异常转换为空列表的普通 `query/filter`。DSL 请求本身失败属于查询失败，默认终止 plan 构造，不能伪装成“全部 ID 未命中”；只有 DSL 成功后，`requested_ids - returned_ids` 才进入主键 fetch fallback。若某后端明确不支持 ID DSL，应通过 adapter capability 直接选择 fetch，而不是先制造一次必然失败的请求。

主键 fallback 可能返回完整记录并包含 dense/sparse vector，但只允许对缺失 ID 执行；结果进入 plan 前必须立即按白名单投影并丢弃 `vector/sparse_vector/content` 等不需要字段。两条路径合并后必须校验：返回 ID 属于请求集合、无重复、URI/level 与第一次 inventory 一致、记录属于当前 tenant。DSL 和 fetch 均未找到的记录再按用途分类：待删除记录视为幂等完成；modified 文件 L2 降级为 repair；目录聚合所需 unchanged 子项摘要缺失则定向补摘要或明确失败，不能用空摘要静默继续。

两阶段查询都发生在目标资源锁持有期间，并在 SemanticPlan 成功持久化/锁 handoff 前完成。它们减少的是重复读取和队列载荷，不提供 generation fencing；历史异步索引任务仍可能在两次查询之间改写记录，继续适用第 16.3 节的已知风险。

`SemanticPlan` 不包含临时树、解析后端和调用来源判断所需字段。以下内容不得进入新计划：

```text
temp_uri / source temp tree
target_uri（计划中只有已提交的 root_uri）
artifact_ref / artifact_files
target_preexisting
通过 generation_trigger 选择执行算法
```

为控制本轮改动面，不新增一套与 `SemanticMsg` 并行的队列 envelope。先给现有消息增加可选的版本化 plan：

```python
@dataclass(frozen=True)
class SemanticMsg:
    # Existing queue fields remain unchanged.
    plan_version: int | None = None
    plan: SemanticPlan | None = None
```

只有 add_resources 新生产者设置 `plan_version=1` 和 `plan`。没有 plan 的旧消息继续走现有路径，确保 write、batch-write、reindex、父目录刷新、copy/delete 和历史队列消息不受影响。旧 consumer 不理解 manifest/minimal-DAG 语义，因此本轮不承诺新 add_resources producer 与旧 consumer 混跑；部署时先升级 consumer，再启用新 producer，并在移除旧字段前排空历史消息。兼容期可按需镜像旧字段用于回滚和诊断，但不能把旧 consumer 的退化执行当作正确性或性能保证。后续其他入口迁移完成后，再决定是否引入独立 `SemanticJob`。

### 18.3 带状态 tree snapshot 必须生成受影响路径最小 DAG

带状态 tree snapshot 的关键验收条件不是“最终少调用模型”，而是“无变化子树不进入 DAG”。当前实现的 `recursive=True + changes` 仍会递归调度所有子目录，这是本阶段必须修掉的性能边界。

示例：

```text
repo/
├── src/
│   ├── a.py          # modified
│   ├── b.py          # unchanged
│   └── utils/        # unchanged subtree
├── docs/             # unchanged subtree
└── tests/            # unchanged subtree
```

目标 DAG：

```text
FileSummary(src/a.py)
        ├── FileVector(src/a.py)
        └── if file abstract changed
                ↓
          DirectoryAggregate(src)
                └── SemanticExecutionResult.abstract_changed
                        ↓
                 ParentFreshness(repo)
```

不得创建或进入：

```text
docs 的 dir/file DAG 节点
tests 的 dir/file DAG 节点
utils 内部的任何节点
b.py 的正文读取、LLM 或 embedding 节点
```

重新聚合 `src` 时仍需获得其直接子项语义，但只能读取直接层信息：

```text
a.py   → 使用本次 FileSummary 的新摘要
b.py   → 使用 plan entry 中的旧 L2 abstract
utils  → 使用 plan entry 中的旧 L0 abstract
```

当前目录的全部直接子项和所需旧摘要由 tree snapshot 的邻接表与各 child entry 的 `indexed_records` 提供；正常聚合不再执行 `ls(src)`，也不再查询向量库，更不允许递归进入 `utils`。目录真正执行时，DAG 在邻接表中的全部直接子项上调用现有 `deterministic_sample()`，只把采样结果交给 overview 模型；这不会要求 plan 包含未变化子目录的后代。若某个未变化直接子项缺少所需摘要，只为该直接子项动态补任务：文件补一个 FileSummary，目录可显式执行该目录修复或使当前任务失败并要求 reindex；不能无条件退化为扫描整个 root。只有 `UPDATE_FIELDS` 为保留未携带的 dense/sparse vector 时允许按稳定 ID 二次查询完整旧记录。

最小 DAG 编译规则：

1. `state=added/modified` 的文件创建文件摘要节点；`state=deleted` 的文件不读取正文。
2. 从每个非 unchanged entry 推导其直接父目录，构造去重后的受影响目录集合。
3. added/deleted 目录及文件/目录类型替换后的 added entry 标记直接父目录结构变化；已删除目录内部不再建节点。
4. 变化文件摘要与旧摘要相同时，文件 L2/MD5 仍更新，但不执行直接父目录聚合。
5. 文件摘要变化或目录成员发生增删时，才执行直接父目录聚合。
6. 目录聚合只使用从 plan entry 路径构建的全部直接子项及其旧摘要，在执行时复用现有唯一采样函数；不再查询正常聚合输入，也不递归进入未变化子目录。
7. 当前目录 L0 未变化则停止向上；变化后沿用现有 `_enqueue_parent_refresh` 运行 freshness 策略。
8. 父目录达到 freshness 阈值时，本轮仍由现有 helper 产生 `recursive=False` 的旧格式父目录消息；它只聚合父目录，不回扫完成的子树。

若裁剪后所有 entry 均为 `unchanged` 且没有 `orphan_vector_deletes`，表示调用方确认无变化；在不存在 freshness 欠账或修复工作的前提下，业务层不得入 SemanticQueue。首次导入则把完整当前树 entry 标记为 `added`，不需要额外的全量 selection。

### 18.4 文件树同步统一前移

local 模式当前已在 `ResourceProcessor` 中完成 diff 和正式树更新；AGFS 模式仍把 temp→target sync 放在 Semantic consumer。下一阶段将二者统一为：

```text
ParseArtifactRef (local/agfs)
        ↓
ResourceTreeCommitService
  - 完整性门禁
  - 规范化最终 bytes / 图片 URI
  - 构造完整新树 manifest
  - 读取目标 F/V 快照
  - 构造并应用 DiffPlan
  - 更新正式文件树、删除旧文件、处理文件/目录类型冲突
  - 不直接删除或更新向量；将确定的向量操作写入 SemanticPlan
  - 产出完整 ResourceTreeManifest + AppliedResourceChangeSet
        ↓
add_resources SemanticPlan builder
  - 裁剪语义树快照
  - 将文件变化及其旧记录写入 entry，将纯孤儿删除写入 plan.orphan_vector_deletes
        ↓
现有 Summarizer / SemanticQueue enqueue
```

完整 `ResourceTreeManifest` 来自已通过完整性门禁、规范化并成功提交的解析产物，不依赖正式 S3/AGFS 的写后递归列举；`AppliedResourceChangeSet` 只能包含实际成功提交的文件变化，并携带新增/修改文件最终 MD5。优先复用现有 `DiffPlan`、`ApplyResult`、parse output store 与 resource target，不为本轮再复制一套 diff 实现；仅在确有复用价值时抽取薄的正式树提交协调函数。

本阶段覆盖现有 `apply_diff_plan()` 的向量副作用：ResourceTreeCommitService 只执行正式文件系统操作。DiffPlan 中 deleted 节点和 structural 旧层级的记录保存在对应 entry 的 `indexed_records`；repair、added、modified 归一化为 entry 状态，其中 repair 使用 `modified + 旧 L2 缺失` 表达。纯 orphan 没有树节点，转换成精确 `VectorRecordRef` 放入 `SemanticPlan.orphan_vector_deletes`。这些向量删除、更新和新增均在下游通过 EmbeddingQueue 执行。Semantic worker 对新 add_resources plan 只点读正式 `root_uri` 下计划要求的文件 bytes，不再调用 `_sync_topdown_recursive`，也不负责图片映射搬运、temp tree 删除、local artifact store 恢复或通过远程 `tree/ls` 发现整棵拓扑。旧消息路径暂时保留原行为。

同步前移后存在已接受的失败窗口：正式树提交成功、SemanticMsg 入队前进程崩溃，会留下文件与语义/向量不一致。按第 8.2 节既定取舍，不新增 `index_pending` 或自动补偿；原操作明确失败，调用方通过 `reindex semantic_and_vectors` 修复。锁仍需从正式树提交阶段 handoff 给队列 worker，不能在任务入队后立即释放，否则语义任务可能读取到下一代文件内容。local/AGFS artifact 在正式树提交并成功入队后由 ResourceProcessor 清理；入队失败则由同一上层失败路径清理。

### 18.5 本轮 add_resources 映射与其他入口边界

| 入口/模式 | tree 状态 | outputs | 执行方式 |
|---|---|---|---|
| `add_resources` 首次目录、`semantic_and_vectors` | 完整裁剪树的当前 entry 均为 `added` | 文件摘要、目录语义；`build_index` 决定是否生成向量 | queued |
| `add_resources` 增量目录、`semantic_and_vectors` | 变化 entry 带 `added/modified/deleted`，聚合依赖为 `unchanged` | 文件摘要、目录语义；`build_index` 决定是否生成向量 | queued |
| `add_resources` 单文件 | 本轮不使用 plan | 保持当前 `refresh_file_parent`/直接向量路径 | 现有路径 |
| `add_resources`、`vectors_only` | 本轮不使用 plan | 保持现有 DiffPlan 变化文件直接 embedding 路径 | 现有路径 |
| `add_resources`、不要求 summarize 且不 build index | 本轮不使用 plan | 保持现有纯文件提交路径 | 无语义任务 |
| resource/skill `write` | 本轮不使用 plan | 保持现有 freshness、file-only 与 tags 语义 | 现有 queued 路径 |
| resource/skill `batch-write` | 本轮不使用 plan | 保持现有分组、freshness、coalesce 与 tags 语义 | 现有 queued 路径 |
| `reindex` | 本轮不使用 plan | 保持直接构造旧 `SemanticMsg` 并 inline 调用 consumer 的现状 | 现有 inline 路径 |
| memory 相关入口 | 本轮不使用 plan | 保持 `MemoryUpdater`、旧 memory Semantic 兼容和 reindex 行为 | 现状 |

add_resources 本身不依赖 content-write 的 coalesce 降级逻辑，因此本轮不设计新的 `CoalescePolicy`，不拆分文件任务与目录聚合任务。新 plan 沿用 add_resources 当前的队列和等待语义；write/batch-write 的 stale 特判继续读取旧字段。

### 18.6 memory 明确排除在本轮改造之外

本轮不新增 `MemoryRefreshPlan`，不调整 `MemoryUpdater`，也不修改 `_process_memory_directory`、`use_hierarchical_aggregation` 或 memory reindex。原因是 memory 正常写入采用 schema/template 派生，而 add_resources 采用通用 LLM 目录 DAG；为了本轮 add_resources 解耦而统一二者，会扩大业务语义和测试矩阵。

已确认的当前事实仅作为后续设计输入：正常 memory write/batch-write/delete 不走 memory 专用 Semantic 分支；memory reindex 显式选择通用 DAG；通用 `summarize(memory_uri)`、历史持久化消息或外部直接生产旧 `SemanticMsg` 仍可进入 `_process_memory_directory`。本轮必须保证这些旧路径在 `plan=None` 时行为完全不变。

### 18.7 执行结果与父目录传播

首次入队前无法知道新 L0 是否真正变化，因此父目录 freshness 不能完全前移。DAG 执行返回：

```python
@dataclass(frozen=True)
class SemanticExecutionResult:
    root_uri: str
    abstract_body_changed: bool
    overview_body_changed: bool
    summarized_files: int
    file_vectors_enqueued: int
    directory_vectors_enqueued: int
```

worker 在成功执行后沿用现有父目录 freshness helper：

```text
abstract_body_changed=False
  → NOOP，不增加父目录 pending

abstract_body_changed=True
  → 原子更新父目录 freshness
  → MARK_PENDING：只记账
  → REFRESH_NOW：沿用现有 `recursive=False` 父目录消息
```

对于新 add_resources plan，执行结束后把 `abstract_body_changed` 交给现有 `_enqueue_parent_refresh`；plan 的 propagation policy 决定是否调用它。现有 `content_copy`、write/batch-write、reindex 和旧消息仍保留原有 `generation_trigger/propagate_to_parent` 判断；本轮不抽取全局 `SemanticPropagationService`。

### 18.8 EmbeddingQueue 的操作类型与查询取舍

队列物理名称继续使用 `Embedding`，不新增或重命名队列。`EmbeddingMsg` 增加可选 operation；旧消息缺少 operation 时默认 `EMBED_AND_UPSERT`：

```python
class EmbeddingOperation(str, Enum):
    EMBED_AND_UPSERT = "embed_and_upsert"
    UPDATE_FIELDS = "update_fields"
    DELETE = "delete"
```

| operation | 是否调用向量模型 | 是否允许再查向量库 | 行为 |
|---|---:|---:|---|
| `EMBED_AND_UPSERT` | 是 | 否 | 使用 plan 中必要非向量旧标量与本次新摘要/MD5构造完整记录，生成新 dense/sparse vector 后按稳定 ID full upsert |
| `UPDATE_FIELDS` | 否 | 是 | 按稳定 ID 读取一次完整旧记录，合并白名单标量后写回，保留原 dense/sparse vector |
| `DELETE` | 否 | 否 | 使用 plan 中精确 record ID 删除，缺失视为幂等成功 |

本轮明确不把 dense/sparse vector 放进 `SemanticPlan`。第一次快照查询只扩展到 full upsert 必须保留的非向量业务标量；为少量标量更新分支预读并跨队列传输全量向量，会放大首次查询、消息体、持久化和重试成本。命中 `UPDATE_FIELDS` 时允许 Embedding worker 再读一次完整旧记录，这是有意接受的低频额外 I/O。

典型的 `UPDATE_FIELDS` 场景是代码文件正文和 MD5 变化，但新旧摘要完全相同，且该文件的 embedding 输入使用摘要：此时不调用 embedding 模型，只更新 `md5`、需要后端保存时的 `content` 和 `updated_at`。现有 `partial_update=True` 已实现 get→merge→upsert，可作为首版执行通道；后续若各后端原生字段更新语义验证一致，再改为严格 `update_data`，不作为本轮前置。

`UPDATE_FIELDS` 使用严格白名单，禁止修改 `id/uri/level/account_id/owner_user_id/vector/sparse_vector`。`EMBED_AND_UPSERT` 不再使用会重复 `get(id)` 的 `partial_update=True`：需要保留的轻量标量必须来自 plan 或当前 URI/ctx 的确定性推导，生成新向量后执行完整 upsert。若无法构造满足 schema 和 ACL 的完整记录，不得静默丢字段，应失败并保留原索引。

计划到操作的映射：

| 状态 | Semantic 处理 | Embedding operation |
|---|---|---|
| added file | 生成摘要，标记目录结构变化 | `EMBED_AND_UPSERT` L2 |
| modified file，embedding 输入变化 | 生成摘要，按摘要变化决定目录聚合 | `EMBED_AND_UPSERT` L2 |
| modified code file，新旧摘要相同 | 不聚合目录 | `UPDATE_FIELDS` L2 |
| repair file | 生成摘要；旧摘要缺失时保守决定目录聚合 | `EMBED_AND_UPSERT` L2 |
| deleted path | 不读正文，标记父目录结构变化 | 对已枚举旧记录发 `DELETE` |
| structural replacement | 处理新 manifest 中的新类型，标记父目录结构变化 | 删除旧层级记录；新文件/目录按需 upsert |
| orphan vector | 不进入语义 DAG | `DELETE` |
| unchanged | 不进入执行集合 | 无；若仅缺 MD5且正文已确认相同，可选 `UPDATE_FIELDS` |

同一 `(account_id, uri, level)` 在一个 plan 中只能编译出一种最终操作；entry 派生删除与 `orphan_vector_deletes` 必须先按 record ID 去重，发现 `DELETE` 与 `UPDATE_FIELDS/EMBED_AND_UPSERT` 冲突时必须在入 EmbeddingQueue 前失败。由于本轮不实现 generation fencing，旧 plan 的异步 DELETE/UPDATE/UPSERT 晚于新请求到达时仍可能破坏新索引；这是第 8.2/16.3 节已记录的最终一致性风险，本轮按用户确认先接受，不能把 operation 统一误称为并发正确性修复。

### 18.9 兼容、故障与生命周期

- `SemanticMsg` 的新 plan 字段可选；无 plan 的旧消息继续由当前逻辑消费，不要求一次性迁移全部生产者。部署顺序为先升级 consumer、再启用 add_resources 新 plan；回滚前先停新接入并排空新计划消息，不能让旧 consumer 处理其不理解的 plan。
- 新 add_resources plan 只走 queued 路径；本轮不建设 queued/inline 通用执行框架。
- local/AGFS artifact 在正式树提交且 SemanticMsg 成功持久化后即可由上层清理；若入队失败，上层清理并上报失败。新 plan 不携带 artifact。
- 完整性门禁发生在 finalize/diff 前：目录解析结果中真正参加解析但含 `error` 的失败项会终止镜像更新；include/exclude、unsupported、source accessor 报告的合法跳过不算 parser failure。代码仓库批量 artifact 写入的失败列表同样必须向上传播，不能用成功文件数掩盖部分写失败。
- 文件摘要或目录摘要的 LLM 请求失败沿用现有容错：记录并跳过当前节点，依赖该节点新摘要的聚合可能使用空值或停止传播，但不把整条 SemanticMsg 改为失败。该行为与“SemanticMsg 根本没有成功入队”不同；后者必须使 add_resources 失败。
- 锁冲突重试不触发模型熔断；永久错误和输入过大终结；暂时性模型错误重新入队，沿用现有错误分类。
- 第一阶段不改变已接受的一致性模型：文件提交成功后语义或 embedding 失败可长期不一致，由显式 reindex 修复。
- 不在本阶段同时引入 generation fencing；旧异步任务覆盖新结果仍按第 16.3 节作为独立正确性工作。
- 本轮不调整外部 `queue_status` 或 `context_count` 契约；`Embedding.processed` 暂时仍统计该队列处理的全部消息，包括 embed/upsert、标量更新和删除。为诊断增加内部分类计数/日志即可，不把指标重命名或响应结构调整纳入本轮。

### 18.10 实施拆分

| 阶段 | 工作 | 行为变化 |
|---|---|---|
| S1 | 新增 `SemanticPlan`、带状态 tree/index snapshot 模型与校验；`SemanticMsg` 增加可选 plan | 无 plan 的所有旧入口行为不变 |
| S2 | 扩展现有 `EmbeddingMsg/Handler` 支持 `UPDATE_FIELDS/DELETE` | 旧消息默认 embed/upsert；新操作不调用模型 |
| S3 | local 目录 add_resources 构造新 plan，消费端将 plan 映射到现有 DAG 参数 | local 行为等价，消息不再携带 artifact |
| S4 | AGFS 目录 temp→target diff/apply 前移，复用现有 DiffPlan/ApplyResult | 新 add_resources consumer 不再同步文件树 |
| S5 | 为带状态 tree snapshot 实现受影响路径最小 DAG | 增量不再访问无变化子树 |
| S6 | 收敛 add_resources 新 plan 的 artifact 清理、锁 handoff、错误与观测 | 完成新链路生命周期 |
| S7 | 运行 local/AGFS 正确性与性能 A/B | 满足访问计数和数据一致性验收 |

每个阶段独立提交；S1/S2 先做兼容扩展，S4 文件同步前移与 S5 DAG 算法优化不得揉成同一个提交。S5 必须以访问计数证明裁剪生效，不能只用 embedding 数下降作为证据。write、batch-write、reindex、memory 的迁移另立后续方案，不作为这些阶段的前置或验收条件。

### 18.11 测试与验收

新增或调整以下测试：

- `tests/storage/test_semantic_plan.py`：entry 状态、tree snapshot、outputs、orphan_vector_deletes 的序列化和校验，以及 `SemanticMsg(plan=None)` 旧消息兼容。
- 同一测试验证新 consumer 可继续解析和执行没有 plan 的历史消息；新 plan 的回滚依赖受控排空，不要求旧 consumer 解释新 plan。
- 向量 hydration 测试：DSL 覆盖全部 ID 时不调用 fetch；DSL 正常部分命中时只 fetch 缺失 ID；DSL 异常时不按 miss 继续；fallback 返回向量字段时进入 plan 前剔除；URI/level/tenant 不匹配时失败。
- `tests/storage/test_embedding_queue_operations.py`：旧消息默认 embed/upsert；`UPDATE_FIELDS` 不调用模型且允许一次 get→merge→upsert；`DELETE` 按精确 ID 幂等删除；非法字段和同记录冲突拒绝。
- `tests/storage/test_semantic_dag_incremental.py`：变化路径最小 DAG、未变化摘要复用、摘要不变停止传播、删除/目录替换。
- `tests/storage/test_semantic_processor_target_preexisting.py`：新 add_resources plan 不执行 temp→target sync，只接收正式 root；旧消息同步行为保留。
- `tests/utils/test_local_artifact_incremental.py` 与 AGFS 对应集成测试：两种产物后端在入队前得到相同正式树和 changes。
- diff apply 边界测试：新 plan 路径只修改正式文件树，不直接调用向量 delete/update；deleted/structural 的旧记录保存在 entry，纯 orphan 进入 `orphan_vector_deletes`，三者最终都由 Embedding handler 执行。
- `tests/utils/test_resource_processor_processing_mode.py`：目录 `semantic_and_vectors` 使用 plan；单文件、`vectors_only`、不建索引继续走非 plan 分支且行为不变。
- 解析完整性和入队失败测试：部分目录解析失败在 finalize/diff 前终止；代码仓库 artifact 部分写失败不产生可提交结果；plan 入队失败清理 artifact、释放锁并向上报错；文件/目录语义请求失败仅跳过对应节点。
- 现有 write、batch-write、reindex、memory 定向回归：证明 `plan=None` 时参数、队列消息和结果不变；不新增这些入口的 plan 测试。

必须覆盖的最小 DAG 用例：

```text
repo/
├── changed/a.py
├── unchanged_a/...
└── unchanged_b/...
```

只修改 `changed/a.py` 时断言：

- Semantic worker 不为发现拓扑调用 `tree/ls`，也不访问 `unchanged_a`、`unchanged_b`；拓扑和直接聚合输入由 plan 提供。
- 不读取未变化子树正文，不为其创建 file/dir DAG 节点。
- 正常目录聚合不查询向量库，直接使用 plan 中当前聚合目录直接子项的 L2/L0 abstract；只有 `UPDATE_FIELDS` 可产生一次按 ID 的旧记录读取。
- 文件摘要未变化时只产生一个文件 L2 embedding，目录 LLM 和父目录任务均为 0。
- 文件摘要变化时只聚合直接父目录；更高祖先由 freshness 结果决定。
- 最终业务文件、L0/L1/L2 URI 集合、MD5、ACL 和 tags 与全量正确执行结果一致。

性能验收在现有 40 文件和 642 文件 frozen fixture 上增加确定性访问计数：`semantic_tree_ls_count`、`dag_file_nodes`、`dag_directory_nodes`、`semantic_vector_reads`、`embedding_update_prereads`、`file_body_reads`。新 plan 路径的 `semantic_tree_ls_count` 必须为 0；正常目录聚合的 `semantic_vector_reads` 必须为 0；`embedding_update_prereads` 只能等于实际 `UPDATE_FIELDS` 数量，且不得随无变化子树规模线性增长。模型调用、embedding 和 upsert 继续使用第 14.8 节口径对比。

### 18.12 SemanticPlan 实施后 40 文件收益验证

在第 14.7 节相同的 frozen 40 文件、shared HTTP 输入、local parse output、本地向量库和远程 S3 环境上，完成 SemanticPlan、同步前移与最小 DAG 后重新执行 initial、no-op、edit-one。每轮仍包含相同的 5 秒 benchmark 等待，且正式代码不增加 shared 重试。

| 场景 | shared-only 基线 | SemanticPlan 优化 | 降幅 | 语义工作量 |
|---|---:|---:|---:|---|
| initial | 100.85s | 78.90s | 21.8% | 40 文件摘要、8 目录摘要、56 embedding |
| no-op | 77.07s | 11.21s | 85.5% | 不入 SemanticQueue；0 摘要、0 embedding |
| edit-one | 75.02s | 12.20s | 83.7% | 1 文件摘要、1 目录摘要、1 embedding |

edit-one 的 Semantic DAG 从 shared-only 基线 33.92s 降至 1.34s，下降 96.1%；EmbeddingQueue 新消息从 27 条降至 1 条。与上一版仅 local artifact + MD5 diff 的 edit-one（38.06s、Semantic DAG 27.07s、9 条 embedding）相比，最小 DAG 又将端到端时间降至 12.20s，并把目录遍历和未变化分支的向量工作收敛掉。

三轮均通过 40/40 正式文件、40/40 正文和 40/40 L2 校验，missing、unexpected、content mismatch 均为空，队列 error_count 为 0；no-op 前后 Semantic/Embedding processed 计数不增加。证据目录：`.scratch/ingest-profile/semantic-plan40-20260913-212633`。每个场景仍只有 1 个有效样本，数据用于功能和工作量收敛验证，不宣称 P95 或统计显著性。

### 18.13 当前提交 AGFS/local 严格对照

为验证 SemanticPlan 在 AGFS 解析产物模式下的行为，并隔离 local/AGFS 这一变量，在提交 `62dcb56de` 上使用同一份 frozen 40 文件分别运行：

- 输入均为 shared HTTP ZIP，上传后固定等待 5 秒。
- 正式文件系统均为远程 S3，向量库均为本地向量库。
- 文件数 40、总大小 424,369 bytes、总行数 11,629，两个运行目录中的输入 manifest 完全一致。
- 唯一配置差异是 `storage.parse_output.mode=agfs/local`。

#### 端到端耗时

| 场景 | 优化前 shared-only | 当前 SemanticPlan + AGFS | 当前 SemanticPlan + local | AGFS 相对优化前 | local 相对 AGFS |
|---|---:|---:|---:|---:|---:|
| initial | 100.85s | 100.88s | 87.00s | 基本持平 | -13.8% |
| no-op | 77.07s | 48.82s | 10.24s | -36.7% | -79.0% |
| edit-one | 75.02s | 43.56s | 10.77s | -41.9% | -75.3% |

表中均包含每轮 shared 上传后的 5 秒测试等待。优化前 shared-only fixture 为同样 40 个路径，但旧提交下总大小为 420,774 bytes、11,534 行；因此“当前 AGFS 相对优化前”是近似同规模对照。当前 AGFS 与当前 local 的 manifest 字节级一致，是只切换 parse output backend 的严格对照。initial 包含真实 LLM 调用，受远程模型时延影响较大；no-op 与 edit-one 更适合衡量存储路径收益。

#### 当前 AGFS 分阶段耗时

以下为 `wall_union_s`，阶段之间可能嵌套，不能直接相加还原端到端时间。

| 阶段 | initial | no-op | edit-one |
|---|---:|---:|---:|
| shared 上传 | 0.61s | 0.13s | 0.19s |
| shared 物化到 worker | 0.15s | 0.54s | 0.25s |
| 解析并写 AGFS temp | 18.27s | 18.31s | 16.25s |
| TreeBuilder finalize | 0.04s | 0.06s | 0.02s |
| artifact 图片 URI 规范化 | 1.51s | 1.02s | 0.81s |
| N/F/V 快照 | — | 0.043s | 0.046s |
| diff apply / 正式树提交 | 首次全量提交 12.24s | 17.38s | 15.49s |
| SemanticPlan 构造 | 0.001s | 0.001s | 0.002s |
| Semantic DAG | 54.15s | 0s | 1.20s |
| AGFS temp 清理 | 6.01s | 2.72s | 2.35s |
| 端到端 | 100.88s | 48.82s | 43.56s |

#### 当前 AGFS/local 增量阶段对照

| 场景/阶段 | AGFS | local | 结论 |
|---|---:|---:|---|
| no-op / 解析及产物写入 | 18.31s | 0.22s | AGFS 逐文件远程写 temp；local 写本地目录 |
| no-op / 图片 URI 规范化 | 1.02s | 0.008s | AGFS 需要远程列举/读取；local 为本地操作 |
| no-op / N/F/V 快照 | 0.043s | 0.036s | 两者接近，快照查询不是主要瓶颈 |
| no-op / diff apply | 17.38s | 0.00004s | AGFS artifact 缺 MD5 manifest，40 文件回退远程正文比较 |
| no-op / Semantic DAG | 0s | 0s | 两者都识别为健康 no-op，不入 SemanticQueue |
| no-op / temp 清理 | 2.72s | 无远程清理 | AGFS 删除远程 temp 树 |
| edit-one / 解析及产物写入 | 16.25s | 0.13s | 修改比例不影响 AGFS 全量 temp 上传 |
| edit-one / 图片 URI 规范化 | 0.81s | 0.008s | 同上 |
| edit-one / N/F/V 快照 | 0.046s | 0.036s | 两者接近 |
| edit-one / diff apply | 15.49s | 0.067s | AGFS 仍先远程比较 40 个文件，local 按 manifest MD5 只提交变化文件 |
| edit-one / Semantic DAG | 1.20s | 0.89s | 两者均执行相同最小 DAG，差异属于远程 I/O/运行波动 |
| edit-one / temp 清理 | 2.35s | 无远程清理 | AGFS 删除远程 temp 树 |

#### 队列工作量和正确性

| 场景 | AGFS Semantic | AGFS Embedding | local Semantic | local Embedding | 文件/正文/L2 校验 |
|---|---:|---:|---:|---:|---|
| initial | 1 | 54 | 1 | 54 | 两组均 40/40/40 |
| no-op | 0 | 0 | 0 | 0 | 两组均 40/40/40 |
| edit-one | 1 | 1 | 1 | 1 | 两组均 40/40/40 |

队列计数只统计当前资源请求归属的根消息；initial 的阶段探针观察到 56 次 embedding handler/upsert，其中另有父目录刷新产生的 2 条目录向量。两种模式的语义工作量一致：initial 都是 40 个文件节点、8 个目录节点；no-op 均没有语义 DAG；edit-one 均为 1 个文件节点和 1 个快速目录判断，实际目录 LLM 为 0。

六轮 API 均为 `success`，文件和正文校验均为 40/40，L2 索引均为 40/40；missing files、unexpected files、content mismatches、missing vectors、Semantic error_count 和 Embedding error_count 均为 0。跨模式 edit-one 终态 validation 完全相同。本次没有保存全量 L0/L1 正文及 dense vector 快照，因此不宣称两次独立模型生成的目录摘要或浮点向量字节级一致。

AGFS 剩余瓶颈已经定位：当前 `CodeRepositoryParser` 的 AGFS 旧写入分支不会生成 `.artifact_manifest.json`，导致新树文件没有预计算 MD5。增量 diff 因而对所有 40 个文件走正文回退比较，从 AGFS temp 和正式 S3 读取 bytes；即使 no-op，`resource_diff_apply` 仍需 17.38 秒。后续若让 AGFS 写入也统一走 `AgfsParseOutputStore` 并在写最终 bytes 时生成 MD5 manifest，可消除这部分全量远程正文比较；但每轮 16–18 秒的 AGFS temp 全量上传及 2–3 秒清理仍然存在。

证据目录：

- 当前 AGFS：`.scratch/ingest-profile/semantic-plan40-agfs-profiled-20260913-2250`
- 当前 local：`.scratch/ingest-profile/semantic-plan40-local-profiled-20260913-2300`
- 优化前 shared-only：`/Users/bytedance/github_openviking/OpenViking/.worktrees/add-resource-shared-baseline/.scratch/ingest-profile/shared-only-baseline40-20260913-012946`

每个场景各 1 个有效样本，结论用于验证功能、阶段归因和工作量收敛，不宣称 P95 或统计显著性。

### 18.14 642 文件三模式、四场景资源评测

为同时验证首次导入、健康 no-op、单文件修改和较大比例修改，使用冻结的 OpenViking Python 源码集执行三组对照。语料包含 642 个文件、7,061,967 bytes、188,387 行；三组 `environment.json.manifest` 规范化后的 SHA-256 均为 `02cbd79658c3074f9323a4d9e2e175a61141136978705862cb2c66c53c6c1450`，逐文件路径、大小、行数和 SHA-256 相同。三组都使用 shared HTTP ZIP 输入、8 路 artifact 上传并发、本地向量库、远程 S3 正式文件系统，以及相同模型和队列配置。

三组仅在代码版本和解析产物后端上不同：

- 优化前 shared-only 基线固定在 `6e04f20ed4f90e79c50f32f88999e2a1e9717ed1`，解析产物写入 AGFS。
- 当前 SemanticPlan + AGFS 和 SemanticPlan + local 固定在 `17e62a015881cac98ecdb81ad207a13a0628b042`。两者只切换 `storage.parse_output.mode`。
- 每个场景在 shared 上传后固定等待 5 秒再调用 `add_resources`；端到端时间包含这 5 秒，阶段时间不包含。
- `edit-one` 追加一个不改变稳定 AST skeleton 的顶层常量；`edit-10%` 在 64 个文件中追加唯一函数，确保文件摘要和受影响目录摘要发生变化。三组 changed list 完全相同。
- 每组按照 initial → no-op → edit-one → edit-10% 顺序运行，后两个修改场景在同一目标树上累积。每个场景只有 1 个样本，不宣称中位数、P95 或统计显著性。

#### 端到端耗时

| 场景 | 优化前 shared-only | SemanticPlan + AGFS | SemanticPlan + local | AGFS 相对基线 | local 相对基线 | local 相对 AGFS |
|---|---:|---:|---:|---:|---:|---:|
| initial | 387.68s | 391.83s | 133.89s | +1.1% | -65.5%（2.90x） | -65.8%（2.93x） |
| no-op | 433.15s | 343.20s | 10.76s | -20.8%（1.26x） | -97.5%（40.26x） | -96.9%（31.90x） |
| edit-one | 437.99s | 336.41s | 9.79s | -23.2%（1.30x） | -97.8%（44.72x） | -97.1%（34.35x） |
| edit-10% | 619.85s（正确性失败） | 448.19s | 130.44s | 原始值 -27.7% | 原始值 -79.0% | -70.9%（3.44x） |

基线 edit-10% 虽然 API 和队列均返回 success，但正式树和 L2 都只有 641/642，不能作为有效性能样本；表中的相对基线降幅仅用于诊断耗时量级，不作为正式加速比。其余对照都通过正确性校验。扣除三组共同的 5 秒等待后，local 的 no-op 和 edit-one 实际处理部分分别约为 5.76 秒和 4.79 秒。

#### 主要阶段耗时

以下均使用 `wall_union_s`。阶段存在嵌套和并行，不能逐列求和还原端到端时间。`提交/diff` 对 initial 表示首次正式树提交，对增量场景表示目标快照与 diff apply；优化前增量场景对应旧 `sync_tree`。

| 模式 / 场景 | 解析及产物写入 | 图片 URI 规范化 | 提交 / diff | Semantic DAG |
|---|---:|---:|---:|---:|
| 基线 / initial | 147.56s | — | 141.76s | 84.74s |
| 基线 / no-op | 146.84s | 0.27s | 190.06s | 77.32s |
| 基线 / edit-one | 147.48s | 0.26s | 190.50s | 79.25s |
| 基线 / edit-10% | 145.22s | 0.48s | 392.00s | 63.97s |
| 当前 AGFS / initial | 144.93s | 8.03s | 133.43s | 91.65s |
| 当前 AGFS / no-op | 141.20s | 7.01s | 178.35s | 0s |
| 当前 AGFS / edit-one | 141.85s | 7.22s | 172.94s | 0.45s |
| 当前 AGFS / edit-10% | 139.10s | 7.01s | 177.85s | 103.92s |
| 当前 local / initial | 0.86s | 0.04s | 42.25s | 81.25s |
| 当前 local / no-op | 1.02s | 0.05s | 0.20s | 0s |
| 当前 local / edit-one | 0.86s | 0.05s | 0.19s | 0.31s |
| 当前 local / edit-10% | 2.03s | 0.05s | 4.43s | 104.07s |

所有场景的 shared 上传和 worker 物化都小于 0.8 秒；shared 已经消除了旧本地入口的重复 SOURCE 暂存成本，不再是主瓶颈。当前 AGFS 的主要剩余成本是：每次仍把 642 个解析产物写入远程 temp，约 139–145 秒；增量 diff 又因 AGFS 产物没有预计算 MD5 manifest 而回退读取和比较远程正文，约 173–178 秒。SemanticPlan 已经消除了 no-op 的全部语义工作，但无法消除这两段远程文件 I/O。

local 模式在解析时写本地 artifact 并计算最终 bytes 的 MD5；no-op 的 diff apply 为 0.00002 秒，edit-one 为 0.039 秒，只在确定有变化后才上传正式文件。edit-10% 的 4.15 秒 diff apply 对应 64 个变化文件的正式上传。initial 仍需把全量正式文件提交到 S3，因此 persist 为 42.25 秒；其 133.89 秒端到端时间中，Semantic DAG 的 81.25 秒已成为主要成本。

#### 语义与向量工作量

| 场景 | 基线：文件节点 / 目录节点 / overview / embedding | 当前 AGFS | 当前 local |
|---|---:|---:|---:|
| initial | 642 / 77 / 77 / 796 | 642 / 77 / 77 / 796 | 642 / 77 / 77 / 796 |
| no-op | 642 / 77 / 51 / 419 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| edit-one | 642 / 77 / 51 / 418 | 1 / 1 / 0 / 1 | 1 / 1 / 0 / 1 |
| edit-10% | 641 / 77 / 54 / 462 | 64 / 11 / 11 / 86 | 64 / 11 / 11 / 86 |

这里的 overview 使用 `generate_overview.calls`，代表实际目录 LLM 调用；目录节点只表示 DAG 访问或快速判断，不等于一定调用 LLM。edit-one 的文件稳定摘要不变，因此不重新生成目录 overview；但本测试通过 ZIP 输入，`is_code_repo=False`，L2 embedding 默认仍以正文为输入，所以修改文件仍产生 1 次 embedding，而不是代码仓库摘要模式下的纯 `UPDATE_FIELDS`。AGFS 与 local 的语义工作量完全一致，说明 parse output backend 只改变存储和 diff 成本，没有改变 SemanticPlan 的裁剪结果。

#### 进程树内存与本地磁盘峰值

RSS 每 0.1 秒采样，统计 benchmark/server 当前进程及递归子进程的 RSS 总和；本地磁盘每 1 秒采样，只统计本次实验目录，以及该进程新建的 `ov_shared_source_*`、`ov_zip_*` 本机临时目录。远程 S3/AGFS 容量不在统计范围内。

| 模式 / 场景 | RSS 峰值 | 本场景 RSS 峰值增量 | 本地磁盘峰值 | 本场景磁盘峰值增量 |
|---|---:|---:|---:|---:|
| 基线 / initial | 344.97 MiB | 17.11 MiB | 27.20 MiB | 17.23 MiB |
| 基线 / no-op | 350.47 MiB | 3.45 MiB | 40.07 MiB | 8.55 MiB |
| 基线 / edit-one | 352.12 MiB | 7.08 MiB | 50.30 MiB | 8.53 MiB |
| 基线 / edit-10% | 352.61 MiB | 1.41 MiB | 59.20 MiB | 8.53 MiB |
| 当前 AGFS / initial | 340.56 MiB | 16.20 MiB | 29.36 MiB | 19.37 MiB |
| 当前 AGFS / no-op | 360.00 MiB | 5.08 MiB | 41.21 MiB | 8.55 MiB |
| 当前 AGFS / edit-one | 318.45 MiB | 6.22 MiB | 44.10 MiB | 8.55 MiB |
| 当前 AGFS / edit-10% | 318.16 MiB | 6.22 MiB | 46.98 MiB | 8.55 MiB |
| 当前 local / initial | 331.59 MiB | 9.67 MiB | 29.18 MiB | 19.20 MiB |
| 当前 local / no-op | 209.20 MiB | 12.83 MiB | 37.34 MiB | 6.81 MiB |
| 当前 local / edit-one | 214.92 MiB | 9.36 MiB | 39.21 MiB | 8.55 MiB |
| 当前 local / edit-10% | 314.86 MiB | 136.88 MiB | 43.86 MiB | 13.05 MiB |

local 模式没有出现内存峰值回退：其最大 RSS 为 331.59 MiB，低于基线最大值 352.61 MiB 和当前 AGFS 最大值 360.00 MiB。local edit-10% 的场景内 RSS 增量较高，是因为该轮重新触发 64 个文件节点、11 个目录 LLM 和 86 次 embedding；绝对峰值仍未超过两种 AGFS 路径。

磁盘绝对峰值会随同一进程内四个顺序场景保留的结果、ZIP 和本地向量库增长，因此跨场景更应同时看 `peak_delta`。local 没有把解析产物写入远程 AGFS，但会短暂保留本地 parse artifact；本轮最大本地磁盘峰值为 43.86 MiB，低于基线的 59.20 MiB 和当前 AGFS 的 46.98 MiB。该数据只回答本机落盘成本，不代表远程对象存储占用。

#### 正确性与故障发现

| 模式 | initial | no-op | edit-one | edit-10% |
|---|---|---|---|---|
| 优化前基线 | 642/642 通过 | 642/642 通过 | 642/642 通过 | **失败：641/642** |
| 当前 AGFS | 642/642 通过 | 642/642 通过 | 642/642 通过 | 642/642 通过 |
| 当前 local | 642/642 通过 | 642/642 通过 | 642/642 通过 | 642/642 通过 |

“通过”同时要求正式文件 URI 集、逐文件正文、L2 URI 集均一致，且 missing、unexpected、content mismatch、vector missing 和队列 error 均为 0。当前 AGFS/local 八轮全部满足这些条件。

基线 edit-10% 在旧 `sync_tree` 中移动 `core/identifiers.py` 时发生 `lock acquire timed out after 0ms`，最终正式文件和 L2 都缺少该 URI；但是 API 仍返回 success，Semantic/Embedding `error_count` 也都是 0。这说明旧链路不仅慢，而且会吞掉单文件同步失败并提交不完整结果。它是本次正确性评测发现，不应通过挑选重跑样本隐藏。

基线 initial 和 no-op 各发生 1 次瞬态 S3 write 失败，由 benchmark 层最多 3 次的短退避重试恢复；正式 OpenViking 代码没有增加 shared 重试。当前 AGFS/local 运行中均未观察到 write 失败或重试。重试次数已随每个结果 JSON 保存。

#### 结论

1. SemanticPlan/最小 DAG 本身有效：即使保留 AGFS parse output，no-op 和 edit-one 也分别比旧基线快 20.8% 和 23.2%，语义工作量从全树收敛为 0 和 1 个文件节点。
2. 最大收益来自 local parse output 与延迟上传：no-op 为 10.76 秒、edit-one 为 9.79 秒，相对基线分别下降 97.5% 和 97.8%；相对同一当前代码的 AGFS 模式也下降 96.9% 和 97.1%。
3. 首次导入仍需全量语义和正式 S3 提交。SemanticPlan + AGFS 与基线基本持平，local 通过去掉 AGFS temp 全量写入将端到端从 387.68 秒降到 133.89 秒，下降 65.5%。
4. 10% 修改时模型计算重新成为主成本：local 的文件系统阶段约 6.5 秒，Semantic DAG 为 104.07 秒，最终 130.44 秒；因此收益从 no-op/edit-one 的约 40–45 倍回落，但相对当前 AGFS 仍快 3.44 倍。
5. 资源代价可控。local 没有提高绝对 RSS 峰值，本地磁盘最大峰值也低于两种 AGFS 路径；新增本地 artifact 的空间成本没有抵消延迟收益。
6. 若还要优化 AGFS 模式，应优先让 AGFS parse output 在写最终 bytes 时生成 MD5 manifest，避免增量 diff 的 642 文件远程正文回退比较；但远程 temp 全量上传本身仍会保留约 140 秒成本，无法达到 local 模式的量级。

证据目录：

- 优化前基线：`/Users/bytedance/github_openviking/OpenViking/.worktrees/add-resource-shared-baseline/.scratch/ingest-profile/shared-only-full-resources-retry-20260914`
- 当前 AGFS：`.scratch/ingest-profile/semantic-plan-full-agfs-resources-20260914`
- 当前 local：`.scratch/ingest-profile/semantic-plan-full-local-resources-20260914`

benchmark runner 和测试仍只保留在本地工作区，不纳入正式提交。

### 18.15 867 文件最终修复版 local 与远程 main 三轮验收

在 642 文件评测之后，又使用真实的 `third_rank_service` 仓库完成了三轮最终验收。该结果替代早期 optimized 样本，作为本轮优化的最终性能与正确性结论。optimized 使用已经补齐资源内最小祖先闭包的最终代码 `ad465f97101e27f13197bf88b29472532b43a4f1`，main 基线使用远程 main 的 `192b813e7e3106680a5534e2d4c9bcf6d2390abd`；两组均由 wrapper `c91e914b727e6944e487b951fc2d735789c15fbc` 启动。

实验通过 HTTP 调用 `add_resources`，不使用 Rust CLI。每轮创建新的 account 和独立服务环境，并在同一进程内依次执行 initial、no-op、edit-one、edit-10%。三轮使用同一份冻结输入：

- 867 个文件，10,635,180 bytes，199,807 行。
- manifest SHA-256 为 `bf9d835a1c635e08c4cd1dbb824875e549834ff5ddbde0baa8190653ab339d16`。
- edit-one 修改 `BASE_BUILD.py`；edit-10% 累计修改 87 个文件。
- 每个场景都校验正式文件 URI、逐文件正文、L2 URI、向量 identity 以及向量记录中的 MD5。`NOT_FILLED` 计入 MD5 missing，不计入非空错误 MD5。
- 阶段耗时均来自本次优化专用探针的 `wall_union_s`；阶段可嵌套或并行，不能相加还原端到端耗时。
- RSS 每 0.1 秒采样 benchmark/server 进程树，本地磁盘每 1 秒采样当前实验目录及本轮产生的临时目录；远程 S3/AGFS 空间不包含在内。

#### 端到端耗时与优化收益

| 场景 | 最终 local 三轮 | local 中位数 | main 三轮 | main 中位数 | local 相对 main |
|---|---:|---:|---:|---:|---:|
| initial | 1281.956 / 1159.608 / 1234.737s | 1234.737s | 1772.045 / 1797.871 / 1915.498s | 1797.871s | 1.46x，减少 31.3% |
| no-op | 178.218 / 355.679 / 177.027s | 178.218s | 1572.042 / 1653.903 / 1742.917s | 1653.903s | 9.28x，减少 89.2% |
| edit-one | 219.084 / 233.918 / 239.519s | 233.918s | 1575.083 / 1696.052 / 1738.261s | 1696.052s | 7.25x，减少 86.2% |
| edit-10% | 373.421 / 395.633 / 407.972s | 395.633s | 1713.327 / 1862.750 / 2129.230s | 1862.750s | 4.71x，减少 78.8% |

no-op 的 local 中位数没有被第二轮 355.679 秒异常样本拉高，因为另外两轮分别为 178.218 秒和 177.027 秒。即使保留该异常样本而不挑选重跑，三轮中位数仍比 main 快 9.28 倍。edit-one 和 edit-10% 的收益来自 local artifact、基于 manifest MD5 的增量提交和最小 Semantic DAG 的共同作用；initial 仍需全量正式树提交和全量语义生成，因此加速比低于增量场景。

#### 服务端主要阶段中位数

| 模式 / 场景 | 解析 | 正式树提交或快照/同步 | SemanticPlan | Semantic DAG | file summary 调用 | overview 调用 | embedding/upsert 调用 |
|---|---:|---:|---:|---:|---:|---:|---:|
| final local / initial | 42.776s | local persist 729.684s | 0.030s | 355.074s | 867 | 174 | 1215 / 1215 |
| final local / no-op | 43.699s | snapshot 34.860s | 0.030s | 0s | 0 | 0 | 0 / 0 |
| final local / edit-one | 41.466s | snapshot 30.071s | 0.047s | 54.776s | 1 | 2 | 3 / 3 |
| final local / edit-10% | 41.606s | snapshot 28.740s | 11.625s | 185.901s | 87 | 12 | 107 / 107 |
| main / initial | 1121.273s | persist 100.641s | — | 323.256s | 867 | 174 | 1215 / 1215 |
| main / no-op | 1103.945s | sync tree 177.401s | — | 291.014s | 866 | 93 | 742 / 742 |
| main / edit-one | 1142.379s | sync tree 157.336s | — | 309.905s | 867 | 90 | 734 / 734 |
| main / edit-10% | 1120.365s | sync tree 341.007s | — | 307.640s | 866 | 105 | 797 / 797 |

这里的调用数同样取三轮中位数；final local 第二轮 no-op 的异常工作量在下一节单独展开。正常 local no-op 不进入 Semantic DAG，edit-one 只重新处理一个文件；edit-10% 每轮都准确产生 87 次 file summary，而 main 在所有增量场景中仍近似全量解析和重做语义工作。

#### CPU、RSS 与本地磁盘

以下均为三轮中位数。CPU 利用率按 `cpu_seconds / elapsed_s` 计算；略高于 100% 表示采样进程树存在少量并行 CPU 时间。RSS 和磁盘同时给出绝对峰值与相对场景开始时的峰值增量。

| 模式 / 场景 | CPU 时间 | CPU 利用率 | RSS 峰值 / 增量 | 本地磁盘峰值 / 增量 |
|---|---:|---:|---:|---:|
| final local / initial | 1253.70s | 101.54% | 788.54 / 278.04 MiB | 33.38 / 21.04 MiB |
| final local / no-op | 177.81s | 99.45% | 858.93 / 19.04 MiB | 42.87 / 20.53 MiB |
| final local / edit-one | 232.28s | 99.64% | 868.86 / 6.50 MiB | 43.94 / 20.53 MiB |
| final local / edit-10% | 393.92s | 99.78% | 912.70 / 36.51 MiB | 46.71 / 21.14 MiB |
| main / initial | 1832.17s | 101.96% | 807.11 / 299.03 MiB | 24.41 / 12.06 MiB |
| main / no-op | 1685.86s | 101.93% | 948.25 / 92.59 MiB | 37.84 / 12.06 MiB |
| main / edit-one | 1728.75s | 101.86% | 1040.92 / 18.76 MiB | 51.61 / 12.06 MiB |
| main / edit-10% | 1898.73s | 101.93% | 1081.37 / 15.20 MiB | 64.35 / 12.07 MiB |

local 以约 8–9 MiB 的场景磁盘峰值增量换取本地 parse artifact 和 manifest，从而消除 main 每轮约 1100 秒的远程 AGFS 解析产物写入。其 RSS 绝对峰值在四个场景中均低于对应 main；磁盘绝对值会随同一进程内保留的 ZIP、结果和本地向量库逐轮增长，因此模式间应结合峰值增量理解。

#### 正确性对比

| 模式 / 场景 | 有效轮次 | 正式文件 | L2 | 正文错误 | 向量缺失 | identity 错误 | MD5 missing | 非空错误 MD5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| final local / initial | 3/3 | 每轮 867/867 | 每轮 867/867 | 0 | 0 | 0 | 0 | 0 |
| final local / no-op | 3/3 | 每轮 867/867 | 每轮 867/867 | 0 | 0 | 0 | 0 | 0 |
| final local / edit-one | 3/3 | 每轮 867/867 | 每轮 867/867 | 0 | 0 | 0 | 0 | 0 |
| final local / edit-10% | 3/3 | 每轮 867/867 | 每轮 867/867 | 0 | 0 | 0 | 0 | 0 |
| main / initial | 3/3 | 每轮 867/867 | 每轮 867/867 | 0 | 0 | 0 | 2601 | 0 |
| main / no-op | 1/3 | 合计缺 3 个 | 合计缺 3 个 | 0 | 3 | 0 | 2598 | 0 |
| main / edit-one | 2/3 | 合计缺 2 个 | 合计缺 2 个 | 0 | 2 | 0 | 2599 | 0 |
| main / edit-10% | 1/3 | 合计缺 2 个 | 合计缺 2 个 | 0 | 2 | 0 | 2599 | 0 |

final local 共 12/12 场景通过：每场景均为 867/867 正式文件、867/867 正文和 867/867 L2，且正文、identity、MD5 均无错误。特别是三轮 edit-10% 均为 87 次 file summary，最终 MD5 mismatch 为 0，说明资源内最小祖先闭包修复消除了早期稳定出现的 14/87 深层文件旧 MD5 问题。

main 的失败表现为偶发少 1–2 个正式文件及其 L2，不是“查到非空错误 MD5”。main 不写入该字段，因此可见记录中的 MD5 均被归类为 `NOT_FILLED`/missing；表内 2601、2598、2599 是三轮逐记录 missing 数量之和。两类失败需要区分：main 是缺失，早期 optimized 缺祖先闭包时则会返回存在但非空且错误的旧 MD5；后者不能被 missing 语义掩盖。

#### 第二轮 no-op 为何是 355.679 秒

第二轮 initial 的严格逐 ID 校验已经读到 867/867 条 L2；紧接着的 no-op 在按 URI/level 做范围扫描时却只得到 `V_vectors=862`。diff 因此将 862 个文件判为 unchanged，将另外 5 个正式文件判为 `vector_missing` 并进入 repair：

```text
N_files=867 F_files=867 V_vectors=862
plan_unchanged=862 plan_repair=5 vector_missing=5
```

这 5 个 repair 触发了 5 次 file summary、18 个目录节点、16 次 overview LLM，以及 31 次 embedding/upsert。对应阶段为 SemanticPlan 5.830 秒、Semantic DAG 167.481 秒，最终把端到端耗时推高到 355.679 秒。该轮结束后的强校验仍为 867/867，MD5 missing 和 mismatch 都为 0；随后 edit-one 的同类范围扫描也恢复为 867。

现有证据说明这是向量库两个读取路径的短暂可见性差异：逐 ID `get` 已经可见 867 条，但 URI/level 范围索引在该次扫描中只返回 862 条。当前 strict count 与范围扫描仍依赖同一份范围结果，无法提前识别这 5 条“已可 get、暂不可 scan”的记录，所以实现选择了保守 repair，而不是错误地当作 no-op。它造成额外耗时，但没有产生错误 MD5 或错误终态。后续若要稳定 no-op 延迟，应让缺项在 repair 前按稳定 ID 做二次 `get`，或为范围索引增加可见性屏障/有界重试。

#### 结论与适用边界

1. 最终 local 方案在三轮健康路径上同时满足性能和正确性要求：四个场景中位数分别比 main 快 1.46x、9.28x、7.25x 和 4.71x，12/12 场景没有非空错误 MD5。
2. 完整的资源内最小祖先闭包是 edit-10% 正确性的必要条件。active ancestor 目录携带 L0+L1，未变化 sibling 目录只携带当前父目录聚合所需的 L0，文件携带 L2；整个资源从一个连通 execution root 自底向上执行。
3. 第二轮 no-op 是范围索引短暂少返回 5 条导致的保守 repair，不是内容或 MD5 错误；它说明 no-op 的尾延迟仍受向量范围索引可见性影响。
4. 本节验证的是串行、成功完成的健康路径，以及实验中实际出现的索引可见性波动。它不能证明并发覆盖、异步写晚到、部分写失败或异常取消窗口已经安全；这些故障模型需要单独的 generation fencing、失败注入和并发测试，不能由 12/12 健康样本替代。

原始证据目录：

- 最终 local：`/tmp/third-rank-benchmark-v4/optimized-round-1`、`optimized-round-2-final`、`optimized-round-3-final`。
- main：`/tmp/third-rank-benchmark-v3/baseline-round-1`、`baseline-round-2`、`baseline-round-3`。
- 24 个场景汇总：`/tmp/third-rank-final-all.tsv`。

这些 `/tmp` 路径是本机实验留存位置，不随仓库提交；每个场景的 JSON、服务日志、环境与 manifest 信息均保存在相应证据目录中。
