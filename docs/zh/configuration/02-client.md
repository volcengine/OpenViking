# ovcli 配置

`ovcli.conf` 是 `ov` CLI 的客户端配置文件，用于保存服务端连接、鉴权身份和命令默认行为。

Codex、Claude Code、OpenCode 等 Agent 插件还会读取各自的 `OPENVIKING_*` 环境变量，用于控制 Recall、Capture、调试等行为；这些不属于 `ovcli.conf`，请在对应的 [Agent 集成](../agent-integrations/01-overview.md)文档中配置。

建议使用 `ov config` 创建和维护配置；使用 `ov config show` 查看脱敏后的当前配置。

默认路径：

```text
~/.openviking/ovcli.conf
```

也可以指定其他文件：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

## 完整示例

```json
{
  "url": "https://openviking.example.com",
  "api_key": "<user-or-admin-key>",
  "root_api_key": "<root-key>",
  "account": "acme",
  "user": "alice",
  "actor_peer_id": "agent:research-assistant",
  "timeout": 60,
  "output": "table",
  "echo_command": true,
  "show_progress": false,
  "verbose": false,
  "profile": false,
  "upload": {
    "ignore_dirs": "node_modules,.cache,dist",
    "include": "*.md,*.pdf",
    "exclude": "*.tmp,*.log"
  },
  "extra_headers": {
    "X-Tenant": "acme"
  },
  "gateway_token": "<gateway-token>"
}
```

不需要的字段可以省略。本地 `dev` 模式通常只需要 `url`。

## 连接与鉴权

```json
{
  "url": "https://openviking.example.com",
  "api_key": "<user-or-admin-key>",
  "root_api_key": "<root-key>",
  "account": "acme",
  "user": "alice",
  "actor_peer_id": "agent:research-assistant",
  "extra_headers": {
    "X-Tenant": "acme"
  },
  "gateway_token": "<gateway-token>"
}
```

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `url` | HTTP(S) URL | `http://127.0.0.1:1933` | OpenViking 服务端地址 |
| `api_key` | string / `null` | `null` | 普通数据操作使用的 user/admin key |
| `root_api_key` | string / `null` | `null` | `ov --sudo` 管理操作使用的 root key |
| `account` | string / `null` | `null` | trusted 模式或 root-key-only 配置使用的账号身份 |
| `user` | string / `null` | `null` | trusted 模式或 root-key-only 配置使用的用户身份 |
| `actor_peer_id` | string / `null` | `null` | 默认 Actor Peer 标识 |
| `agent_id` | string / `null` | `null` | 兼容字段；新配置使用 `actor_peer_id`，两者不能同时设置 |
| `extra_headers` | object / `null` | `null` | 每个 HTTP 请求附加的自定义请求头；`extra_header` 是兼容别名 |
| `gateway_token` | string / `null` | `null` | 网关挑战重试时使用的 `X-Gateway-Token` |

### API Key 选择

| 配置方式 | 普通命令 | `ov --sudo` |
|---|---|---|
| 仅 `api_key` | 使用 user/admin key | 不可用 |
| 仅 `root_api_key`，并配置 `account`、`user` | 使用 root key 和显式身份 | 使用 root key |
| 同时配置两种 key | 使用 `api_key` | 使用 `root_api_key` |
| 两种 key 都不配置 | 仅适用于未开启鉴权的本地服务 | 不可用 |

`ov.conf` 中的 `server.root_api_key` 是服务端接受的凭证；CLI 管理该服务端时，`ovcli.conf` 中的 `root_api_key` 需要与其一致。

## 命令行为

```json
{
  "timeout": 120,
  "echo_command": true,
  "show_progress": true,
  "verbose": false,
  "profile": false
}
```

