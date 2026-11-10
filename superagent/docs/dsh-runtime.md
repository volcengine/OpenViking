# DeepSeek Harness runtime

DSH runtime 用 OpenViking SuperAgent 的常驻会话、历史同步、Agent 工具和投递回执，驱动本机安装的 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) SDK profile。它支持轮次中插话、原地打断、`/new`、`/model` 和 `/effort`；模型路由写在 DSH 自己的设置文档里，密钥值存在同一个 DSH home 的 credential 库里，两者都由 `ovsa dsh model` 维护。`ovsa setup` 会探测它，也可以直接 `/runtime dsh`。

操作上只有三件事：用 `ovsa dsh model` 的菜单加或改一条 route；用 `key set` 给 reference 存值；改完等一个轮次边界（daemon 发现文档变了会自己重启 DSH 进程），急着生效就直接 `ovsa restart <实例>`。看现状用 `list`，文档被写坏了用 `repair`；每条子命令的完整用法由它自己打印。

## 装什么、怎么选中它

- 安装 `dsh`，支持版本为 `>=0.2.2-rc.1 <0.3.0`；Node 至少 24.9。OpenViking SuperAgent 安装包自带的 Node 会放在子进程 PATH 最前面，不受 pm2 PATH 中其他 Node 影响。
- DSH 不随 OpenViking SuperAgent 打包。默认在 PATH 中查找 `dsh`，也可以用 `DSH_BIN` 指定可执行文件——和其他 runtime 的 `*_BIN` 一样，既可以写在 manifest 的 `env` 里，也可以从 shell 导出（shell 优先）。`ovsa setup` 探测时也读 manifest 里的值。`DSH_BIN` 有两种合法写法：裸命令名（如 `DSH_BIN=dsh`，同样走 PATH 查找）或路径（按路径使用）；裸名字不会被当成当前目录下的文件。
- **DSH 的模型设置不写在 manifest 里。** `VIKA_DSH_EFFORT` 只从启动 OpenViking SuperAgent 的进程环境读取；模型和路由只属于 DSH 自己的设置文档，密钥值只属于 DSH 的 credential 库（见下节）。写进 `config.json` 的这类字段会被当作未知项忽略并告警。
- 一次启动（从 spawn 到握手结束）默认最多 30 秒。DSH 从源码树跑时是两次 `node --import tsx`（版本探测一次、进程一次），机器空闲时各约 0.4 秒，机器忙时可能是几十秒；超时会让 daemon 启动失败。机器负载高或同时起多个实例时用 `VIKA_DSH_START_TIMEOUT_MS` 放宽（同样只从进程环境读取）。
- 默认模型是内置的 `deepseek-official/deepseek-flash`，它要的 `DEEPSEEK_API_KEY` 从进程环境（`~/.ovsa/secrets.env` 里的值也会被载入 daemon 进程环境）或实例的 credential 库来：`ovsa dsh model key set DEEPSEEK_API_KEY` 存的值同样能让这条内置 route 列出 model（实测）。操作方自己的路由来自 DSH 的设置文档，用 `ovsa dsh model` 配置；配好凭据后不依赖环境里的 `DEEPSEEK_API_KEY`。`VIKA_DSH_EFFORT` 只设置新实例的初始 effort，取值为 `off`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`，默认 `high`；之后由 `/effort` 与持久化设置决定。
- 隔离的 DSH_HOME 不读用户自己 `~/.dsh` 保存的凭据或路由。`DEEPSEEK_BASE_URL` 不会被 OpenViking SuperAgent 删掉，daemon 环境里已经有它时会原样传给 DSH 子进程（OpenViking SuperAgent 不为它做转发，写进 manifest 的 `env` 会被当成未知项忽略）。工作区 `.env` 仍可能被 DSH 读取，但进程环境优先；`DSH_*`、`DEEPSEEK_BASE_URL`、`PATH` 这类启动变量不能由 `.env` 提供。
- `/runtime dsh` 直接选择。`ovsa setup` 的检查是 `dsh --version` 加一次 `initialize → vika/hello → vika/llm/models → shutdown` 探测，不创建会话，也不调用模型。探测默认使用临时 DSH_HOME，因此看不到实例自己的路由；编辑已有实例时，setup 会用该实例自己的 DSH_HOME 再跑一次同样的握手（启动补丁换成 `ovsa dsh model` 用的控制补丁），所以已配置的实例不会被显示成「没有路由」，见「setup 里没有可用 model 时怎么办」。

## 模型路由存在哪里、字段怎么填

模型配置只有一个事实来源：实例的 DSH 设置文档 `<DSH_HOME>/profiles/sdk/cordis.patch.yml` 里的 `llm-pi-ai` 段。`<DSH_HOME>` 是 `<dataDir>/dsh/home`。这份文档由 DSH 自己的 settings 服务写入；`ovsa dsh model` 通过 bridge 调它，OpenViking SuperAgent 不直接写 YAML（只有两处例外：`repair` 恢复读不了的文档，以及 `ovsa setup` 给还没有这份文档也没有 credential 库的新实例写第一份文档，见「setup 里没有可用 model 时怎么办」）。`config.json` 里没有 provider 或模型清单。

在菜单里添加一个 model 后，写出来的文档是这样（实测 `dsh 0.2.2-rc.1`；路由的 `displayName` 和模型条目的 `name` 默认等于 route id）：

```yaml
- id: llm-pi-ai
  name: "@deepseek-ai/dsh-llm-pi-ai"
  config:
    providers:
      deepseek:
        api: openai-completions
        baseURL: https://api.deepseek.com/v1
        apiKeyEnv: DEEPSEEK_API_KEY
        displayName: deepseek
        models:
          - id: deepseek-chat
            name: deepseek
