# OpenViking Assets

> 实验性功能。`openviking-assets/1` 协议和命令行行为仍可能在后续版本中调整。

OpenViking Assets 用声明文件描述“一个知识库应该由哪些资源组成”。团队在一个
Catalog 中维护可接入资源的全集，再用多个 Manifest 按名称选择不同用途所需的资源。
执行 Manifest 时，OpenViking 会逐项创建或更新资源，并在本地保存资产与
`viking://` 资源之间的映射。

它适合管理多仓代码问答库、团队文档集和其他需要重复构建、持续更新的资源集合。

## 与其他资源操作的区别

| 能力 | 描述 |
| --- | --- |
| `ov add-resource <source>` | 添加或更新一个资源，描述的是一次资源操作。 |
| OpenViking Assets | 声明一组资源的预期构成，可以 review、共享并重复执行。 |
| OVPack | 导出或导入已经生成的数据快照，搬运的是内容和可选索引数据。 |

OpenViking Assets 不替代现有资源处理流程。Git 拉取、内容解析、语义提取、向量化和
Watch 更新仍由 `add_resource` 及服务端连接器完成；Assets 只增加声明、解析和逐项编排。

## 概念模型

OpenViking Assets 包含三个主要对象：

- **Catalog**：团队可接入资源的目录，包含来源、分支、默认更新周期和凭据别名。
- **Manifest**：一次构建需要选择的资产名称列表。
- **State**：某个 Manifest 上次执行的结果，以及资产到 `viking://` 资源的映射。

```text
assets.yaml + manifest.yaml
          |
          v
服务端解析和校验 openviking-assets/1
          |
          v
Resolved Assets
          |
          v
CLI 解析本地凭据和 State
          |
          v
逐个调用 add_resource -> viking:// resources
```

服务端是协议解析的权威实现。CLI 会把 Catalog 和 Manifest 的原始 YAML 发送到当前配置的
OpenViking 服务，由服务端完成严格校验并返回执行计划；服务端的解析接口本身不会创建资源。

## 协议

### Catalog

Catalog 通常命名为 `assets.yaml`：

```yaml
protocol: openviking-assets/1

defaults:
  git:
    auth_ref: team-git
    watch_interval: 1440

assets:
  - name: openviking
    connector: git
    description: OpenViking 主仓库
    params:
      repo_url: https://github.com/volcengine/OpenViking
      branch: main

  - name: requests
    connector: git
    description: Requests HTTP 客户端源码
    watch_interval: 0
    params:
      repo_url: https://github.com/psf/requests
      branch: main
```

Catalog 顶层字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `protocol` | 是 | 当前必须为 `openviking-assets/1`。 |
| `defaults` | 否 | 按连接器设置 Catalog 级默认值。 |
| `assets` | 是 | 资产定义列表。 |

`defaults.git` 支持：

| 字段 | 说明 |
| --- | --- |
| `auth_ref` | 本地凭据文件中的默认别名。 |
| `watch_interval` | 默认 Watch 周期，单位为分钟；`0` 表示不自动刷新。 |

Git 资产支持：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | Catalog 内唯一名称，必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]*`。 |
| `connector` | 是 | v1 只支持 `git`。 |
| `description` | 否 | 资产用途说明。 |
| `params.repo_url` | 是 | Git clone URL。 |
| `params.branch` | 否 | 要接入的分支；设置时不能为空。 |
| `auth_ref` | 否 | 覆盖 `defaults.git.auth_ref`。 |
| `watch_interval` | 否 | 覆盖 `defaults.git.watch_interval`。 |

Catalog 采用严格校验：未知字段、重复资产名和不支持的连接器都会使整个解析失败，
即使有问题的资产没有被当前 Manifest 选择。`params` 内容和 clone URL 安全性
针对被 Manifest 选中的资产校验。

### Manifest

Manifest 是一个平铺的资产名称列表：

```yaml
protocol: openviking-assets/1
catalog: ../assets.yaml

assets:
  - openviking
  - requests
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `protocol` | 否 | 设置时必须为 `openviking-assets/1`。 |
| `catalog` | 否 | Catalog 的描述性路径。当前 CLI 不使用此字段查找文件。 |
| `assets` | 是 | 从 Catalog 中选择的资产名称列表，解析后至少包含一个资产。 |
| `include` | 否 | v1 不支持组合其他 Manifest；非空时解析失败。 |