| 字段 | 类型 / 可选值 | 默认值 | 作用 |
|---|---|---|---|
| `timeout` | number，秒，`> 0` | `60` | HTTP 请求超时 |
| `echo_command` | boolean | `true` | 是否显示 `find`、`search`、`ls` 等命令的实际请求参数 |
| `show_progress` | boolean | `false` | 上传时是否默认显示进度 |
| `verbose` | boolean | `false` | 上传时是否默认输出诊断信息 |
| `profile` | boolean | `false` | 是否请求性能 profile；服务端还需启用 `server.profile_enabled` |
| `output` | `"table"` / `"json"` | `"table"` | 兼容字段；当前命令使用 `-o table` 或 `-o json` 选择输出格式 |

`--profile`、`--progress`、`--no-progress`、`--verbose` 等命令行参数会覆盖本次命令的配置。

## 上传过滤

```json
{
  "upload": {
    "ignore_dirs": "node_modules,.cache,dist",
    "include": "*.md,*.pdf",
    "exclude": "*.tmp,*.log"
  }
}
```

| 字段 | 类型 / 格式 | 默认值 | 作用 |
|---|---|---|---|
| `upload.ignore_dirs` | 逗号分隔字符串 / `null` | `null` | 忽略的目录名 |
| `upload.include` | 逗号分隔 glob / `null` | `null` | 只上传匹配的文件 |
| `upload.exclude` | 逗号分隔 glob / `null` | `null` | 排除匹配的文件 |

本地目录上传还会遵循 `.gitignore`。命令行 `--include`、`--exclude` 会与配置文件中的规则合并。

## 工作区配置

仓库可以自带插件配置，这样项目的记忆行为跟着代码走，而不是散落在每位协作者的 home 目录里。工作区根目录下有两个文件，另有一层按机器保存：

```text
<repo-root>/.openviking/config.json         # 提交到仓库，团队共享
<repo-root>/.openviking/config.local.json   # 私有，不提交
~/.openviking/workspaces/<slot>.json        # 本机注册表，每个工作区一个文件
```

工作区根目录是向上查找时最先遇到的、包含 `.git` 或包含 `.openviking/config.json`（或 `config.local.json`）的目录；`$HOME` 和文件系统根目录永远不会被当作工作区根。两者都没有的目录不是工作区：没有配置层，没有注册表条目，也没有属于自己的 peer。注册表的槽位名由根目录名加上完整路径的哈希组成，因此同一台机器上同一仓库的两个 clone 不会共用同一条记录。这些层由 Claude Code 和 Codex 插件读取，`ov` 命令不读取。

### 优先级

从高到低：

| 层 | 生效范围 |
|---|---|
| `OPENVIKING_*` 环境变量 | 当前进程 |
| `~/.openviking/workspaces/<slot>.json` | 本机的这个工作区 |
| `<repo-root>/.openviking/config.local.json` | 本地这份 checkout，私有 |
| `<repo-root>/.openviking/config.json` | 整个仓库，随代码提交 |
| `ovcli.conf` `plugin.<harness>` | 本机的单个 harness |
| `ovcli.conf` `plugin` | 本机的所有 harness |
| `ov.conf` harness 段 | 旧部署的兼容层 |
| 内置默认值 | |

标量由高优先级的层直接覆盖低优先级的层；列表在各层之间取并集，首元素为 `"!reset"` 时会丢弃低层贡献的全部条目，即 `["!reset", "*/scratch/*"]` 就是最终列表。

注册表文件没有任何命令会写入。按 `ov-memory-doctor` 打印的路径手工创建即可，写上 `version: 1`，schema 与工作区文件相同。

### Schema

必须写 `version: 1`。声明其他版本的文件会被跳过并给出警告，而不是按猜测解析。

```json
{
  "version": 1,
  "peer": { "source": "git" },
  "recall": { "peer_scope": "actor", "max_items": 20 },
  "capture": { "commit_token_threshold": 20000 },
  "labels": { "team": "search" }
}
```