```

模型条目还有 `input`、`contextWindow`、`maxTokens`、`reasoningEfforts` 这些可选字段（规则见下），向导不问，要设就手工加。对同一条 route 再写一次时，DSH 会把 schema 默认值（`defaultInput`、`defaultContextWindow`、`defaultMaxTokens`、`compat` 等）和模型条目的 `input: []` 一起物化进文档，键顺序也会变，所以文件字节每次都可能不同，内容仍是同一份配置。

字段规则：

- `providers` 的键是路由名，也是 `provider/model` 的前半段；不能包含 `/`。模型 id 可以包含 `/`（如 `deepseek/deepseek-chat`），`provider/model` 在**第一个** `/` 处切分。
- `api` 只能是 `openai-completions`、`openai-responses`、`anthropic-messages`；一条路由只服务一种协议、一个 `baseURL`（http 或 https）。
- `apiKeyEnv` 是凭据**引用名**（`^[A-Za-z_][A-Za-z0-9_]*$`），不是密钥本身。值按顺序解析：`<DSH_HOME>/.credentials.yaml`（`ovsa dsh model key set` 写入）→ DSH 子进程环境里**同名**的变量 → DSH 启动时读到的 `.env` 层。三处都没有时 DSH 在请求时报 `MISSING_CREDENTIAL`，这条 route 在目录里也就没有 model（`list` 会显示 `credential 未配置`）；它**不会**退回 pi-ai 自己的 provider 发现（那是另一套按 provider 固定命名的环境变量，比如 `OPENAI_API_KEY`）；要按 provider 自己的发现解析，就不要写 `apiKeyEnv`。
- `input` 只有 `text` 和 `image` 两种。不写时按路由的 `defaultInput`（DSH 默认只有 `text`）；内置 `deepseek-official` 的模态由 DSH 自己的模型目录决定。工具结果里的图片只有当前模型声明了 `image` 才会交给模型，见「图片什么时候能到模型」。
- `reasoningEfforts` 是模型是否支持推理的声明，`false` 表示非推理模型；不写表示沿用 DSH 内置目录里同名模型的能力（内置目录没有这个模型时即不支持推理）。写字典时键只有 `off`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`，值是该档位发给 provider 的线值字符串。**只有 `off` 允许空值（null）**，其余档位写 null 会被拒绝；某个档位不提供时，正确写法是省略这个键；线值不能是空字符串。空字典 `{}`、无值的 `reasoningEfforts:`，以及只声明了 `off` 的字典都会被拒绝（最后一种应写 `false`）。字段名是 `reasoningEfforts`，不是数组；模型条目里没有 `reasoning` 字段（bridge `vika/llm/model/info` 的返回里另有一个 `reasoning` 对象，描述该模型实际提供的档位）。
- 不要在 home patch（`<DSH_HOME>/cordis.patch.yml`）或启动 overlay（`--patch` 文件）里写 `llm-pi-ai`：DSH 会报 `overridden by a home patch or command-line overlay`，`ovsa dsh model` 的写入会被拒绝（错误码 `SETTINGS_OVERRIDDEN`）。OpenViking SuperAgent 自己生成的 `<dataDir>/dsh/run/patch.yml` 已不含任何 provider 段。
- 文档被手工改坏后 DSH 不会自愈：`llm-pi-ai` 不再激活，`describe` 拿不到可写的配置项。daemon 在握手时记一条 `dsh_model_settings_invalid` 告警，内置路由照常可用，不阻塞启动；修复用 `ovsa dsh model repair`（它只在文档确实读不了时才改写，语义见「其它子命令各做什么」里的 `repair`）。

从旧版本升级：早期 OpenViking SuperAgent 用实例目录下的 `models.json` 描述路由，这个通道已经删除。`ovsa dsh model import <models.json>` 只用于一次性迁移旧文件，迁移后模型配置就只存在于上面这份 DSH 设置文档里。

**一条 route 都没有时**：DSH 用内置的 `deepseek-official/deepseek-flash` 启动（`initialize` 不需要密钥，进程起得来），但这条内置 route 在没有 `DEEPSEEK_API_KEY` 时一个 model 也不服务——`/model` 是空的，第一轮对话以 `MISSING_CREDENTIAL` 失败。manifest 只写 `dsh`（没写具体 model）的实例，启动时会看一眼 DSH 自己的目录：内置 route 服务不了 model、而目录里有能服务的 route 时，daemon 采纳那个 route 并记为这个实例的 model（只在这一步、且从来没存过选择时写盘）。manifest 指定了具体 model 的实例不做这个替换。缺 route 时先看 `ovsa dsh model list`，再按下一节加一条。

## 怎么添加或修改一个 model

在终端里直接敲 `ovsa dsh model`，先打印这个实例的 route 和 model，然后是一个菜单（`--instance` 指定实例；有多个实例又没写时会问一次）。添加和修改都只走这个菜单；菜单里的「添加 model」和 `ovsa setup` 里那段是同一个模块，问的问题、验证方式和写入路径相同。

```text
要做什么
  添加 model
  修改 model
  删除 model 或 route
  设置 credential 的值
  删除 credential
  切换实例
  退出
```

菜单行就是这些短标签，右侧没有解释文字：每一行都是一个动作，而它要操作的列表就在菜单上方。两个例外：`切换实例` 只在有多个实例时出现；修改、删除和两个 credential 动作的**选择列表**会给一条短提示——route/model 的显示名（和 id 不同时），以及 reference 属于哪个 route——那是标签本身给不出的信息。菜单里没有单独的「查看列表」动作，因为列表本来就在上面。

只有动作真的改动了什么，才重新读一次 DSH 的设置并重画列表；没改动（取消、键没填）只重画菜单，`←` 取消时连列表一起从上次读到的内容重画。菜单上方每次写着 `模型设置：实例 <id>（<显示名>）`；切换实例后会重新读那个实例自己的文档，不会把上一个实例的内容留在屏幕上。`←` 在动作里取消这一项、回到菜单，在菜单本身不做任何事；只有在最开始的实例选择处，`←` 表示离开命令。

**添加 model** 走的是和 `ovsa setup` 完全一致的四步：

1. **网关地址**（http(s) 的 base URL，自建网关一般以 `/v1` 结尾）
2. **protocol**，带说明的菜单，默认值按地址推断：
   - `openai-completions`：OpenAI 兼容（`/v1/chat/completions`），绝大多数自建网关都是这个
   - `openai-responses`：OpenAI Responses（`/v1/responses`），Codex 一类的后端
   - `anthropic-messages`：Anthropic Messages（`/v1/messages`），Claude 兼容的网关

   推断规则：主机名带 `anthropic`、或路径以 `/anthropic`（可带 `/v1`）结尾 → Anthropic Messages；`api.openai.com`、`api.x.ai`、或路径里有一段是 `responses`/`codex` → OpenAI Responses；其他一律 OpenAI 兼容。所以直接回车就是对的。
