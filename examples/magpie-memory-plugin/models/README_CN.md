# OpenViking 模型管理插件（Magpie）

[English](README.md)

在 Magpie 的 **Agents → OpenViking** 中选择模型，让 OpenViking 与 VikingBot 使用 Magpie 的供应商、账号和路由。供应商密钥留在 Magpie，OV 保存网关地址、网关 Key 和完整模型 ID。

这是统一插件 `@openviking/magpie-plugin` 的模型管理模块，不再作为独立插件安装。安装和迁移见 [统一插件说明](../README_CN.md)。本文的 `node cli.mjs ...` 在 `models/` 目录执行。已用官方 Linux Magpie 0.1.3528 完成隔离模型切换、自动重启和恢复验证，Windows 尚未验证。

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
| `queryMode: "preserve"`（默认） | 保留 Query Planner 原配置 |
| `queryMode: "follow"` | 移除可选的 `query_planner` 配置，恢复 OV 原生的 VLM 回退；需要开启 `manageVlm` |
| `queryMode: "independent"` | 给 Query Planner 指定独立的 Magpie 模型连接 |
| `queryModel` | 独立模式下的完整 `provider/model` 或 `group/name`；留空使用 Agent 选择的模型 |
| `botMode: "preserve"`（默认） | 保留 Bot 现有配置；原本继承 VLM 的 Bot 会在重新加载后继续继承 |
| `botMode: "follow"` | 删除 Bot 显式模型和凭据绑定，恢复继承完整根 VLM；需要开启 `manageVlm` |
| `botMode: "independent"` | 给 Bot 写入独立的 Magpie 连接；默认使用 Agents 中选择的模型 |
| `botModel` | 独立模式下，可指定另外一个完整的 `provider/model` 或 `group/name` |
| `manageVlm: false` | 保留根 VLM；仍可独立管理 Query 和／或 Bot |
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

### 分别选择 VLM、Query 与 Bot 模型

VLM 继续在 **Agents → OpenViking** 选择。在 **Plugins → OpenViking → Options** 中将以下字段合并到现有选项，保留原有连接配置和功能开关：

```json
{
  "queryMode": "independent",
  "queryModel": "your-provider/your-query-model",
  "queryParameters": { "max_tokens": 2048, "timeout": 90 },
  "botMode": "preserve"
}
```

模型 ID 须来自 Magpie 模型目录，也可以填写路由组 `group/name`。`queryMode: "preserve"` 会释放 Query 接管并恢复原配置；`"follow"` 移除整个可选配置，继承 VLM 模型及生成参数。`queryParameters` 仅在独立模式生效。Query 配置段整体保存恢复记录：接管期间手动修改其中任意字段都会阻止恢复，不会覆盖用户修改。原本为 `null` 或不存在的配置段也会准确还原。默认不启用 Bot，不改变 Query。

仅修改 Query 同样通过现有生命周期重启并验证 OV。账号覆盖不在管理范围：账号显式 Query 配置可覆盖启动配置；账号未配置 Query、但有自己的 VLM 时，账号 VLM 也可能优先于服务端 Query。不能仅凭启动文件判断所有账号的实际模型。

### 输出预算和生成参数

未指定的模型参数沿用现有配置；输出预算仍按所选模型的已知上限校验。

Options / `adapter.json` 中对应 `vlmParameters`、`queryParameters`（仅独立 Query）与 `botParameters`，使用统一的 snake_case 字段：

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

Embedding、Rerank、Bot 渠道、工具、生图模型等保持原配置。Query 默认保持不变，显式设置后才接管。本版本只管理启动文件；遇到受管 VLM 或 Query 的 Cluster 运行覆盖时停止接管，不会声称文件修改已影响实际模型。Account 模型覆盖不在本插件管理范围内。

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
- `sync`：解除暂停并立即同步。CLI 与后台同步使用同一套配置解析，自动读取 Magpie 中本插件的 Options（包括已关闭的插件条目），无需重复配置凭据。

`~/.config/magpie-openviking-models/state.json` 保存字段原值、待写值和恢复目标，可能包含原有凭据。目录权限 `0700`，选择、状态和恢复文件 `0600`。不要分享或提前删除恢复文件。状态中的 `recoveryPending` 表示仍保留配置所有权／恢复记录，并不一定表示同步失败。