| 键 | 类型 / 可选值 | 作用 |
|---|---|---|
| `peer.source` | `"git"` / `"cwd"` / `"none"` / 模板 / 模板列表 | 工作区 peer 的推导方式；默认只有 git 仓库才会有 peer |
| `peer.id` | string | 直接指定 peer，优先于 `peer.source` |
| `recall.enabled` | boolean | 是否启用 Recall |
| `recall.peer_scope` | `"all"` / `"actor"` | `all` 会额外扫描该用户的其他 peer 并对命中降分；`actor` 只读用户级记忆和本工作区的 peer |
| `recall.dedup_turns` | integer，`0`–`20` | 与最近多少轮对话去重 |
| `recall.max_items` | integer，`1`–`100` | Recall 结果条数上限 |
| `recall.score_threshold` | number，`0`–`1` | Recall 结果的最低分数 |
| `capture.enabled` | boolean | 是否启用 Capture |
| `capture.commit_token_threshold` | integer，`1000`–`1000000` | 累计多少 token 后提交一次 Capture |
| `bypass.session_patterns` | glob 列表 | 会话 id 或工作目录命中时跳过 Recall 与 Capture |
| `labels` | object | 给人看的自由元数据，插件不读取 |

超出范围的数值会被夹到最近的边界并给出提示；无法识别的枚举值会被忽略。表中之外的键会保留在文件里但不生效。

### 工作区 peer

peer 是用户空间下的一段路径前缀——`viking://user/<you>/peers/<peer>/memories`——把一个项目的记忆归拢在一起。默认只有 git 仓库会有 peer：优先用归一化后的 `origin` URL，其次是仓库根路径。不在 git 仓库中的目录不发送任何 peer，在那里记下的内容进入用户级空间 `viking://user/<you>/memories`。这是有意为之：每个任务新建一个目录的应用，否则会为每个任务铸造一个全新的空 peer。

规则由 `peer.source` 决定。同一项配置在环境变量中写作 `OPENVIKING_PEER_SOURCE`，在 `ovcli.conf` 中写作 `plugin.peerSource` 或 `plugin.<harness>.peerSource`。

#### 让一个目录拥有独立记忆

在该目录下创建 `.openviking/config.json`：

```json
{"version": 1, "peer": {"id": "my-project"}}
```

这个目录及其下的一切从此写入 peer `my-project`，是不是仓库都一样。这个 id 不含路径，因此目录移动、改名、换一台机器都不会变；两个目录写同一个 id 就共享同一份记忆，这正是合并它们的方式。

其余写法，优先级从高到低：

| 写在哪 | 作用 |
|---|---|
| `OPENVIKING_PEER_ID=my-project` | 为单个进程钉住 peer，无视配置文件 |
| `.openviking/config.json` 的 `peer.id` | 指定本工作区的 peer。推荐做法；`config.local.json` 是同一个键，只是不提交 |
| 同一文件的 `peer.source` | 不直接指定，而是推导——`"cwd"` 用目录路径，`"team-{dir}"` 用模板 |
| `ovcli.conf` 的 `plugin.peerSource`，或 `OPENVIKING_PEER_SOURCE` | 对本机所有目录生效；`"cwd"` 可整体恢复 `git` 默认之前的行为 |

| `peer.source` | 含义 |
|---|---|
| `"git"` | 默认值。优先用归一化后的 `origin` URL，其次是仓库根路径，等价于 `["{git_remote}", "{git_root}"]`；不在仓库中则什么都不发送，也不添加任何前缀 |
| `"cwd"` | 把工作目录中所有非字母数字字符替换成 `-`，与旧版本发送的值逐字节一致 |
| `"none"` | 完全不发送 peer；`OPENVIKING_WORKSPACE_PEER=0` 含义相同 |
| 模板 / 模板列表 | 例如 `"git-{git_remote}"` 或 `["{git_remote}", "team-{dir}"]`；按顺序尝试，某个模板的变量为空时落到下一个 |