3. **model id**（这个网关要 DSH 服务的 model，可以带 `/`）
4. **密钥**：现在粘贴（隐藏输入，写进实例的 DSH credential 库），或者只写 reference 稍后用 `key set` 存值

**route id 不再是问题**：它只是 DSH 文档里的本地 provider 键、也是 model id 的前缀，所以直接从网关主机推导（`api.deepseek.com` → `deepseek`，纯 IP → `gateway`），显示名同样默认等于 route id。写入前会打印一份摘要（route、model、protocol、网关、credential），这也是唯一能改 route id 的地方——回答 `no` 就只重新问 route id，其他项重跑一次即可。

**修改 model** 走同一个向导，只是先挑一个条目：选择列表只列这个实例**自己声明过**的 route/model（DSH 自带的 `deepseek-official` 之类不在其中，和删除用的是同一条规则），每项写着 `<route>/<model>` 和它的显示名。选中后向导用这个条目的当前值做默认值（`baseURL`、protocol、model id、credential reference；**不会读取也不会显示密钥值**），改完按 upsert 写回**同一个 route/model**：

- 能改的是网关地址、protocol、model id 和 credential reference；向导不问的字段（`input`、`contextWindow`、`maxTokens`、是否推理）原样保留。
- 在同一个 route 里改 model id 就是**改名**：写入时旧的 id 从这条 route 的 `models` 里去掉，文档里只留新 id，不会变成两条。
- 写成功打印 `已更新 <route>/<model>`，下面逐条列出改了什么（`网关 A → B`、`protocol X → Y`、`model 旧 → 新`、`credential reference 旧 → 新`，以及「credential 的值已更新」——粘贴了新值时它也是改动，即使没有任何字段变）。
- 写入前的摘要回答 `no` 时，修改流程只能改 model id（同一个 route 内的改名）；其他项要重跑一次命令。
- 中途取消（`←` 或选择列表里选「取消」）什么都不写；提交的值和原来一样时也不会写，只提示「没有改动，未写入」。

**同一个网关不会变成两个 route**：route 已经存在且地址相同，就直接更新它（可以继续往里加 model）；route 存在但指向另一个地址，会先说明「现在指向 X，继续会改成 Y」再问是否改过去，回答 `no` 则让你换一个 route id，不会静默改掉另一个 model 的端点。

填完先在一个一次性 DSH_HOME 里验证这个 route 真的能服务该 model，再写进实例自己的 DSH 设置文档；验证不通过会说明临时 DSH 回了什么，并让你决定是否照样保留。

**写入语义**：添加和修改没有单独的子命令，只在菜单里。写入是按 route 的幂等 upsert——未点名的字段保持不变，重复执行不会产生第二条 route 或重复的 model；第二次起 DSH 会把它解析出的默认值一并写回文档，所以文件字节与键顺序会变（见上一节），内容仍是同一份配置。

- 新 route 的 reference 由 route id 派生（大写、非字母数字换成 `_`，再接 `_API_KEY`，例如 `deepseek` → `DEEPSEEK_API_KEY`）。这个派生名只是默认值：它已经被别的 route 解析时写入会被拒绝，让你换一个 reference（不会自动改成 `_2`；那种带序号的名字只出现在 `import` 的分配里）。
- **「添加 model」不读 route 现有的 reference**：它以派生名作为默认值，所以给一条 reference 已被 `import` 轮换成 `FOO_BAR_API_KEY_2` 的 route 再加 model，会把它改回 `FOO_BAR_API_KEY`（值在 `_2` 里的话，这条 route 随后就成了 `credential 未配置`）。这类 route 请走「修改 model」，它以当前 reference 作默认值。同理，手填一个已被别的 route 解析的 reference 会被直接拒绝：两个 route 共用一个 reference 等于让一个带上另一个的密钥。
- model id 接受字母、数字和 `. _ : @ + / [ ] -`，且完整 `<route>/<model>` 不超过 256 个字符（DSH 路径的 `/model` 用的是同一条规则；`+` 只有 DSH 接受，其他 runtime 的规则更窄）。DSH 本身能服务更多字符（空格、`#`、非 ASCII），但 `/model` 是聊天里手打的一行输入，放宽到那一步需要结构化菜单，属于后续单独设计。
- **已有配置里这类 ID 不会被删除或重写**：`list` 原样显示，迁移不提及的 route 逐字保留；用 `/model` 选它会明确说明「这个模型 ID 在当前 Runtime 的目录里，但 OpenViking SuperAgent 的 /model 不接受它」并给出原因（含哪个字符），而不是报成拼写错误。
- **写了值就先把值写进去，再写 route**：粘贴密钥时，reference 的值先落到 credential 库，成功后才提交 route 的 `set`，所以 route 落盘的那一刻它引用的 reference 已经有值。凭据写入被拒时 route 一个字节都不动；settings 写入被拒时，这次刚写的值会被删掉（只在本次调用新建它时删；提交结果不明的失败保留它，因为可能已经落地）。没粘贴值时就只写 reference，成功后提示用 `key set` 补值。
- route 用到了尚未写入的 credential 时，写入后还会提示接着运行 `key set`。

写入成功后会打印 `已写入 <route>/<model>（route …，api=…）`，并提示用 `ovsa restart <实例>` 让正在运行的实例用上新 route（DSH 只在启动时读这份文档）。其实不重启也会生效，只是要等一个边界：daemon 在下一次有输入排队时（或 `/model` 刷新时）比较文档的内容 hash，发现变了就在空闲时自己重启一次，见「什么时候生效、要不要重启」；`ovsa restart` 只是不用等到那一刻。

## 命令作用在哪个实例上、没有终端时怎么办