重复的资产名称会按首次出现的位置去重。Manifest 引用不存在的资产时，整个解析失败。

::: warning Catalog 文件查找规则
当前 CLI 不会根据 Manifest 中的 `catalog:` 字段读取文件。实际规则是：

1. 传入 `--catalog <file>` 时使用该路径；相对路径基于当前工作目录。
2. 未传入时读取 Manifest 所在目录下的 `assets.yaml`。
:::

### 资产身份

服务端根据以下信息生成稳定的 `asset_id`：

```text
connector + normalized locator + ref
```

Git URL 会去除协议、用户名前缀、端口、结尾的 `.git` 和 `/`，并把主机名统一为小写。
因此，同一仓库的 HTTPS、SSH 和 SCP 风格地址通常会得到相同定位符；不同分支会得到不同资产。

资产名称不参与身份计算。重命名资产但保持来源和分支不变时，会继续关联原资源；修改来源或
分支时会产生新资产，旧资源被报告为 orphan。

出于安全原因，clone URL 不能：

- 为空或包含控制字符；
- 以 `-` 开头；
- 使用 `ext::`、`fd::` 等 Git remote-helper 传输格式。

## 快速开始

### 前置条件

1. 安装支持 OpenViking Assets 的 `ov` CLI。
2. 配置支持 `/api/v1/openviking-assets/resolve` 的 OpenViking 服务。
3. 确认 CLI 可以连接服务：

```bash
ov health
```

### 验证示例