| 变量 | 取值 | 何时为空 |
|---|---|---|
| `{git_remote}` | 归一化后的 `origin`，形如 `github.com-org-repo` | 不在 git 仓库中，或仓库没有 `origin` |
| `{git_root}` | 仓库根路径，所有非字母数字字符替换成 `-` | 不在 git 仓库中。仓库内某个子目录放了 `.openviking/config.json` 时，它仍然是仓库自己的根，因此标记子目录不会拆散默认 peer |
| `{cwd}` | 工作目录，所有非字母数字字符替换成 `-` | 从不为空——它也不在任何默认链里，裸路径只有在你明确要求时才会成为 peer |
| `{dir}` | 工作区根目录的目录名：仓库根，或放着 `.openviking/config.json` 的那个目录 | 该目录不是工作区 |
| `{harness}` | 当前 agent 的名字（`claude-code`、`codex`、`dsh`、`opencode`、`pi`、`cursor`、`trae`、`trae-cn`、`zcode`） | 从不为空——但 MCP proxy 不参与推导，所以只走 proxy 的读路径解析不出它 |

在 `/Users/x/Dev/OpenViking/examples/codex-memory-plugin` 目录下、`origin` 为 `git@github.com:volcengine/OpenViking.git` 时，peer 是 `github.com-volcengine-openviking`——无论从哪个子目录、哪个 worktree、哪台机器、哪份 clone 得到的都是同一个值。因此同一仓库的所有 clone 共享一个 peer，而 fork 的 `origin` 不同，默认就是独立的 peer。推导过程直接读取仓库文件而不调用 `git`，因此 `PATH` 中没有 `git` 时同样可用；URL 会先归一化，使同一仓库的 ssh 与 https 写法收敛到同一个值，URL 中内嵌的 token 也不会进入 peer id。

#### 按场景选择

| 场景 | 怎么做 |
|---|---|
| 有 `origin` 的仓库 | 什么都不用做。所有 clone、worktree、子目录共用一个 peer |
| Fork | `origin` 不同，默认与上游分开。要合并两边的记忆，就在两边写同一个 `peer.id` |
| 没有 remote 的本地仓库 | 默认用仓库根路径，换台机器就会变。长期项目建议写一个 `peer.id` |
| 长期使用但不是仓库的目录 | 创建 `.openviking/config.json`，写上 `peer.id` |
| monorepo 里某个子项目要单独记忆 | 在子目录放 `config.json`，写 `peer.source: "{git_remote}-{dir}"`。只放标记文件仍会沿用仓库 peer，因为 `{git_remote}` 先解析成功 |
| 一次性任务目录（应用按日期新建的目录、临时解包目录） | 什么都不用做，记忆进入用户级空间 |
| 同一仓库下各个 agent 想各存各的 | `peer.source: "{git_remote}-{harness}"`。默认不这么分——跨 agent 共享一份项目记忆通常才是想要的，所以这一档得自己写 |
| 几个目录共享一份记忆 | 各处写同一个 `peer.id` |
| 不想按项目区分 | `peer.source: "none"`（等同于 `OPENVIKING_WORKSPACE_PEER=0`） |

### 召回隔离

`peer.source` 决定记忆写到哪里，`recall.peer_scope` 决定读回什么。peer 是路径前缀，不是租户边界。同一项配置在 `ovcli.conf` 中写作 `plugin.recallPeerScope`，环境变量为 `OPENVIKING_RECALL_PEER_SCOPE`。

| `recall.peer_scope` | 召回读什么 |
|---|---|
| `"all"`（默认） | 用户级记忆与本工作区 peer 全权重参与，再对该用户的其他 peer 做一次扫描，命中结果按类别降分——服务端 `other_peer_penalty` 默认对 events、entities 为 0.1，对 preferences、experiences、resources、skills 为 0.02。因此其他项目的内容只能垫底 |
| `"actor"` | 只看用户级记忆和本工作区的 peer。插件会额外查询一次此处按 `git` 默认之前的规则推导出的 peer，因此旧版本写下的内容不会丢 |