恢复 CLI 默认读取 `$XDG_CONFIG_HOME/magpie/plugins.json`（XDG 路径须为绝对路径）或 `~/.config/magpie/plugins.json`；便携模式或自定义配置目录可在动作后加 `--magpie-config /path/to/magpie-config`。找不到插件条目时回退到 `adapter.json` 和所选 OV 客户端配置，存在多个匹配条目时拒绝猜测。

全局 `plugin.enabled: false` 会在下次轮询暂停模型自动同步及自动重启，显式执行恢复 CLI 不受此开关限制。

配置通过服务端版本号防止覆盖并发修改。若服务端已有插件之外的待重启配置，插件返回 `external_restart_required`，暂停写入与自动重启并保留恢复记录；先通过原管理方式应用或撤销这些改动，再继续模型同步。手动更改插件管理的字段后，进入 `conflict`，不会强行覆盖；状态只列字段名。先将冲突字段改回插件最后写入的值，再执行恢复；若决定保留手动配置，请先备份恢复记录并人工核对。切换 OV 服务地址或服务器配置文件位置时，必须先在原目标上恢复。

## 与记忆功能一起使用

OV 和 Bot 继续使用 `openviking-models` 网关身份，统一插件的记忆层将其识别为原生流量，跳过召回和采集，避免递归处理自己的模型请求。

## 开发验证

```sh
cd .. # 统一插件目录
npm run check
npm test
npm pack --dry-run
MAGPIE_BIN=/path/to/magpie node models/tests/magpie-smoke.mjs
```

可通过 `MAGPIE_BUN` 指定已有的 Bun 可执行文件，离线验证原版 Magpie。

宿主测试使用临时 HOME/XDG 目录、模拟 OV 与模型供应商，不读取真实账号、不修改实际配置，也不发起模型推理。`tests/ov-contract.py` 可在仓库 Python 环境中读取标准输入的临时配置，检查真实 OV 与 Bot 配置解析。



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
| `runtime_query_override` | 存在 Cluster Query Planner 运行覆盖；先明确实际配置来源 |
| `runtime_vlm_override` | 存在 Cluster VLM 运行覆盖；先明确实际配置来源，不能仅靠修改启动文件判断生效 |
| `adapter_locked` | 其他同步或重启正在持锁。若进程在过期锁回收期间崩溃，先停用适配器及 CLI，检查 `adapter.lock.reclaim` 中的 PID；确认该进程已退出后才移除回收锁，保留恢复记录 |
| `server_restart_unsupported` | 当前启动方式不支持重启 API，使用原来的 systemd / Docker / 进程管理方式重启 |
| `saved` 后模型看起来没变化 | 检查 `restartRequired` 并完成重启；确认没有 Account 覆盖，也没有连接到另一台 OV |
| 界面显示已接入，但仍不确定是否生效 | 查看插件日志，运行 `node cli.mjs doctor` 和 `node cli.mjs status`；不要把 Magpie 的接入标记当作最终状态 |

## 无重启接口的范围

`PATCH /api/v1/admin/accounts/{account_id}/configuration` 可热更新 Account VLM，后续模型调用使用新实例；这不等于热更新根启动配置，也不会更新已启动 VikingBot 的模型实例。当前插件管理根 `ov.conf`，因此采用保存后重启；不会悄悄给某个 Account 添加覆盖来代替根配置修改。

## 安装与重启能力检测

Agent 行通过 `openviking-server` 命令或 OV 配置目录发现安装。`node cli.mjs doctor` 分别报告 PATH 中是否有服务 CLI、当前连接能否通过 Root 配置鉴权、服务是否支持重启 API，以及 Bot 是否已启用。本机没有服务 CLI 不影响连接远端 OV。

当前 `openviking-server` 提供启动参数，没有 `restart` 子命令；再次执行会尝试启动另一个服务。因此插件优先调用当前实例公开的重启 API，保留原 Python 环境、CLI 参数和 `--with-bot` 状态。实例不支持该 API 时明确报告不支持，交由原来的 systemd / Docker / 进程管理器重启，不猜测进程并杀掉，也不会新增 Bot 启动参数。