**实例解析**：除 `import --dry-run`（它不读实例）外的每条子命令，连同菜单和「添加 model」向导，都走同一套规则——`--instance` 指定谁就是谁；只有一个实例就直接用它；有多个实例且在终端里就问一次「配置哪个实例的 model route」；有多个实例又不是终端就报错并列出所有实例。**不会静默落到 `defaultInstance`**（问的时候只是把光标停在它上面）：那会让第二个实例永远配不到，还会显示第一个实例的设置。

不是终端时（管道、脚本、没有 TTY）`ovsa dsh model` 就是 `list` 的输出（只有一个实例，或写了 `--instance` 时），绝不提问；`ovsa dsh model --json` 也算这种「没写子命令」的调用，所以它同样只是那个机器可读的列表。`key set` 是唯一会读标准输入的子命令：管道喂进去就行，`--stdin` 是终端里强制走这条路的写法。

## 其它子命令各做什么

除 `help` 和 `import --dry-run` 外，每次读写（子命令，以及菜单里的读取和每个动作）都会用实例自己的 DSH_HOME 起一个临时的 DSH 控制进程（打包的 `dsh` 几秒，源码树的启动器更慢，读目录还要等模型列表），通过 bridge 读写设置与 credential 后退出；它不改动正在运行的 daemon 进程（设置文档变化后 daemon 会在下一次轮次边界发现并自行重启一次，见「什么时候生效、要不要重启」），也不与 daemon 的运行 patch 混用（临时 patch 在 `<dataDir>/dsh/run/control-patch.yml`）。完整用法以命令自己打印的为准（`ovsa dsh model help`）。

- `list`：列出 route、model、credential 是否就绪，以及 DSH model 目录返回的模态。**它会显示未就绪的 route 和原因**（例如「credential 未配置」或目录查询失败），这正是 `/model` 菜单里看不到某个 route 时该看的地方。`--json` 输出机器可读的数组；此时诊断信息走 stderr，stdout 保持可解析。
- `remove`：删除一个 route 或其中一个 model。删掉最后一个 model 时整个 route 一起删除；credential reference 保留，按提示用 `key unset` 单独删除。
- `import`：把旧版 `models.json`（Pi 词表）一次性翻译成原生 provider profile。写入 protocol 的三条不变量（对应第三轮复审的 P1-a/P1-b/P1-c）：

  1. **绝不写入任何现有 route 正在解析的 credential reference。** 文件里的字面密钥（`apiKey` 或 `auth.json` 里的 key）一律分配**新 reference**：`<ROUTE>_API_KEY`（route id 大写）若既没被任何 route 解析、credential 库里也没有值，否则依次取 `_2`、`_3`…。已存在的 reference 一个字节都不动。
  2. **顺序是「新 reference 写值 → 一次 `mutate` 写入端点与 reference → 成功后才清理本次新建的」。** 因为写的是新 reference，settings 写入失败时现有配置完全不变；本次新建的 reference 会被删掉（它是孤儿），清理失败会点名报出来。只有一种情况**不删**：提交结果不明（超时、连接断、进程中途退出）——那时不猜，报「结果不确定」并保留 reference，让用户先复查。
  3. **计划绑定读取到的 revision。** 撞上 `SETTINGS_CONFLICT` 时重试一次，重试会按新的 settings 重建整份计划（不是重放旧计划）；重建时若发现本次已写入的 reference 已被别的 route 占用，就换一个新 reference 而不改动那个 route。**重试后仍冲突则中止并报错**，不会静默成功。

  **reference 轮换是有意行为，不是缺陷。** 因为不变量 1 不允许复用任何在用 reference，同一份 `models.json` 重复导入必然换到下一个 reference（第一次 `FOO_BAR_API_KEY`，第二次 `_2`，第三次 `_3`）。旧 reference 连同原值都保留，导入结束时逐条点名「route 原 reference → 新 reference」，确认不再需要时用 `ovsa dsh model key unset <reference>` 清理。这样任何一次导入都不会改变未参与本次导入的 route 实际发出的密钥。
  `--dry-run` 只打印将要写入的 `ops` 与迁移注意事项，不需要实例，也不写任何东西；因为不读实例状态，它打印的 reference 是文档自身视图，实际导入会另分配（输出里会说明）。
  迁移遇到这些会说明并处理：DSH 不服务的 protocol（整个 route 或单个 model 跳过）、`headers`（值里可能带 credential，不迁移）、`cost`（不属于 DSH 配置，忽略）、`!命令` 形式的 `apiKey`（不执行命令，route 迁移后没有 credential）。route 没有声明 `api`/`baseUrl` 而 model 条目声明了时，按 model 条目的值迁移，并说明取的是哪个值；同一 route 下的 model 如果指向多个（protocol、端点）组合，会拆成 `route`、`route--2`… 多个 route 并说明拆出了哪几个（DSH 一个 route 只能一种 protocol、一个端点）。既没有 `api` 也没有端点可推的 model 条目才会被跳过。**文件提到了 route 却没有声明 credential 时，保留这个 route 现有的 `apiKeyEnv`**：整个 route `set` 会替换 profile，不写这个字段等于把该 route 从「用已存的密钥」改成「走 DSH 自己的环境变量发现」（通常是第一次请求就 `MISSING_CREDENTIAL`），而且是静默发生的。导入会在输出里点名「保留了 X」；本来就无 reference、或本次新拆出的 `--2` route，按「无 reference」迁移并说明。**model id 逐字节原样迁移**：不做 trim、不改写；首尾空白、内部空格、`#` 等 `/model` 暂时选不了的 ID 会原样写入并出一条 note（DSH 本身能服务这些字符）。同一个 ID 在 `models` 里出现两次时保留第一条并说明（DSH 会因重复 ID 拒收整个 route）。文件里必须有 `providers` 对象。