两档之下用户级记忆都是全权重，这也是"不在仓库中就不发 peer"的代价：一次性任务里学到的东西，之后在每个项目里都会参与召回。需要更强隔离时，给这类目录也写一个自己的 `peer.id`，或整体切到 `"actor"`。

切换到 `git` 默认值不需要迁移，也不会搬动任何数据：写在旧的 cwd 派生 peer 下的记忆原地不动，召回仍然读得到——`"all"` 下靠跨 peer 扫描，`"actor"` 下靠那次额外查询。`peer_scope` 是逐请求参数；服务端版本过旧、不认识它时，插件会记录一次降级并告警，而不是静默地读取全部。

### 工作区文件不能设置的内容

hook 是非交互进程，因此这些文件不经确认即被信任；被拒绝的是结构性的内容：

- 连接与凭证类的键——`url`、`api_key`、`root_api_key`、`account`、`user`、`extra_headers` 等——无论出现在哪一层都会被剥离并给出警告。“数据发往哪个服务端”这个问题始终只看 `ovcli.conf` 和环境变量就能回答。
- 这些文件中不会展开 `${VAR}`。

提交到仓库的文件关掉了什么，采用提示而不是拦截的方式：插件的 `ov-memory-doctor` 会列出每一项工作区级配置的值、来源层，以及它覆盖掉的内容。

`.gitignore` 不能忽略整个 `.openviking/`，否则 `config.json` 永远无法提交。请把规则收窄到解析器的临时目录和私有文件：

```text
.openviking/media/
.openviking/downloads/
.openviking/config.local.json
```

存在整目录忽略规则时，`ov-memory-doctor` 会给出警告。

## 相关环境变量

`ov` CLI 直接使用的环境变量只有少量几个：

| 环境变量 | 作用 |
|---|---|
| `OPENVIKING_CLI_CONFIG_FILE` | 指定要读取的 `ovcli.conf` 路径 |
| `OPENVIKING_UPLOAD_MODE` | 指定临时上传模式：`local` 或 `shared` |

`ov config add` 和 `ov config edit` 的 `--api-key-env <变量名>`、`--root-api-key-env <变量名>` 可以从指定环境变量读取密钥，并写入配置文件。

Agent 插件使用的 `OPENVIKING_AUTO_RECALL`、`OPENVIKING_RECALL_LIMIT`、`OPENVIKING_AUTO_CAPTURE`、`OPENVIKING_DEBUG` 等变量由插件进程读取，不是 `ovcli.conf` 字段。

## 多服务配置

普通 `ov` 命令以及 `ov config show`、`ov config validate` 按以下顺序解析实际配置：

1. 设置 `OPENVIKING_CLI_CONFIG_FILE` 后，该路径具有最高优先级；文件不存在时会直接报错。
2. 未设置该变量时，使用默认 Active 文件：

```text
~/.openviking/ovcli.conf
```

交互式管理器以及 `ov config list`、`switch`、`add`、`edit`、`delete` 始终管理默认配置仓库。该仓库中的命名配置与默认 Active 文件位于同一目录：

```text
~/.openviking/ovcli.conf.<name>
```

例如，一份生产环境配置可以写成：

```json
{
  "url": "https://openviking.example.com",
  "api_key": "<production-api-key>",
  "timeout": 120
}
```

常用命令：

```bash
ov config
ov config list
ov config switch <name>
ov config validate
ov config show
```

`ov config switch <name>` 会把命名配置复制为默认 Active 文件。如果仍设置了 `OPENVIKING_CLI_CONFIG_FILE`，普通 `ov` 命令会继续读取环境变量指定的文件；需要取消该变量后才会使用刚切换的默认配置。新的 `ov` 命令会重新读取实际配置文件；已经运行的 Agent 客户端需要重启后才会读取变更。

交互式配置和 Agent 辅助配置步骤见[OpenViking CLI 配置指南](../getting-started/05-cli-setup.md)。
