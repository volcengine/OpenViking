# Temp Upload 单接口分布式方案设计

## 背景

当前 `POST /api/v1/resources/temp_upload` 的实现会将上传内容直接写入当前实例本机的 `upload_temp_dir`，并返回一个本地文件名形式的 `temp_file_id`。随后：

- `POST /api/v1/resources`
- `POST /api/v1/skills`
- `POST /api/v1/pack/import`

会根据该 `temp_file_id` 回到本机目录读取文件并继续处理。

这套机制在单机场景可用，但在远程分布式部署下存在根本问题：

1. 上传请求可能落到实例 A。
2. 后续消费请求可能落到实例 B。
3. 实例 B 本地没有该上传文件，导致 `temp_file_id` 无法解析。

本方案的目标是在**不新增上传接口**、**不破坏当前默认行为**的前提下，为 `temp_upload` 增加一个可选的共享上传模式，使其在分布式集群下可稳定使用。

---

## 目标

本方案需要满足以下目标：

1. 保留现有 `POST /api/v1/resources/temp_upload` 作为唯一上传入口。
2. 保留当前默认行为，不影响现有 CLI、console、SDK、curl 调用。
3. 支持通过一个新增参数显式启用分布式共享上传。
4. 保持现有消费接口不变，仍然只接受 `temp_file_id`。
5. `shared` 模式下的 `temp_file_id` 是一次性消费 token。
6. 成功消费后立即删除共享上传对象。
7. 失败时允许重试。
8. 不依赖“每个实例周期性扫描全量临时目录”的 cleaner。
9. 第一阶段尽量复用现有 `add_resource` / `add_skill` / `pack.import` 路径。

---

## 非目标

本方案明确不覆盖以下内容：

1. 不新增 init / complete / presign 两段式上传接口。
2. 不实现断点续传或 multipart object upload。
3. 不引入 Redis / DB 作为必须依赖。
4. 不在第一阶段把全部 parser 改为直接流式读取对象存储。
5. 不允许客户端直接使用 shared 存储 URI 参与导入。

---

## 总体方案

保留原有接口：

`POST /api/v1/resources/temp_upload`

新增 multipart 表单字段：

- `upload_mode=local|shared`

默认值：

- `local`

语义如下：

- `local`：保持当前行为，上传内容写本机临时目录。
- `shared`：上传内容写共享临时空间，支持任意实例消费。

后续消费接口全部保持不变：

- `POST /api/v1/resources`
- `POST /api/v1/skills`
- `POST /api/v1/pack/import`

调用方继续只传：

- `temp_file_id`

服务端内部根据 `temp_file_id` 的格式和元数据，决定走 local 解析还是 shared 解析。

---

## API 设计

### 上传接口

接口：

`POST /api/v1/resources/temp_upload`

请求字段：

- `file`：multipart 文件，必填
- `telemetry`：保留现有字段
- `upload_mode`：新增字段，可选
  - `local`
  - `shared`
  - 默认 `local`

返回结构保持不变：

```json
{
  "temp_file_id": "..."
}
```

### 消费接口

以下接口均不做对外结构变更：

- `POST /api/v1/resources`
- `POST /api/v1/skills`
- `POST /api/v1/pack/import`

它们仍然只接收 `temp_file_id`，不增加额外模式字段。

---

## `temp_file_id` 设计

### local 模式

沿用当前格式：

```text
upload_<uuid>.<ext>
```

示例：

```text
upload_3113340a08084a9998b6351146fbbd42.zip
```

### shared 模式

shared token 格式定义为：

```text
shared_<upload_id>
```

示例：

```text
shared_7f3c1b8d4f2e4b1bb0f6e8b2d9a4c123
```

### `upload_id` 生成方式

由服务端生成：

```python
uuid.uuid4().hex
```

这样做的原因：

1. 实现简单。
2. 熵高，碰撞概率极低。
3. 不暴露账号、用户、时间等业务信息。
4. 便于直接拼接成 `shared_<upload_id>`。

### 解析规则

- 以 `upload_` 开头且符合旧格式：按 local 解析。
- 以 `shared_` 开头：按 shared 解析。
- 其他格式：拒绝。

---

## shared 存储布局

shared 上传对象统一放到受控内部前缀：

```text
viking://temp/upload/{user}/{account}/{upload_id}/
```

目录中包含两个对象：

- `content`
- `meta.json`

示例：