- `repair`：唯一直接写设置文档的命令，而且**只在拿到「文档不可读」的证据时才改写**。它先用实例自己的 DSH_HOME 起一次控制进程探测 `llm-pi-ai`，结果分三类：

  1. **可写**（`descriptor` 有值）：报告「无需修复」，文档一个字节都不动。
  2. **文档不可读**（唯一的改写依据，两种回答）：`descriptor: null`——DSH 读到了文件，但里面没有可用的 `llm-pi-ai` 条目；或 settings 服务回答 `SETTINGS_INVALID`——服务明确说这份文档读不了。这时才：
     - 把**改写前读到的原始字节**原样备份为同目录的 `<文件名>.bak-<UTC 时间戳>`（0600，已存在时加序号；用 `wx` 打开，不会覆盖并发出现的备份；备份不解析、不重新序列化，写坏的内容照样留底）；
     - 改写前说明这次会丢弃多少个 route（最小文档不含 `providers` 段；结构认不出的文档报字节数）；
     - 原子写入最小文档 `[{"id":"llm-pi-ai"}]`：同目录临时文件 + `fsync` + `rename`，0600；rename 之前再比对一次文档字节，发现修复期间被别的进程改过就中止、不覆盖；
     - 再探测一次验证，通过后报告 route 数与 revision；仍不可写时提示检查实例的 DSH 是否启用了 model 插件。备份或写入本身失败时（例如目录只读）原文档保持不变，不留下半写文件或残留临时文件。
  3. **其它一切情况**：**中止、不改动文档**，退出码非 0，并打印原因与排查建议。包括 `dsh` 跑不起来、控制进程启动后没应答、握手超时或崩溃、bridge 版本过旧（这些根本没拿到 settings 回答，说明不了文档有问题），以及 settings 服务回答 `SETTINGS_NOT_CONFIGURABLE` 或 `SETTINGS_OVERRIDDEN` 时——改这份文件也修不好，要去处理禁用或抢占它的那一层。

  文档是 **YAML 语法错误**时更特殊：DSH 的 loader 在启动阶段解析失败，进程直接退出，根本走不到 settings 服务，因此没有错误帧可分类。repair 同样不改写文档，只在 stderr 里点名这个文件并给出手工修复命令（`cp <文档> <文档>.bak-manual` 后写入 `[{"id":"llm-pi-ai"}]`）。其它形式的启动崩溃（包括顶层不是数组这类结构错误）只给通用建议：先确认 `dsh` 本身能起来，再手工改好或移走这份文档。

  两种「`disabled: true`」结果不同（两种都在 `describe` 上回了 `descriptor: null`）：写在这份 profile 文档里的 `- id: llm-pi-ai` + `disabled: true` 会让 profile 层自己当作没有可写条目，repair 把它重写成最小文档后**能修好**；写在 home patch 里的同一行，repair 会照「不可读」重写 profile 文档，但重写治不了上层禁用，验证阶段仍失败（退出码非 0、备份保留），这时要去删掉 home patch 里那一行。
- `key set`：从标准输入（管道或 `--stdin`）或终端隐藏输入读取密钥值，**不接受命令行参数明文**，回显只有 reference。`key unset` 删除一个 reference（可重复执行）。密钥写入 DSH 的 `<DSH_HOME>/.credentials.yaml`，不改设置文档，因此不会触发 daemon 重启。

## setup 里没有可用 model 时怎么办

`ovsa setup` 选中 DeepSeek Harness 后，会看 DSH 的 `vika/llm/models` 里有没有能服务 model 的 route。一个都没有时（新实例的临时 DSH_HOME 就是这样；内置 `deepseek-official` 没有 `DEEPSEEK_API_KEY` 时也是零 model），setup 不会默默接受：

1. **说明缺什么。** 打印目录里每条 route 各有几个 model（例如 `deepseek-official: 0 models; deepseek-account: 0 models`），并指出这样保存的实例启动后会反复重启（原因见「模型路由存在哪里、字段怎么填」结尾的「一条 route 都没有时」）。
2. **问是否现在添加一个 model route**，字段依次是：网关地址（http/https，含 `/v1`）、protocol（`openai-completions`、`openai-responses`、`anthropic-messages` 三选一，默认按地址推断）、model id（可以带 `/`），最后是 credential——现在粘贴密钥，或者只写 reference（默认由 route id 派生，稍后用 `ovsa dsh model key set` 存值）。route id 不单独问，从网关主机推导（主机是 IP 时用 `gateway`），显示名默认等于 route id。
3. **校验草稿。** 把草稿写进一个一次性 DSH_HOME，重跑同一个握手（`initialize → vika/hello → vika/llm/models → shutdown`）。通过说明「DSH 接受了这个 route，并给出 model <id>」；不通过会说明临时 DSH 的返回，并可以仍然保留或跳过。**校验只覆盖 DSH 这一侧**：它证明配置能被 DSH 解析、credential 能解析，不会真的向网关发一次请求，所以密钥对不对只有第一轮对话才知道。
4. **保存时才写盘。** 草稿只在内存里；选「保存」时（和写 manifest 同一处）才写进 `<dataDir>/dsh/home/profiles/sdk/cordis.patch.yml`（粘贴了密钥时还会写 `<DSH_HOME>/.credentials.yaml`，0600）。取消 setup 不会留下半个实例目录。实例的 DSH home 里**既没有设置文档也没有 credential 库**时（DSH 还没启动过），setup 直接写第一份文档（JSON 也是 YAML，和 `repair` 的最小文档同一写法；实测 DSH 能读、credential 能解析）；两者任何一个已存在就改走 DSH 自己的 settings 服务，按菜单向导的 upsert 语义合并 `providers`，不会覆盖别的 route。写失败会放弃这条草稿、用原来的 `dsh` 保存并打印下面这条命令。

写进 manifest 的 model 是**完整限定**的 `dsh:<route>/<model>`。不写前缀会落到两个不同的坑：裸 `dsh` 解析成内置 `deepseek-official/deepseek-flash`（没有 `DEEPSEEK_API_KEY` 时零 model），裸 `<route>/<model>` 不属于任何 runtime，`resolveRuntimeModelTarget` 会按默认 runtime（`codex`）处理。

编辑已有实例时同理：如果它的 manifest 里是裸 `dsh`，而它自己的 DSH_HOME（用实例目录探测，不是临时目录）能服务某个 route，保存时 setup 会把 manifest 改成那个 route 的限定写法。已经限定过的值（比如 `/model` 选过的）不会被改写。

没有终端（非 TTY）时整步跳过，只打印怎么去菜单里补，不阻塞 setup；选「否」也一样。提示就是一条可以直接粘贴的命令，进去选「添加 model」：

```sh
ovsa dsh model --instance <实例>    # 菜单里选「添加 model」
```

