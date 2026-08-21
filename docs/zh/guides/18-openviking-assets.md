# OpenViking Assets

> 实验性功能。`openviking-assets/1` 协议和命令行行为仍可能在后续版本中调整。

OpenViking Assets 用声明文件描述“一个知识库应该由哪些资源组成”。最简单的形态是
一个 Manifest 文件直接定义要接入的资产；团队也可以在共享的 Catalog 中维护可接入
资源的全集，再用多个 Manifest 按名称选择不同用途所需的资源。执行 Manifest 时，
OpenViking 会逐项创建或更新资源，并在本地保存资产与 `viking://` 资源之间的映射。

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

- **Manifest**：实际执行的文件。可以在 `catalog:` 下直接定义要接入的资产，也可以按名称
  从单独的 Catalog 文件中选择资产。
- **Catalog**：团队可接入资源的目录，包含来源、分支、默认更新周期和凭据别名。只有在多个
  Manifest 需要共享时才作为单独文件存在，否则直接写在 Manifest 里。
- **State**：某个 Manifest 上次执行的结果，以及资产到 `viking://` 资源的映射。

```text
manifest.yaml（使用共享 Catalog 时再加 catalog.yaml）
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

服务端是协议解析的权威实现。CLI 会把 Manifest 的原始 YAML（使用单独 Catalog 文件时
一并发送 Catalog YAML）发送到当前配置的 OpenViking 服务，由服务端完成严格校验并返回
执行计划；服务端的解析接口本身不会创建资源。

## 协议

### Manifest

Manifest 描述一次知识库构建。最简单的形态下它是唯一需要的文件：在 `catalog:` 下
直接定义资产：

```yaml
protocol: openviking-assets/1

defaults:
  git:
    auth_ref: team-git
    watch_interval: 60

catalog:
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

assets: [openviking]   # 可选：省略 = 执行上面定义的全部资产
```

Manifest 顶层字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `protocol` | 定义 `catalog` 时必填 | 当前必须为 `openviking-assets/1`；只按名称选择资产的 Manifest 可省略，但设置时同样会校验。 |
| `defaults` | 否 | 为本文件定义的资产设置连接器级默认值；只能与 `catalog` 一起使用。 |
| `catalog` | 否 | 资产定义列表（字段见下）。定义了 `catalog` 的 Manifest 自身就是完整配置。 |
| `assets` | 见说明 | 要执行的资产名称。`catalog` 在同一文件中时可省略——省略表示执行全部定义的资产；资产定义在单独 Catalog 文件中时必填。 |
| `include` | 否 | v1 不支持组合其他 Manifest；非空时解析失败。 |

重复的资产名称会按首次出现的位置去重。选择不存在的资产时，整个解析失败。

`defaults.git` 支持：

| 字段 | 说明 |
| --- | --- |
| `auth_ref` | 本地凭据文件中的默认别名。 |
| `watch_interval` | 默认 Watch 周期，单位为分钟；`0` 表示不自动刷新。 |

Git 资产支持：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 唯一资产名称，必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]*`。 |
| `connector` | 是 | v1 只支持 `git`。 |
| `description` | 否 | 资产用途说明。 |
| `params.repo_url` | 是 | Git clone URL。 |
| `params.branch` | 否 | 要接入的分支；设置时不能为空。 |
| `auth_ref` | 否 | 覆盖 `defaults.git.auth_ref`。 |
| `watch_interval` | 否 | 覆盖 `defaults.git.watch_interval`。 |

校验是严格的：未知字段、重复资产名和不支持的连接器都会使整个解析失败，即使有问题的资产
没有被本次执行选择。`params` 内容和 clone URL 安全性针对被选中的资产校验。这些规则与
资产定义所在的位置无关——写在 Manifest 的 `catalog` 里和写在单独的 Catalog 文件里完全相同。

### 多个 Manifest 共享一个 Catalog