```text
viking://temp/upload/alice/acme/7f3c1b8d4f2e4b1bb0f6e8b2d9a4c123/content
viking://temp/upload/alice/acme/7f3c1b8d4f2e4b1bb0f6e8b2d9a4c123/meta.json
```

这样设计的原因：

1. 它是临时上传空间，不应进入最终 `viking://resources`。
2. 现有系统已使用 `viking://temp/...` 作为处理中临时空间，语义一致。
3. 可以复用 VikingFS/AGFS/S3 能力。
4. 便于对象存储 lifecycle 兜底清理。

### 安全边界

`viking://temp/upload/...` 是**内部受控临时上传空间**，不是普通 temp 工作目录。

必须满足：

1. 客户端不能直接传该 URI 给业务导入接口。
2. 服务端只接受 `temp_file_id`，不接受客户端指定 shared `storage_uri`。
3. shared 内容路径只对服务端内部可见。
4. shared 上传内容不通过普通资源读取能力直接暴露给普通用户。

---

## `meta.json` 设计

### 目的

`meta.json` 用于承载 shared 上传的最小控制信息，包括：

- 所属账号 / 用户
- 原始文件名
- 内容对象位置
- 文件大小 / 摘要
- 过期时间
- 当前状态
- 正在消费的抢占信息

### 结构

建议第一版使用以下 schema：

```json
{
  "version": 1,
  "upload_mode": "shared",
  "upload_id": "7f3c1b8d4f2e4b1bb0f6e8b2d9a4c123",
  "temp_file_id": "shared_7f3c1b8d4f2e4b1bb0f6e8b2d9a4c123",

  "account": "acme",
  "user": "alice",
  "agent": "default",

  "original_filename": "repo.zip",
  "content_type": "application/zip",
  "file_ext": ".zip",
  "size": 1048576,
  "sha256": null,

  "storage_uri": "viking://temp/upload/alice/acme/7f3c1b8d4f2e4b1bb0f6e8b2d9a4c123/content",

  "state": "uploaded",
  "created_at": 1778131200,
  "updated_at": 1778131200,
  "expire_at": 1778134800,
  "consuming_started_at": null
}
```

### 字段说明

- `version`：元数据 schema 版本
- `upload_mode`：固定 `shared`
- `upload_id`：shared 上传主键
- `temp_file_id`：对外 token
- `account` / `user` / `agent`：归属信息
- `original_filename`：上传时原始文件名
- `content_type`：MIME 类型
- `file_ext`：扩展名，materialize 本地临时文件时保留
- `size`：文件大小
- `sha256`：可选完整性校验
- `storage_uri`：shared 内容对象位置
- `state`：状态机
- `created_at` / `updated_at`：时间戳
- `expire_at`：过期时间
- `consuming_started_at`：抢占消费开始时间，用于异常恢复

---

## 状态机设计

shared 模式只使用两个状态：

- `uploaded`
- `consuming`

### 状态含义

- `uploaded`：可被消费
- `consuming`：已被某个实例抢占，正在消费中

### 为什么不使用 `consumed`

本方案里，shared token 是一次性消费 token。

消费成功后将立即删除整个 upload 目录，因此不需要持久化一个长期存在的 `consumed` 状态。

### 状态流转

正常流转如下：

```text
uploaded -> consuming -> delete
```

失败流转如下：

```text
uploaded -> consuming -> uploaded
```

异常恢复流转如下：

```text
uploaded -> consuming --(timeout recover)--> uploaded
```

---

## 上传完成语义

shared 上传视为完成的条件是：

1. `content` 已写完
2. `meta.json` 已写入
3. `meta.state == "uploaded"`

### 写入顺序

必须严格按以下顺序：

1. 生成 `upload_id`
2. 创建 upload 目录
3. 流式写 `content`
4. 边写边统计 `size`，可选计算 `sha256`
5. 最后写 `meta.json`

### 结论

- `meta.json` 是唯一完成标志
- 只有 `content` 没有 `meta.json` 的对象是 orphan，不可消费
- orphan 由对象存储 lifecycle 兜底清理

---

## 上传流程

### local 模式

1. 接收请求
2. `upload_mode=local`
3. 将文件流式写入本地 `upload_temp_dir`
4. 写对应 `.ov_upload.meta`
5. 返回 `upload_...`

### shared 模式

1. 接收请求
2. `upload_mode=shared`
3. 校验服务端已启用 shared 上传
4. 生成 `upload_id`
5. 边读边校验大小上限
6. 流式写入 shared `content`
7. 生成并写入 `meta.json`
8. 返回 `shared_<upload_id>`

---

## 大小限制