仓库中的完整示例位于
[`examples/openviking-assets`](https://github.com/volcengine/OpenViking/tree/main/examples/openviking-assets)。

在 OpenViking 仓库根目录执行：

```bash
ov add-resource \
  --manifest examples/openviking-assets/manifests/code-qa.yaml \
  --catalog examples/openviking-assets/assets.yaml \
  --dry-run
```

`--dry-run` 会完成以下操作：

- 读取两个本地 YAML 文件；
- 调用当前 OpenViking 服务解析并校验协议；
- 检查所有 `auth_ref` 是否能在本地解析；
- 输出每个资产将执行的 create 或 sync 操作；
- 不提交资源，也不写入 State。

### 应用 Manifest

确认计划后去掉 `--dry-run`：

```bash
ov add-resource \
  --manifest examples/openviking-assets/manifests/code-qa.yaml \
  --catalog examples/openviking-assets/assets.yaml
```

等待每个资源处理完成：

```bash
ov add-resource \
  --manifest examples/openviking-assets/manifests/code-qa.yaml \
  --catalog examples/openviking-assets/assets.yaml \
  --wait \
  --timeout 600
```

## 凭据

Catalog 只保存 `auth_ref` 别名，不应保存 token、密码或私钥。CLI 默认从以下文件解析别名：

```text
~/.openviking/openviking_assets_credentials.yaml
```

示例：

```yaml
credentials:
  team-git:
    username: oauth2
    token: replace-with-your-token
```

可以使用环境变量覆盖文件位置：

```bash
export OPENVIKING_ASSETS_CREDENTIALS_FILE=/secure/path/assets-credentials.yaml
```

执行前，CLI 会先解析所有选中资产的 `auth_ref`。只要有一个别名不存在，整个操作会在提交
任何资源之前失败。解析出的 Git 参数会通过当前配置的 OpenViking 服务连接发送给资源接口，
因此远程部署应使用 TLS，并限制凭据文件的本地访问权限。

如果目标服务已经具备访问仓库所需的 SSH key 或其他认证配置，可以不设置 `auth_ref`。

## Create、Sync 和 State

非 dry-run 执行后，CLI 在 Manifest 旁写入：

```text
<manifest-file>.state.json
```

例如：

```text
code-qa.yaml.state.json
```

State 使用 `openviking-assets-state/1` 协议，记录：

- `asset_id`、名称、连接器、定位符和 ref；
- 对应的 `resource_uri` 和 `task_id`；
- 最近一次执行状态、错误和时间。

执行规则：

| 条件 | 行为 |
| --- | --- |
| State 中没有该 `asset_id` 的资源 URI | create：创建新资源。 |
| State 中已有资源 URI | sync：把 URI 作为 `to` 再次调用 `add_resource`。 |
| 资产不再被 Manifest 选择 | 报告 orphan，保留资源和 State，不自动删除。 |
| `asset_id` 因来源或分支变化 | 创建新资产，旧资产成为 orphan。 |

State 属于执行环境，不是 Catalog 或 Manifest 协议的一部分。共享 Manifest 仓库通常应在
`.gitignore` 中加入：

```text
*.state.json
```

不要并发执行同一个 Manifest；当前 State 文件不提供跨进程锁。

内容级同步进度不保存在 Manifest State 中。持续刷新由 OpenViking Watch 和连接器负责。

## 更新周期

`watch_interval` 的优先级从高到低为：

1. CLI 的 `--watch-interval`；
2. 单个资产的 `watch_interval`；
3. `defaults.git.watch_interval`；
4. `0`，不自动刷新。

例如，临时把 Manifest 中全部资产调整为每 60 分钟刷新：

```bash
ov add-resource \
  --manifest manifests/code-qa.yaml \
  --catalog assets.yaml \
  --watch-interval 60
```

后续内容刷新由 Watch 执行，不需要周期性重新运行 Manifest。重新运行 Manifest 主要用于应用
Catalog 或 Manifest 的构成变化、恢复失败资产，或显式触发同步。

## 失败处理

默认采用 fail-fast：

1. 当前资产失败；
2. 后续资产标记为未尝试；
3. 已成功资产和失败记录写入 State；
4. 命令以非零状态退出。

使用 `--skip-failed` 可以继续处理其余资产：

```bash
ov add-resource \
  --manifest manifests/code-qa.yaml \
  --catalog assets.yaml \
  --skip-failed
```

`--skip-failed` 不会把部分失败转换为成功。只要有资产失败，命令最终仍以非零状态退出；
已经成功的资源不会回滚。全部资产失败时，命令会报告没有任何资产成功应用。

## 命令行选项

Manifest 模式的主要参数：

| 参数 | 说明 |
| --- | --- |
| `-m, --manifest <file>` | Manifest 文件。 |
| `--catalog <file>` | Catalog 文件；省略时使用 Manifest 同目录的 `assets.yaml`。 |
| `--dry-run` | 解析并输出计划，不提交资源、不写 State。 |
| `--skip-failed` | 一个资产失败后继续处理其他资产。 |
| `--wait` | 等待每个资源处理完成。 |
| `--timeout <seconds>` | `--wait` 的超时时间。 |
| `--watch-interval <minutes>` | 覆盖全部资产的更新周期。 |
| `--processing-mode <mode>` | 所有资产使用 `semantic_and_vectors` 或 `vectors_only`。 |

`--to`、`--parent`、`--parent-auto-create`、`--args`、`--strict`、`--ignore-dirs`、
`--include` 和 `--exclude` 属于单资源模式，不能与 `--manifest` 一起使用。

`--reason`、`--instruction`、`--no-directly-upload-media`、`--progress`、`--no-progress` 和
`--verbose` 当前不会应用到 Manifest 中的资产，Manifest 模式下不要依赖这些参数。

## 结构化输出

默认输出适合终端阅读。使用 JSON 输出时，Manifest 模式会输出 NDJSON，即每行一个完整的
JSON 事件，而不是一个单独的 JSON 文档：

```bash
ov --output json add-resource \
  --manifest manifests/code-qa.yaml \
  --catalog assets.yaml \
  --dry-run
```

可能出现的事件包括：

- `plan`
- `orphan`
- `asset_planned`
- `asset_start`
- `asset_done`
- `asset_failed`
- `asset_skipped`
- `summary`

自动化程序应逐行解析，并以进程退出码和最终 `summary` 共同判断结果。注意不要假设第一行
一定是 `plan`：存在 orphan 时，`orphan` 事件会先于 `plan` 输出。

## 当前限制

`openviking-assets/1` 当前具有以下边界：

- 只支持 Git 资产；
- Manifest 必须平铺，不支持递归 `include`；
- 服务端 resolver 只返回计划，不执行批量提交；
- CLI 按顺序逐个执行资产；
- 不自动删除 orphan；
- 不包含 `ov share` 指针码或从现有知识库导出 Manifest 的能力；
- State 是本地文件，不在多台机器之间自动同步；
- CLI 和服务端都必须支持同一协议版本。

## 相关文档

- [OpenViking Assets API](../api/22-openviking-assets.md)
- [资源管理 API](../api/02-resources.md)
- [资源 Watch API](../api/15-watches.md)
- [OVPack 导入导出](09-ovpack.md)
- [OpenViking Assets 示例](https://github.com/volcengine/OpenViking/tree/main/examples/openviking-assets)