当多个 Manifest 复用同一批资源时，把资产定义移到单独的 Catalog 文件中，通常命名为
`catalog.yaml`。Catalog 包含 `protocol`、可选的 `defaults`，以及同样的 `catalog` 块——
一份 Catalog 文件就是一个不做选择的 Manifest：

```yaml
protocol: openviking-assets/1

defaults:
  git:
    auth_ref: team-git
    watch_interval: 60

catalog:
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

每个 Manifest 只需按名称选择：

```yaml
assets:
  - openviking
  - requests
```

全团队维护一份 Catalog；在 Catalog 中修改资产，所有选择它的 Manifest 都会生效。因为两种
文档同构，Catalog 也可以直接执行：`ov add-resource -m catalog.yaml` 会导入它定义的全部资产。

CLI 按以下规则查找 Catalog 文件：

1. 传入 `--args catalog:<file>` 时使用该路径；相对路径基于当前工作目录。
2. 未传入时读取 Manifest 所在目录下的 `catalog.yaml`。

定义了 `catalog` 的 Manifest 不使用单独的 Catalog 文件；同时传入会导致解析失败。

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

### 编写并验证 Manifest

创建 `manifest.yaml`：

```yaml
protocol: openviking-assets/1

catalog:
  - name: openviking
    connector: git
    params:
      repo_url: https://github.com/volcengine/OpenViking
      branch: main
```

先验证：

```bash
ov add-resource --manifest manifest.yaml --args dry_run:true
```

`dry_run` 会完成以下操作：

- 读取本地 YAML 文件（使用单独 Catalog 文件时一并读取）；
- 调用当前 OpenViking 服务解析并校验协议；
- 检查所有 `auth_ref` 是否能在本地解析；
- 让服务端使用最终凭据对每个 Git 仓库执行只读 `git ls-remote` 权限预检；
- 输出每个资产将执行的 create 或 sync 操作；
- 不克隆仓库、不提交资源、不创建任务，也不写入 State。

任何仓库不可读时，dry-run 立即以 `PERMISSION_DENIED` 退出，不再输出可执行计划。

### 应用 Manifest

确认计划后去掉 `dry_run`：

```bash
ov add-resource --manifest manifest.yaml
```

等待每个资源处理完成：

```bash
ov add-resource --manifest manifest.yaml --wait --timeout 600
```

仓库中包含一个完整示例（一份共享 Catalog 加一份按名选择的 Manifest），位于
[`examples/openviking-assets`](https://github.com/volcengine/OpenViking/tree/main/examples/openviking-assets)。

## 凭据

Manifest 和 Catalog 只保存 `auth_ref` 别名，不应保存 token、密码或私钥。CLI 默认从以下
文件解析别名：

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

执行前，CLI 会先解析所有选中资产的 `auth_ref`，然后由服务端在实际执行环境中用
`git ls-remote` 校验每个仓库的读取权限。只要有一个别名不存在或仓库不可读，整个操作都会
在提交任何资源之前失败；`dry_run` 也执行相同预检。原生 Git 凭据别名只支持 `username` 和
`token`，并保持上述扁平结构。使用默认的原生 Git 链路时，CLI 会在调用 `add_resource` 时将它们放入
`args.auth_config`，而 `branch` 或 `commit` 仍留在 `args` 顶层。解析出的 Git 参数会通过
当前配置的 OpenViking 服务连接发送，因此远程部署应使用 TLS，并限制凭据文件的本地访问权限。

当最终 `watch_interval` 大于 `0` 时，OpenViking 会把通过 `auth_ref` 解析出的 HTTPS Git
token 保存到 Watch task 私有且与仓库 URL 绑定的鉴权状态中。token 不会写入 Manifest
State、普通入库队列或 Watch API/MCP/CLI 返回。周期为 `0` 时，token 仍只在本次请求内使用。
Git PAT 没有通用刷新流程，token 过期或被撤销后需要重建 Watch。

Watch 私有状态保存在 `viking://resources/.watch_tasks.json`。启用 VikingFS 文件加密时会
静态加密；否则服务端控制文件及其备份包含明文 token 状态。生产环境应限制服务端存储访问并
启用加密。