建议配置：

- `shared_max_size_bytes`
- `local_max_size_bytes`，可选

### 执行方式

上传时边读边计数：

1. 每读一个 chunk，累计字节数
2. 如果超过上限：
   - 立即停止上传
   - 删除已写半成品
   - 返回错误

不能等上传完成后再做大小校验。

---

## 消费接口

以下接口保持不变：

- `POST /api/v1/resources`
- `POST /api/v1/skills`
- `POST /api/v1/pack/import`

调用方继续只传：

```json
{
  "temp_file_id": "..."
}
```

消费模式完全由服务端内部判断。

---

## shared 消费语义

`shared_<upload_id>` 是**一次性消费 token**。

规则如下：

1. 同一个 token 只能成功消费一次。
2. 开始消费前，必须先从 `uploaded` 抢占到 `consuming`。
3. 只有一个实例可以抢占成功。
4. 消费成功后，立即删除整个 upload 目录。
5. 消费失败后，状态恢复回 `uploaded`，允许重试。

---

## 为什么需要 `consuming`

如果不引入 `consuming` 状态，在并发场景下会出现问题：

1. 实例 A 和 B 同时读到 `uploaded`
2. A 和 B 都开始消费
3. 可能两个实例都成功

这样 shared token 就不是一次性了。

因此必须引入抢占状态：

- `uploaded -> consuming`

只有抢占成功的实例可以继续消费。

---

## 抢占规则

shared 消费开始前，必须执行原子抢占：

```text
uploaded -> consuming
```

抢占要求：

1. 只有当当前状态是 `uploaded` 时，才能成功进入 `consuming`
2. 如果当前状态已经是 `consuming`，则说明已有实例正在消费
3. 后来的实例必须直接返回错误

### 实现要求

这一状态变更必须具备原子语义，不能只是：

1. 先读状态
2. 再普通写状态

否则仍有竞态。

实现上可以复用现有事务/路径锁能力，或在 shared upload store 内实现一个最小互斥机制。无论具体机制如何，最终都必须保证：

- 同一 token 同时只有一个实例能进入 `consuming`

---

## 消费流程

### local token

保持现状：

1. 校验 `temp_file_id`
2. 校验路径位于本地 `upload_temp_dir`
3. 返回本地路径给下游逻辑

### shared token

统一流程如下：

1. 解析 `temp_file_id`
2. 判断它是 `shared_...`
3. 读取 `meta.json`
4. 校验：
   - token 格式合法
   - `account` 匹配当前请求
   - `user` 匹配当前请求
   - `expire_at > now`
   - `state == "uploaded"`
   - `content` 存在
5. 抢占状态：`uploaded -> consuming`
6. 将 shared `content` 下载到当前实例本地临时文件
7. 将本地临时文件路径交给现有消费逻辑
8. 如果业务成功：
   - 立即删除整个 shared upload 目录
9. 如果业务失败：
   - 将状态从 `consuming` 恢复为 `uploaded`
10. 请求结束时清理 materialize 出来的本地临时文件

---

## 为什么消费时要先下载到本地

第一阶段建议统一采用：

- `shared` 内容先下载到当前实例本地临时文件
- 再走现有 `add_resource` / `add_skill` / `pack.import`

原因：

1. 当前下游链路大量逻辑默认输入是本地路径。
2. 这样可以最小改动复用现有 parser / import 逻辑。
3. 不需要在第一阶段把所有解析链改成直接读对象存储流。

这意味着，例如 `add_resource` 在 shared 模式下的实际流程是：

1. 解析 token
2. 抢占
3. 下载到本地临时文件
4. 调现有 `add_resource(path=local_path, ...)`

---

## materialize 本地临时文件

### 规则

shared 内容被消费时，需要 materialize 成当前实例本地临时文件。

要求：

1. 文件名尽量保留原扩展名
2. 采用流式下载，不整文件读入内存
3. materialize 失败时删除半成品

建议生成路径示例：

```text
/tmp/ov_shared_upload_<uuid>.zip
```

### 生命周期

本地 materialize 文件是**请求级资源**。

必须由统一解析器返回：

- `local_path`
- `cleanup()`

调用方必须在最外层 `finally` 中执行 cleanup。

对 local 模式：

- `cleanup()` 为 no-op

对 shared 模式：

- `cleanup()` 删除本地临时文件

---

## 成功与失败语义

### 成功

“消费成功”的定义是：

- 目标业务接口已完成核心导入逻辑，并准备返回成功

例如：