## 什么时候生效、要不要重启

`/model` 的菜单来自 DSH 自己的模型目录（bridge `vika/llm/models`），只列**能真正运行**的模型：没有凭据的路由、未登录的 `deepseek-account`、目录查询失败的路由都不会出现（它们通常一个模型也返回不了）。`ovsa dsh model list` 会把这些路由和原因显式列出来。

切换在**下一轮开始前**的空闲边界生效：daemon 发一次 bridge `vika/session/model/select {sessionId, provider, model, reasoningEffort}`，把请求的选择记录到运行中的会话；**不重启 DSH 进程，也不打断在途轮次**。没有显式选 effort 时不传 `reasoningEffort`（DSH 记录的是请求值，不会把默认档位钉成用户选择）；**保存为「无覆盖」的模型重启用同样语义**——initialize 不带 `reasoningEffort`，由 DSH 用模型自己的默认档位，不会被补成配置默认值再被 DSH 拒绝（那会给模型注入一条「请修复模型配置」的提示）。`VIKA_DSH_EFFORT` 只对「从未选择过」的实例生效。`/effort` 的候选来自 `vika/llm/model/info`，查不到时按 DSH 认识的七档展示，最终由 DSH 校验：不支持的档位以 `UNSUPPORTED_REASONING_EFFORT` 被拒，此时进程继续跑它实际在用的路由与档位，排队输入收到失败通知。**被拒的选择已经写进持久化设置**（`/model`、`/effort` 都在命令返回前就落了记录），所以下次启动会再试一次；为了避免每轮重发，daemon 也记住这个组合：**在用户重新 `/model`、`/effort`、`/new` 或进程重启之前，不会自动再次提交它**（否则每次可用性回调都会把同一个被拒请求重发一遍）。进程重启后新进程会再试一次——恢复会话时的重新选择若仍被拒，下一次轮次边界再试一次，之后重新记为被拒，因为文档可能已经补上了这条路由。

配置文档的内容变化（比较的是设置文档的**内容 hash**，不是 mtime；基线在拉起 DSH 子进程**之前**采样，握手完成时不重采，所以握手期间落盘的写入仍算变化）会在轮次边界或 `/model` 刷新时被发现：空闲时 daemon 重启一次 DSH 进程（`model-settings-changed`），因为 DSH 只在启动时读 profile 文档。`/model` 在给出菜单前会先做这个比较，所以在菜单里加完 model 马上看 `/model` 就能看到新路由；轮次进行中则拒绝重启，菜单先显示正在运行的进程实际可用的模型。凭据文件由 DSH 自己的 watcher 热加载，**不需要重启**。

启动握手时如果请求的 route 起不来：DSH 这一侧先试「去掉 effort 再 initialize 一次」（档位不受支持的那条路），daemon 再拿内置 `deepseek-official/deepseek-flash` 试一次。manifest 只写 `dsh`（没指定具体 model）的实例还允许再降一步：起来的 route 在目录里一个 model 都服务不了时，从 DSH 自己的目录里挑一条能服务的 route 采纳，并在「从来没存过选择」时记成这个实例的 model。所有降级只记日志与事件，不覆盖持久化的用户选择；降级过的那一轮会带一条提示，告诉模型自己实际跑在哪个 route 上。`session/resume` 之后会重新发一次 `select`，因为 resume 会恢复会话自己的选择。`llm-pi-ai` 未能激活不是致命启动失败：DSH 用内置路由照常启动，daemon 记可读告警。

## 实例跑什么进程、用哪些文件

每个实例一个 DSH 子进程，工作目录为实例 workspace。`<dataDir>/dsh` 及其下由 OpenViking SuperAgent 创建的目录（`home`、`agents`、`run`）为 0700，OpenViking SuperAgent 写的 `run/patch.yml`、`run/control-patch.yml` 为 0600。`home` 里面由 DSH 自己创建的东西不由 OpenViking SuperAgent 决定：实测它把 `profiles/`、`profiles/sdk/` 建成 0755，`profiles/sdk/cordis.patch.yml` 初次生成是 0644（那时还只有注释模板，没有路由），settings 服务第一次写入后是 0600。凭据库 `.credentials.yaml` 是 0600。

```text
<dataDir>/dsh/home/     DSH_HOME：会话、附件、缓存，以及 DSH 自己的设置与凭据文档
<dataDir>/dsh/agents/   DSH_AGENTS_HOME：实例自有 skills 与 AGENTS.md
<dataDir>/dsh/run/      daemon 每次启动生成的 patch.yml；ovsa dsh model 用的 control-patch.yml（0600）
```

启动命令是 `dsh --profile sdk --patch <dataDir>/dsh/run/patch.yml`。patch 是 JSON 写出的 YAML 兼容配置：禁用原版 `sdk-jsonrpc-server` 行，换入 OpenViking SuperAgent bridge，挂载 `personal_agent` stdio MCP，并禁用无人值守不需要的行。bridge 通过 DSH CLI 的包依赖解析 SDK server 与消息 API，不依赖 OpenViking SuperAgent 安装额外 DSH 包。除 OpenViking SuperAgent 自有的 `vika/hello`、`session/steer`、`session/cancel` 和 bridge v2 的模型/设置/凭据方法外，请求原样交给原版 SDK transport。

帧格式是 LF 定界的字节流，不是 `readline`：U+2028、U+2029 是合法的 JSON 字符串内容，`readline` 会把一行切成两段而让这一帧解析失败。桥按 0x0A 切帧、逐行 UTF-8 解码（跨 chunk 的多字节字符不会截断）、剥掉行尾 CR、EOF 时补发最后一帧，单帧上限 16 MB（与 daemon 自己的行读取上限同一个数；超限丢该帧并告警，不退出进程）。回归用例 `packages/daemon/test/dsh-bridge.test.mjs` 覆盖 U+2028、U+2029、UTF-8 切分、CRLF、超长行和畸形行。

patch 的分层顺序是 bundle → profile（`<DSH_HOME>/profiles/sdk/cordis.patch.yml`，即模型设置文档）→ home（`<DSH_HOME>/cordis.patch.yml`）→ `--patch` overlay（OpenViking SuperAgent 的 patch 最后应用）。实测（`dsh --profile sdk --patch … --dump-config`）：

