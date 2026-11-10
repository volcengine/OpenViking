# OpenViking 模型管理插件（Magpie）

[English](README.md)

在 Magpie 的 **Agents → OpenViking** 中选择模型，让 OpenViking 与 VikingBot 使用 Magpie 的供应商、账号和路由。供应商密钥留在 Magpie，OV 保存网关地址、网关 Key 和完整模型 ID。

展示名称为 **OpenViking**，npm 包名为 `@openviking/magpie-models-plugin`。

与 [`@openviking/magpie-plugin`](https://github.com/volcengine/OpenViking/blob/main/examples/magpie-memory-plugin/README_CN.md) 的用途不同：记忆插件为其他 Agent 召回、保存 OV 记忆；本插件管理 **OV 和 VikingBot 自己使用的模型**。两个插件可以独立安装。

## 本地安装

本包尚未发布到 npm，请安装完整目录，而不是只添加 `openviking.agent.js`：

```sh
magpie plugin add /absolute/path/to/OpenViking/examples/magpie-models-plugin
```

也可以在 Magpie 的 Plugins → Add plugin 中选择该目录。需要 Magpie Agent 插件、Bun 插件宿主以及 OV 启动配置文件 API。已在 macOS Magpie **0.1.2818** 验证隔离宿主流程，并在 Linux Magpie **0.1.3502** 验证安装与重启；Windows 尚未验证。OV 和 Magpie 都需要保持运行。以下 `node cli.mjs ...` 命令均在插件目录执行。

1. 配置 OV 服务地址和 Root 凭据，见下文。
2. 在 Agents → OpenViking 中选择一个支持所需能力的聊天模型。
3. 插件自动同步启动配置、调用重启 API 并验证新实例生效。
4. 查看 Magpie 插件日志或运行 `node cli.mjs status`：`saved` 表示已保存，`applied` 表示已执行生效检查；确认没有冲突或错误。
5. 未开启自动重启时，执行 `node cli.mjs apply` 或通过原服务管理方式重启。使用 `openviking-server --with-bot` 时，受管 Bot 随 OV 重启。

默认在切换模型后自动重启并验证（`restartOnChange: true`）；也可执行 `node cli.mjs apply` 重试。插件调用现有 Root 重启 API，等待实例 ID 变化、目标文件版本一致且无需再重启，再报告生效；失败或服务回滚会明确报错，不会循环重启。`saved` 表示配置已保存，不能据此认定运行中的进程已经切换。OV VLM 改动需要重启 OV；Bot 自身配置改变或它继承的 VLM 改变，需要重启 Bot。仅管理 Bot 独立配置时，OV 模型不变。状态分别提供 `ovRuntime` 与 `botRuntime`；启动文件匹配不代表独立 Bot 进程已重载，恢复原配置后也保留重启提示。插件不执行付费推理探测，只检查模型目录。使用前验证 VLM 的图片能力，以及 Bot 的流式输出、工具调用和输入预算。

## 连接和管理范围

高级选项可通过 Plugins → Options 或以下本地文件配置。日常使用只需在 Agent 行切换模型。插件 Options 优先于本地文件。

`~/.config/magpie-openviking-models/adapter.json`：

```json
{
  "serverUrl": "http://127.0.0.1:1933",
  "rootApiKeyFile": "~/.config/magpie-openviking-models/root.key",
  "manageVlm": true,
  "botMode": "follow",
  "pollMs": 2000
}
```

`root.key` 中只放 OV Root API Key，文件权限建议设为 `0600`。这是 **OV 管理凭据**，与 Magpie 网关 Key、普通记忆访问 Key 分开。

插件先执行 `ov config show -o json` 发现当前 CLI 连接。该命令输出脱敏 Key，因此只使用其中的地址；Root Key 仍从同一 CLI 配置文件私下读取，不把掩码当作凭据。CLI 不可用时回退到配置文件。未指定地址时，读取 `OPENVIKING_CLI_CONFIG_FILE` 或 `~/.openviking/ovcli.conf` 的 `url`，最后使用 `http://127.0.0.1:1933`。凭据优先读取 `rootApiKeyFile`，其次 `OPENVIKING_ROOT_API_KEY`、`ovcli.conf` 的 `root_api_key`；不会自动把普通 `api_key` 当作 Root。GUI 启动的 Magpie 可能不继承终端环境变量，建议使用本地文件。无认证开发服务可省略 Key。

| 设置 | 行为 |
| --- | --- |
| `manageVlm: true`（默认） | 接管启动文件的 `vlm` 模型服务绑定 |
| `botMode: "preserve"`（默认） | 保留 Bot 现有配置；原本继承 VLM 的 Bot 会在重新加载后继续继承 |
| `botMode: "follow"` | 删除 Bot 显式模型和凭据绑定，恢复继承完整根 VLM；需要开启 `manageVlm` |
| `botMode: "independent"` | 给 Bot 写入独立的 Magpie 连接；默认使用 Agents 中选择的模型 |
| `botModel` | 独立模式下，可指定另外一个完整的 `provider/model` 或 `group/name` |
| `manageVlm: false` + 独立模式 | 只管理 VikingBot |
| `ovcliConfig` | 显式指定用于解析服务地址和 Root 凭据的 `ovcli.conf` |
| `restartOnChange` | 默认 true；切换模型或恢复配置后自动重启并验证新实例 |
| `pollMs` | 同步间隔，500–60000 毫秒；默认 2000 |

独立 Bot 示例：

```json
{
  "manageVlm": true,
  "botMode": "independent",
  "botModel": "your-provider/your-tool-capable-model"
}
```

插件通过 `/bot/v1/health` 检测原本是否启用受管 Bot。未启用或未检测到时不会创建 Bot 配置、修改启动参数或开启 Bot；显式选择 Bot 管理模式会报错。独立 Bot 不在 OV 的重启生命周期中，插件不会为它擅自启动新进程。修改角色范围时，会先恢复不再接管的字段。Bot 的上下文窗口会使用所选模型目录中的已知值；未知时保留原值，需要自行核对。

### 输出预算和生成参数

本插件仅提供 Agent 模型切换和自动重启验证，不提供配置页面或设置面板，不修改 Magpie 宿主。未指定的模型参数沿用现有配置；输出预算仍按所选模型的已知上限校验。

Options / `adapter.json` 中对应 `vlmParameters` 与 `botParameters`，使用统一的 snake_case 字段：

```json
{
  "vlmParameters": { "max_concurrent": 4, "max_tokens": 8192, "temperature": 0.2, "thinking": false, "timeout": 600 },
  "botParameters": { "max_tokens": 4096, "thinking": true, "timeout": 120 }
}
```

省略参数保留原值；`max_tokens: null` 让 OV 使用供应商默认限制。Bot 独立模式下同样使用模型默认值；跟随 OV 时，Bot 的空值继续使用 OV 的输出限制。Bot 的非空输出预算会覆盖继承值，因此两边分别校验。

`vlmParameters.max_concurrent` 可配置 OV 语义处理及 Compile 使用的并发容量，必须为正整数；默认沿用原值，修改后需要重启 OV。它不是网关或 VikingBot 的全局请求并发限制，多条处理路径可能叠加负载。Bot 不支持同名的 `bot.agents.max_concurrent`，插件不会写入这个无效字段。订阅模型可先从 2–4 这样的小并发观察耗时和取消情况，这不是供应商保证的配额。

切换模型时，若目录提供 `max_output_tokens`，插件将请求的 `max_tokens` 限制在其范围内，不会把上下文窗口误当作最大输出。比如原配置 32768、新模型输出上限 8192，会保存为 8192，并在 `adjustments` 列出原因；原主凭据上的输出限制也会保留并校验。换回容量更大的模型时会重新使用原请求预算；断开时恢复原参数。目录缺失上限时不猜测，保留预算并在 `warnings` 提示核对；配置中的环境变量输出预算需要通过参数选项提供明确数值。插件日志也会展示重启要求、预算调整和兼容性提示。

若目录明确标识不支持 temperature，会清除原采样值；用户显式请求不支持的 temperature 则停止同步。`thinking` 保留用户选择并提示验证协议兼容性。重试策略保持原设置；仅在参数选项显式指定时调整 timeout 和 VLM 的 max_concurrent。配置保存仍需相应进程重启后生效。

Embedding、Rerank、显式 Query Planner、Bot 渠道、工具、生图模型等保持原配置。未显式配置的 Query Planner 按 OV 规则跟随 VLM。本版本只管理启动文件；遇到 Cluster VLM 运行覆盖时停止接管，不会声称文件修改已影响实际模型。Account 模型覆盖不在本插件管理范围内。

## 远程和容器

通过 Root 配置 API 操作服务器的真实 `ov.conf`，不会尝试修改客户端电脑上的同名文件。除回环地址外，OV 管理地址和 Magpie 网关均要求 HTTPS，禁止重定向和 URL 中嵌入凭据。

Magpie 写入的网关地址必须能被 **OV / Bot 所在机器** 访问。远端或容器中的 `127.0.0.1` 指向自身；需要在 Magpie 中配置可访问的共享网关地址和对应 Key。只更改 `serverUrl` 不能解决网关可达性。模型目录检查由 Magpie 进程所在机器执行，不代替服务端连通性验证。

## 断开和恢复

**卸载、禁用插件或退出 Magpie 前，先断开 Agent，并等待恢复完成。**

```sh
magpie openviking-models default
```

断开恢复原配置后，同样需要重启 OV 和 Bot。桥接宿主已退出时，自动恢复不能保证完成；可从本目录执行：

```sh
node cli.mjs doctor
node cli.mjs status
node cli.mjs restore
node cli.mjs sync
node cli.mjs apply
```

- `status`：读取本地最后一次同步结果，不做网络请求，也不输出密钥。
- `doctor`：检查本机 CLI、Root 配置访问权限、服务重启能力及 Bot 状态。
- `restore`：恢复仍由插件管理的字段，暂停当前模型选择，防止下一次轮询立即接管。
- `apply`：对已保存的配置执行现有重启 API；Root 鉴权、配置版本与服务重启能力均会检查。`--with-bot` 的受管 Bot 随 OV 重启并检查健康，独立 Bot 仍需通过自己的进程管理器重启。
- `sync`：解除暂停并立即同步。CLI 不读取 Magpie 保存的 Options；恢复时使用相同的 `adapter.json` 和后端凭据。

`~/.config/magpie-openviking-models/state.json` 保存字段原值、待写值和恢复目标，可能包含原有凭据。目录权限 `0700`，选择、状态和恢复文件 `0600`。不要分享或提前删除恢复文件。状态中的 `recoveryPending` 表示仍保留配置所有权／恢复记录，并不一定表示同步失败。

配置通过服务端版本号防止覆盖并发修改。手动更改插件管理的字段后，进入 `conflict`，不会强行覆盖；状态只列字段名。先将冲突字段改回插件最后写入的值，再执行恢复；若决定保留手动配置，请先备份恢复记录并人工核对。切换 OV 服务地址或服务器配置文件位置时，必须先在原目标上恢复。

## 与记忆插件一起使用

OV 和 Bot 使用本插件的专属网关身份 `openviking-models`。本仓库的记忆插件会自动把它识别为原生 OV 流量，跳过召回和采集，避免后台模型调用再次进入自己的记忆处理链。使用旧版记忆插件时，将 `openviking-models` 加入其 `nativeAgents`，或者升级到包含此行为的版本。

## 开发验证

```sh
npm run check
npm test
npm pack --dry-run
MAGPIE_BIN=/path/to/magpie node tests/magpie-smoke.mjs
```

可通过 `MAGPIE_BUN` 指定已有的 Bun 可执行文件，离线验证原版 Magpie。

宿主测试使用临时 HOME/XDG 目录、模拟 OV 与模型供应商，不读取真实账号、不修改实际配置，也不发起模型推理。`tests/ov-contract.py` 可在仓库 Python 环境中读取标准输入的临时配置，检查真实 OV 与 Bot 配置解析。

发布和市场登记尚未执行。市场条目草稿见 `marketplace.json`。


## 配置文件与状态说明

| 文件或状态 | 含义 |
| --- | --- |
| `selection.json` | Magpie 写入的模型选择，由插件桥接程序读取；OpenViking 本身不读取它。包含网关 Key，不要公开分享 |
| `adapter.json` | 插件连接和行为配置；Magpie Options 的同名字段优先 |
| `state.json` | 原配置与恢复记录，可能包含凭据；恢复完成前不要删除 |
| `status.json` | 插件最近一次同步或生效检查结果 |
| `saved.json` | 插件保存目标、完整配置指纹及确认版本；用于限定自动重启范围 |
| `activation.json` | 重启请求与验证记录，用于避免重复重启 |
| `paused.json` | 手动恢复后的暂停标记，防止立刻再次接管 |
| `saved` | 实际服务配置已保存，仍需检查重启状态 |
| `applied` | 插件已完成生效检查；同时核对 `ovRuntime`、`botRuntime`、`restartRequired` |
| `external_restart_required` | 插件之外的配置修改待重启；不会自动应用这些修改 |
| `conflict` / `apply_failed` | 存在手动修改冲突或重启失败，需要处理后再继续 |

以上插件文件默认位于 `~/.config/magpie-openviking-models/`。实际流程为：Magpie 写入 `selection.json`，桥接程序调用 API 更新服务真正使用的 `ov.conf`，再按设置重启并验证。Magpie 行上的“已接入”只表示其接管配置已写入，不代表 OV 已经切换模型。

自动重启仅应用插件自己保存的版本，发起重启前还会再次核对服务器、配置文件及版本归属。外部修改的待重启状态单独提示；保存响应丢失时，通过完整配置指纹确认归属。非独立模式会忽略保留在界面选项中的 Bot 模型 ID；独立模式留空则使用 Agent 选择。

## 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `root_authentication_required` / `root_permission_required` | 核对当前 CLI 配置的 Root Key 或 `rootApiKeyFile`，普通记忆访问 Key 不具备模型管理权限 |
| `bot_not_enabled_or_detected` | Bot 未启用或未检测到；选择保留 Bot 配置，或先按原部署方式启用 Bot |
| `runtime_vlm_override` | 存在 Cluster VLM 运行覆盖；先明确实际配置来源，不能仅靠修改启动文件判断生效 |
| `adapter_locked` | 其他同步或重启正在持锁。若进程在过期锁回收期间崩溃，先停用适配器及 CLI，检查 `adapter.lock.reclaim` 中的 PID；确认该进程已退出后才移除回收锁，保留恢复记录 |
| `server_restart_unsupported` | 当前启动方式不支持重启 API，使用原来的 systemd / Docker / 进程管理方式重启 |
| `saved` 后模型看起来没变化 | 检查 `restartRequired` 并完成重启；确认没有 Account 覆盖，也没有连接到另一台 OV |
| 界面显示已接入，但仍不确定是否生效 | 查看插件日志，运行 `node cli.mjs doctor` 和 `node cli.mjs status`；不要把 Magpie 的接入标记当作最终状态 |

## 市场信息

[marketplace.json](marketplace.json) 包含 **OpenViking** 的官方图标和中英文简介。`kind: "agent"` 表示它属于模型接入的 Agent 分类，与记忆插件的 `middleware` 分类不同。`package.json` 的 `magpie.icon` 用于 npm 发现和已安装列表。

npm 发布不会自动加入社区推荐列表。完成发布后，需要另行将条目提交到 [Magpie 社区注册表](https://github.com/magpie-community/plugins/blob/main/registry.json)。当前条目不声明由 Magpie 社区维护。

宿主插件接口、安装与加载说明见 [Magpie 官方插件文档](https://usemagpie.ai/docs/plugins)。

## 无重启接口的范围

`PATCH /api/v1/admin/accounts/{account_id}/configuration` 可热更新 Account VLM，后续模型调用使用新实例；这不等于热更新根启动配置，也不会更新已启动 VikingBot 的模型实例。当前插件管理根 `ov.conf`，因此采用保存后重启；不会悄悄给某个 Account 添加覆盖来代替根配置修改。

## 安装与重启能力检测

Agent 行通过 `openviking-server` 命令或 OV 配置目录发现安装。`node cli.mjs doctor` 分别报告 PATH 中是否有服务 CLI、当前连接能否通过 Root 配置鉴权、服务是否支持重启 API，以及 Bot 是否已启用。本机没有服务 CLI 不影响连接远端 OV。

当前 `openviking-server` 提供启动参数，没有 `restart` 子命令；再次执行会尝试启动另一个服务。因此插件优先调用当前实例公开的重启 API，保留原 Python 环境、CLI 参数和 `--with-bot` 状态。实例不支持该 API 时明确报告不支持，交由原来的 systemd / Docker / 进程管理器重启，不猜测进程并杀掉，也不会新增 Bot 启动参数。