- `resources.add_resource` 成功
- `skills.add_skill` 成功
- `pack.import` 成功

此时执行：

1. 删除整个 shared upload 目录
2. 返回成功响应

### 失败

如果业务处理失败：

1. 不删除 shared upload 目录
2. 将 `state` 从 `consuming` 恢复回 `uploaded`
3. 允许调用方后续重试
4. 清理 materialize 本地临时文件

---

## `consuming` 卡死恢复

如果实例在进入 `consuming` 后崩溃，会导致 token 长时间卡在：

```json
"state": "consuming"
```

因此必须增加超时恢复。

### 元数据字段

`meta.json` 中保留：

- `consuming_started_at`

### 恢复规则

如果发现：

- `state == "consuming"`
- 且 `now - consuming_started_at > shared_consuming_timeout_seconds`

则视为一个僵死消费。

当前实例可以执行恢复：

- 将状态回退为 `uploaded`

恢复后允许重新消费。

### 选择“回退”而不是“直接删”的原因

因为这类卡死往往意味着“消费结果未知”，直接删除会让重试机会丢失。回退到 `uploaded` 更适合恢复业务。

---

## 过期与懒删除

### 过期判断

每次消费 shared token 时，检查：

```text
expire_at > now
```

否则直接拒绝消费。

### 为什么不使用集群周期 cleaner

不采用“每个实例定时扫描全量 upload 前缀”的 cleaner，原因：

1. 集群下会重复扫目录
2. 不优雅
3. 成本高

### 懒删除定义

懒删除只针对“当前请求访问到的无效 token”。

也就是说：

- 访问到了一个 token
- 判断它已经无效
- 顺手删掉这个 token 对应的 upload 目录

它不是全量扫描，不是后台全局清理。

### 懒删除触发条件

1. token 已过期
2. `meta.json` 存在但 `content` 丢失
3. `meta.json` 损坏或关键字段非法
4. `state == "consuming"` 且明显超时异常，并且恢复失败或对象已不完整

### 删除范围

删除整个目录：

```text
viking://temp/upload/{user}/{account}/{upload_id}/
```

包括：

- `content`
- `meta.json`

### 幂等要求

懒删除必须是幂等的：

1. 目录不存在也算成功
2. 只剩部分对象也算成功
3. 删除失败不影响主错误返回

---

## 对象存储 lifecycle

对象存储 lifecycle 仍然需要保留，但只作为兜底：

1. 清理无人再访问的 orphan `content`
2. 清理已过期但没人再访问的 upload 目录
3. 清理因异常遗留的旧对象

lifecycle 不是业务过期判断主机制。

业务上“是否可消费”仍然由应用层在读时校验：

- `expire_at`
- `state`
- 归属
- 内容完整性

---

## 异常状态处理

### `meta.json` 存在，`content` 丢失

处理：

1. 返回错误
2. 尝试懒删除整个目录

### `content` 存在，`meta.json` 缺失

处理：

1. 不可消费
2. 视为 orphan
3. 由 lifecycle 清理

### token 已过期

处理：

- 返回 `Temporary upload has expired`

### token 正在被消费

处理：

- 返回 `Temporary upload is being consumed`

### token 归属不匹配

处理：

- 返回 `Temporary upload does not belong to current account/user`

---

## 安全要求

必须满足：

1. 客户端不能直接传 shared `storage_uri`
2. 客户端只能传 `temp_file_id`
3. shared 路径只服务端内部可见
4. 必须校验：
   - `account`
   - `user`
   - `state`
   - `expire_at`

local 模式继续保留当前安全规则：

1. 只允许本地 `upload_temp_dir` 下的普通文件
2. 防路径穿越
3. 防 symlink escape

---

## 配置设计

建议在 `server` 下新增：

```json
{
  "server": {
    "temp_upload": {
      "default_mode": "local",
      "shared_enabled": true,
      "shared_ttl_seconds": 3600,
      "shared_max_size_bytes": 536870912,
      "shared_consuming_timeout_seconds": 900,
      "shared_prefix": "viking://temp/upload"
    }
  }
}
```

### 字段说明

- `default_mode`
  - 第一阶段固定为 `local`
- `shared_enabled`
  - 是否允许 `upload_mode=shared`
- `shared_ttl_seconds`
  - shared 上传未消费有效期
- `shared_max_size_bytes`
  - shared 上传大小上限
- `shared_consuming_timeout_seconds`
  - `consuming` 卡死恢复阈值
- `shared_prefix`
  - shared 上传根前缀

---

## 客户端策略