- home patch 仍能改 OpenViking SuperAgent 不写的键，例如给 `llm` 设 `requestTimeoutMs`。
- `llm-pi-ai` 只应留在 profile 层；写进 home patch 或 overlay 会让设置服务拒绝写入（`SETTINGS_OVERRIDDEN`），`ovsa dsh model` 随之不可用。
- home patch 也能把某一行整行 `disabled: true`；`disabled` 与 `config` 是不同键，两个都会留下。把 `llm-pi-ai` 整行禁用后模型插件不激活，需要删掉这一行才能继续用 `ovsa dsh model`。
- 引用不存在的 id 会在 stderr 报 `patch: entry "…" not found`，其余层照常应用。

无人值守策略关闭提问、DSH 定时器、插件管理和 Web 交付卡片工具，并关闭 `subagent_fork`：fork 子 agent 继承父会话历史（含 `decision_id`）且不支持独立 preset，是唯一无法被 preset 收敛的委派入口，因此在 patch 里被 `disabled` 掉。`subagent`（spawn）保留。权限模式为 `danger-full-access`，即本机进程没有额外安全边界；服务端若请求审批，OpenViking SuperAgent 一律拒绝并记录。遥测关闭。工作区 AGENTS.md 和 DSH skills 保留，用户的全局 `~/.dsh`、`~/.agents` 不会混入。

当前支持的上限锁定 0.2.x，因为 bridge 使用 SDK 的进程内 Agent API；版本低于 `0.2.2-rc.1` 或不带该 bridge 的 DSH 会在启动/握手时被拒绝。

## 密钥存在哪里

密钥不在设置文档里，也不进 patch。每条路由的 `apiKeyEnv` 只是引用名，值存在 DSH 自己的 `<DSH_HOME>/.credentials.yaml`（0600），由 `ovsa dsh model key set` 通过 DSH 的 credentials 服务写入；`key unset` 删除引用。没存值时 DSH 还会用子进程环境中同名的变量、以及它启动时读到的 `.env` 层（见上面 `apiKeyEnv` 的解析顺序）。`ovsa dsh model import` 迁移旧文件时，能迁移的凭据（`apiKey` 字面值或 `auth.json` 里的 API key）会写入**本次新分配的引用**——导入绝不改写任何已有引用（详见 `import` 条目与下面的「引用轮换」）；`$ENV_NAME` 形式只保留引用名、由环境提供值；`!命令` 和 OAuth 之类的非 API key 凭据不迁移。

- 引用了 `apiKeyEnv`、但三处来源（凭据库、同名环境变量、`.env` 层）都没有值时，DSH 在请求时报 `MISSING_CREDENTIAL`，这条 route 在目录里也就是零 model。它不会退回 pi-ai 自己按 provider 固定命名的发现逻辑——否则可能拿环境里另一个 key 去计费另一个租户。
- 完全没写 `apiKeyEnv` 时，凭据解析交给 pi-ai 自己的 provider 发现（环境变量）。DSH 子进程环境会删掉这些前缀：`ARK_*`、`VOLCENGINE_*`、`VIKA_*`、`LARK_*`、`FEISHU_*`、`LARKSUITE_*`（`sanitizeAgentChildEnv` / `sanitizeNonArkChildEnv`）、`SESSION_ID`（按完整名字）和 `DSH_*`（`sanitizeDshEnv`）。所以 `import` 迁移 `$ARK_API_KEY` 这类引用时只保留引用名并提示：这些名字不可能从 DSH 的环境解析，要用 `ovsa dsh model key set <名字>` 把值存进凭据服务。
- 凭据文件被 DSH 热加载；改凭据不需要重启，也不用重写设置文档。

### 引用轮换

`import` 给每个字面密钥分配新引用，因此**重复导入同一份文件会轮换引用**（`FOO_BAR_API_KEY` → `_2` → `_3`…）。旧引用连同原值保留，导入输出逐条说明「现在用哪个、保留了哪个」；确认不需要后运行 `ovsa dsh model key unset <引用>` 删除。轮换是刻意的：只要复用任何在用引用，一次导入就可能改变别的路由实际发出的密钥，而那正是第三轮复审 P1-a 的缺陷形态。菜单里的「添加 model」是另一种写：它把 route 指向一个 reference（可以顺带写值），对已有 route 是幂等 upsert，不会为了写值另起新引用，所以不轮换——但它用的 reference 是 route id 派生名，不一定是该 route 当前的（见「怎么添加或修改一个 model」的写入语义）。

## 图片什么时候能到模型

规则只有一条，且不做猜测：**当前模型路由声明了 `image` 输入，MCP 工具结果里的图片才交给模型；只声明 `text` 的路由不会收到图片。**

工具结果的图片来自 `personal_agent` 的 MCP 适配器（`packages/daemon/src/actions/mcp.mjs` 用 `packages/daemon/src/inline-images.mjs` 把 `get_message_media` 的附件转成 MCP image block）。DSH 的 MCP 客户端在把结果交给模型前检查当前模型声明的输入模态：没有 `image` 时整块丢弃，换成一行 `[image unavailable: <mime>; …]` 诊断文本，图片本身仍可被程序化调用方取到。

`input` 由谁决定，分两种情况：

- 操作方路由：`input` 就在设置文档里，写什么就是什么。在菜单里添加 model 时向导不问模态，写入的条目不带 `input`，按路由的 `defaultInput`（默认只有 `text`），此时工具图片不会交给模型；需要 `image` 时手工在设置文档的模型条目里加 `input: [text, image]`。
- 内置 `deepseek-official` 条目：`input` 由 DSH 自己的模型目录决定。用 `vika/llm/model/info` 实测（`dsh 0.2.2-rc.1`）：`deepseek-official/deepseek-flash` 声明 `inputModalities: ['text','image']`，**会**收到工具图片；`deepseek-official/deepseek-v4-pro` 只声明 `text`，图片走上面的诊断文本。

需要确定的图片行为时，配一条显式声明 `input` 的路由：内置条目的模态跟随 DSH 版本，可能随升级变化。