即使没有指定 `--wait`，原生凭据导入也需要等 clone 和 parse 完成后，服务端才会返回 task；
因此 CLI 对这类资产默认使用 300 秒请求超时，大仓库可通过 `--timeout <秒>` 调大。token
会放在 HTTPS 请求体中传输，生产环境应保持诊断请求体 dump 关闭。

如果目标服务已经具备访问仓库所需的 SSH key 或其他认证配置，可以不设置 `auth_ref`。

## Create、Sync 和 State

非 dry-run 执行后，CLI 在 Manifest 旁写入：

```text
<manifest-file>.state.json
```

例如：

```text
manifest.yaml.state.json
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
ov add-resource --manifest manifest.yaml --watch-interval 60
```

后续内容刷新由 Watch 执行。原生 HTTPS Git 资产使用 `auth_ref` 时，服务端会在每次刷新时
从 Watch 私有状态恢复与仓库绑定的 token。重新运行 Manifest 仍可用于应用 Catalog/Manifest
构成变化、恢复失败资产或显式触发同步。

## 失败处理

权限预检先于所有资源提交。任一资产预检失败时：

1. 命令立即以原始错误码退出，例如 `PERMISSION_DENIED`；
2. 不提交任何资产，不创建后台任务；
3. 不写入 State；
4. `skip_failed` 不会跳过预检失败。

只有全部预检成功后，才进入以下逐资产执行阶段。

默认采用 fail-fast：

1. 当前资产失败；
2. 后续资产标记为未尝试；
3. 已成功资产和失败记录写入 State；
4. 命令以非零状态退出。

使用 `skip_failed` 可以继续处理其余资产：

```bash
ov add-resource --manifest manifest.yaml --args skip_failed:true
```

`skip_failed` 不会把部分失败转换为成功。只要有资产失败，命令最终仍以非零状态退出；
已经成功的资源不会回滚。全部资产失败时，命令会报告没有任何资产成功应用。

## 命令行选项

与 `--manifest` 搭配使用的参数：

| 参数 | 说明 |
| --- | --- |
| `-m, --manifest <file>` | Manifest 文件。 |
| `--args <key:value,...>` | Manifest 运行选项，多个选项用逗号分隔，支持的键见下表。 |
| `--wait` | 等待每个资源处理完成。 |
| `--timeout <seconds>` | HTTP 请求超时。原生私有 Git 即使没有 `--wait` 也会使用该值，默认 300 秒。 |
| `--watch-interval <minutes>` | 覆盖全部资产的更新周期。 |

`--args` 支持的运行选项：

| 键 | 说明 |
| --- | --- |
| `catalog:<file>` | 按名称选择资产的 Manifest 使用的单独 Catalog 文件；省略时使用 Manifest 同目录的 `catalog.yaml`。Manifest 自身定义了 `catalog` 时不使用。 |
| `dry_run:true` | 解析协议并校验所有仓库的读取权限；不提交资源、不创建任务、不写 State。 |
| `skip_failed:true` | 一个资产失败后继续处理其他资产。 |

`--args` 既支持 `key:value,...` 逗号分隔形式，也支持整段 JSON 对象，例如
`--args '{"dry_run": true, "catalog": "shared/catalog.yaml"}'`。

运行选项由 CLI 在本地消费，不会作为资源参数发送给服务端；未知的键会直接报错。

## 当前限制

`openviking-assets/1` 当前具有以下边界：

- 只支持 Git 资产；
- Manifest 必须平铺，不支持递归 `include`；
- 服务端 resolver 只返回计划，不执行批量提交；
- 服务端 preflight 通过只读 `git ls-remote` 校验仓库权限，不下载仓库内容；
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