为了让分布式场景真正可用，客户端需要支持显式上传模式配置。

建议客户端配置：

- `upload.mode = local | shared | auto`

第一阶段最低要求：

1. CLI 支持显式 `shared`
2. console 支持显式 `shared`
3. SDK / HTTP client 支持显式 `shared`

当前第一阶段实现中：

1. 默认行为仍然是 `local`
2. Rust CLI 可通过环境变量 `OPENVIKING_UPLOAD_MODE=shared` 显式启用 shared 上传
3. console / SDK / HTTP client 只有在显式传 `upload_mode=shared` 时才走 shared
4. 若不做任何配置，所有客户端都保持原有 local 上传行为

如果后续做 `auto`，建议规则为：

1. 非 localhost
2. 服务端声明支持 shared
3. 则自动使用 `shared`

第一阶段不依赖自动探测。

---

## 示例

### 默认 local

```bash
curl -X POST http://localhost:1933/api/v1/resources/temp_upload \
  -F "file=@./README.md"
```

返回：

```json
{
  "status": "ok",
  "result": {
    "temp_file_id": "upload_3113340a08084a9998b6351146fbbd42.md"
  }
}
```

### shared 上传

```bash
curl -X POST http://localhost:1933/api/v1/resources/temp_upload \
  -F "file=@./repo.zip" \
  -F "upload_mode=shared"
```

返回：

```json
{
  "status": "ok",
  "result": {
    "temp_file_id": "shared_7f3c1b8d4f2e4b1bb0f6e8b2d9a4c123"
  }
}
```

### shared 导入资源

```bash
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -d '{
    "temp_file_id": "shared_7f3c1b8d4f2e4b1bb0f6e8b2d9a4c123",
    "to": "viking://resources/code"
  }'
```

处理过程等价于：

1. 解析 token
2. 抢占 `uploaded -> consuming`
3. 下载 `content` 到本地临时文件
4. 调现有 `add_resource(path=local_path, ...)`
5. 成功后删除 shared upload 目录
6. 清理本地临时文件

---

## 代码改动建议

核心改动点如下：

1. `openviking/server/routers/resources.py`
   - `temp_upload` 增加 `upload_mode`
   - 改为分块流式写入
2. 新增 `openviking/server/temp_upload_store.py`
   - `TempUploadStore`
   - `LocalTempUploadStore`
   - `SharedTempUploadStore`
   - `ResolvedTempUpload`
3. `openviking/server/local_input_guard.py`
   - 从本地 temp 目录解析器升级为统一 `temp_file_id` 解析器
4. `openviking/server/routers/pack.py`
   - 接入统一 shared/local 解析
5. `openviking/server/config.py`
   - 增加 `server.temp_upload` 配置
6. CLI / console / SDK
   - 支持传 `upload_mode=shared`

---

## 测试重点

### 兼容性

1. 不传 `upload_mode` 时行为完全不变
2. local token 现有流程不回归

### shared 正常链路

1. shared 上传成功
2. 跨实例消费成功
3. `resources` / `skills` / `pack.import` 都可用

### 单次消费

1. 同一个 shared token 并发消费时只有一个成功
2. 成功后整个目录立即删除
3. 再次消费失败

### 失败重试

1. 消费失败后状态恢复为 `uploaded`
2. 后续可重新消费

### consuming 恢复

1. 实例崩溃后留下 `consuming`
2. 超时后可恢复到 `uploaded`

### 过期与异常

1. 过期 token 被拒绝
2. `meta.json` 有、`content` 缺失时触发懒删除
3. orphan 内容由 lifecycle 兜底

### 资源清理

1. materialize 文件在请求结束后删除
2. 大文件不整文件进内存

---

## 最终结论

最终方案如下：

1. 保留单接口：`POST /api/v1/resources/temp_upload`
2. 新增参数：`upload_mode=local|shared`
3. 默认：`local`
4. shared token 格式：`shared_<upload_id>`
5. `upload_id`：`uuid.uuid4().hex`
6. shared 存储：`viking://temp/upload/{user}/{account}/{upload_id}/`
7. 元数据：`meta.json`
8. 状态机：`uploaded -> consuming -> delete`
9. shared token 是一次性消费 token
10. 成功后立即删除
11. 失败恢复为 `uploaded`
12. 崩溃卡死靠 `shared_consuming_timeout_seconds` 恢复
13. 不做集群周期 cleaner
14. 懒删除只处理当前访问到的无效对象
15. 第一阶段统一下载到本地后复用现有消费链路