另外注意：模型能收到图片，不等于它一定看得懂内容。图片是否被理解取决于网关后面的真实模型；本节的实测只验证了图片有没有被交给模型。

## 会话、插话与崩溃恢复怎么处理

OpenViking SuperAgent session id 就是 DSH session id。`session/working-directory/get` 建立会话，所有实际输入都经 `session/steer`，轮次以 `session/wait` 结算。轮次中的新消息立即 steer，在下一个步骤边界生效；`cancelTurn` 清掉 DSH 未消费 inbox 但保留会话，供死循环检测原地打断。`/new` 先准备新的空会话，再原子提交会话轮换。

每批输入都有显式终态，不会「接受了就当成功」：已 ack 且被模型看见才算完成；ack 了但始终没被看见的输入以 `DSH_INPUT_UNOBSERVED` 判失败并通知，不推进 journal；cancel 主动清掉的那部分单独记 `dropped`，不算一次失败；进程崩溃时，**未 ack** 的输入放回队列并强制新会话与历史前缀（重放不会重复作用于旧会话）；**已 ack** 的输入（无论模型是否看见）一律判失败并通知、永不重放——DSH 已经把它收进自己的会话，重放可能重复工具调用与回复；是否被看见只决定 journal 游标停在什么位置。`session/wait` 有 600 秒的空闲上限，到期只是重新发一次 wait，不会把长的正常轮次判超时。

模型或 effort 在轮次之间变更时，daemon 在下一个轮次边界用 `vika/session/model/select` 应用到运行中的会话，不重启进程；配置文档本身变化（新增路由、改端点）才在空闲时重启一次，见「什么时候生效、要不要重启」。选择被 DSH 拒绝时保留进程实际在用的路由，排队输入收到失败通知。

崩溃后进程按 1 秒起步、30 秒上限退避重连；连续 3 次重连都失败且队列里还有输入时，先按这次的真实错误把这些输入判失败通知出去（不让它们无限等待）；连续 20 次仍没起来就停止自动重连（日志 `dsh_reconnect_exhausted`，队列里剩下的以「dsh reconnect attempts exhausted」判失败）。原会话可恢复时继续 resume；失败则记日志后开新会话。SDK 没有 `session/close`，`/new` 后旧会话在进程存续期间保留。

队列有上限（100 批 / 500 条），`stdin` 写满时等待 drain 而不是无限缓冲；超限的输入收到失败通知而不是静默丢弃。

assistant 文本、reasoning、工具调用、usage 和结束原因写入 OpenViking SuperAgent 活动记录；子 agent 的事件不作为根会话活动。回复始终通过 `personal_agent`，活动页中的 assistant 文本不作为聊天回复。活动行的事件键含每次进程运行的 `runId`，同一 session 重启不会把新行当成重复丢掉。

## 怎么自检、有哪些已知限制

```sh
# 可选：非默认 dsh 路径
DSH_BIN=/path/to/dsh npm run check:dsh
```

`check:dsh` 用不应答的本地 HTTP 端点代替真实模型，覆盖 `vika/hello`（bridge v2 的全部方法）、`settings/describe`、`settings/mutate`、`credentials/set`（含晚就绪的 `NOT_READY` 重试）、`credentials/unset`、`llm/models`（有/无凭据两种）、`llm/model/info`、`session/model/select`（含被拒档位）、initialize、两个 steer、第三个含 U+2028/U+2029 的 steer（S0-1 回归）、运行中 cancel、`turn/end aborted` 与 shutdown，不需要真实 API key。启动预算是 120 秒（`DSH_CHECK_STARTUP_MS` 可调），因为源码树的 tsx 启动器抖动很大：本机同一份代码实测 startup 在 3.7 秒和 20.8 秒都出现过。不要用耗时判断它有没有卡住，看它打印的那行 startup 毫秒数。

DSH 的回归用例是新增的扁平文件，不重组上游既有测试：`packages/daemon/test/dsh-{runtime,runtime-fallback,session,turn,models,events,startup,bridge,control,model-selection,model-command,mcp-readonly,config}.test.mjs` 与 `packages/cli/test/dsh-{model,model-import,model-interactive,probe,detect,setup,setup-model,manifest}.test.mjs`；夹具都在 `packages/daemon/test/` 下：`dsh-agent-fixture.mjs`、`dsh-model-selection-fixture.mjs`、`fake-dsh.mjs`、`fake-provider.mjs`、`real-dsh.mjs`、`cli-fixture.mjs`。真实进程用例 `dsh-live.test.mjs`、`dsh-turn.e2e.test.mjs`、`dsh-model-cli.e2e.test.mjs` 在找不到 `dsh` 时整体跳过。

已知限制（都已按现状核对，不是待办清单）：

- 没有 `/compact` 和 `/goal`：DSH 的 `sdk` profile 本身带这两个命令，但 OpenViking SuperAgent 的本机命令集只处理 `/new`、`/help`、`/status`、`/primary`、`/runtime`、`/model`、`/effort`，不转发未知命令，所以从聊天里用不了。这是 OpenViking SuperAgent 的转发面没覆盖，不是 DSH 缺能力。
- 没有 `session/close`：不只是 OpenViking SuperAgent 没转发——上游 DSH 的 SDK 协议里没有这个方法（请求表只有 `initialize`、`session/prompt`、`session/query`、`session/resume`、`session/wait`、`session/working-directory/get|set`、`shutdown`）。`/new` 之后旧会话在进程存活期间保留。
- 子 agent 的细粒度活动不投影为根会话活动。子 agent 仍能调用 `personal_agent`（同一个 MCP server），这是一个未决的设计问题，本期没有封堵。
- 子 agent 只能用 `subagent`（spawn）；`subagent_fork` 在 patch 里被禁用。
- `ovsa dsh model list` 的凭据状态依赖 DSH 的 credentials 服务：服务晚就绪时那一行显示 `credential 状态未知`，`--json` 里对应 `credential.known: false`，重跑一次即可。模型行后面的 `（目录未返回）` 同理，是模型目录暂时没给出该模型，不是设置文档里没有。
- 明文 HTTP 网关（如内网 `http://`）的链路安全由部署方自己负责；daemon 不做 TLS 强制。
