---
description: 添加和配置 OpenViking 网关转发请求的模型服务地址。
---

# 管理网关上游

本页面向负责添加和修复上游（OpenViking 网关转发请求的模型服务接口）的人：先照步骤添加一个一次就能用的上游，再按 Studio 编辑页的顺序查阅每个字段。在 Studio 中：**上游**标签页。添加、修改和测试上游需要写权限，见[谁能修改什么](06-manage-in-studio.md#who-can-change-what)。

## 什么是上游 {#upstreams}

上游是网关转发请求的一个模型服务接口，只使用一种 API。网关不在 API 之间转换，所以客户端用到几种 API，就要添加几个上游，即使它们来自同一家服务商：

| 协议 | 使用者 |
| --- | --- |
| Anthropic Messages | Claude Code 和 Anthropic SDK |
| Chat Completions | 大多数聊天应用和 SDK |
| Responses | Codex CLI，前提是每个请求都携带完整历史（见[客户端要求](04-openviking-tools.md#client-requirements)） |

上游由整个账号共享，但一个[网关密钥](09-gateway-keys.md#gateway-keys)只能访问签发时选中的上游。

在编辑页选择服务商后，会自动填入它的默认 Base URL：

| 服务商 | 协议 | 默认 Base URL |
| --- | --- | --- |
| 通用 | 三种都有 | 无，自己填写 |
| Anthropic | Anthropic Messages | `https://api.anthropic.com` |
| OpenAI | Chat Completions、Responses | `https://api.openai.com/v1` |
| DeepSeek | 三种都有 | `https://api.deepseek.com`；Anthropic Messages 为 `https://api.deepseek.com/anthropic` |
| 火山方舟 | 三种都有 | Chat Completions 和 Responses 为 `https://ark.cn-beijing.volces.com/api/v3`；Anthropic Messages 为 `https://ark.cn-beijing.volces.com/api/compatible` |
| BytePlus 方舟（海外站） | 三种都有 | Chat Completions 和 Responses 为 `https://ark.ap-southeast.bytepluses.com/api/v3`；Anthropic Messages 为 `https://ark.ap-southeast.bytepluses.com/api/compatible` |

## 部署默认上游 {#deployment-default-upstreams}

运维可以在网关配置中设置 `default_upstreams`，供[直接使用 OpenViking API 密钥](09-gateway-keys.md#openviking-keys)的客户端使用，见[配置示例](13-configuration.md#gateway-fields)。

- 仅在账号没有存储上游时生效。账号一旦存储任何上游，就整组替换部署默认值，即使存储的上游全部已停用。
- 部署默认值不会复制到账号存储中，也不会在 Studio 中显示。签发的网关密钥继续使用它们选定的已存储上游。
- 保持对象的 key（例如 `ark-chat`）稳定：它就是冻结到对话中、供后续轮次路由使用的上游 ID。
- 托管凭证写成 `"api_key": "$ARK_API_KEY"`，Helm 部署通过 `extraEnv` 从 Secret 注入 `ARK_API_KEY`。网关加载文件时展开环境变量引用，不要把明文 API 密钥写进 ConfigMap。

## 添加上游 {#add-an-upstream}

1. 在**上游**标签页点**添加上游**，打开**新建上游**编辑页。页面分为**接口**、**凭证**、**模型**和**路由**四部分。
2. 在**接口**中填写**名称**，选择**服务商**和客户端所用的**协议**。**Base URL** 下方的**请求将发往**应显示服务商接口的完整地址，例如 `https://api.openai.com/v1/chat/completions`。
3. 在**凭证**中保持**由网关保管 API Key**，粘贴服务商的 **API Key**。
4. 在**模型**中列出客户端会请求的模型名，留空则接受任意模型名。窗口小于默认值的模型，要在**上下文窗口**里加一项，见[设置模型的上下文窗口](05-long-conversations.md#context-window)。
5. 点**添加上游**。应看到提示**已添加“<名称>”**，列表里出现新上游。如果编辑页底部显示**还需填写或修正**，按提示修改对应字段。
6. 在新上游这一行点**测试**，再选择模型并点**运行测试**。应先看到**连接可用**，再看到**模型可用**；否则见[测试和删除](#test-and-delete)。
7. [签发一个网关密钥](09-gateway-keys.md#issue-a-key)并选中这个上游。已有的密钥只能访问签发时选中的上游。

## 接口 {#endpoint}

**接口**部分决定网关把请求发到哪里，以及[上游](#upstreams)使用哪种 API。新建的上游默认服务商为*通用*、协议为 *Chat Completions*。

| 字段 | 作用 |
| --- | --- |
| **名称** | 在 Studio 中和签发网关密钥时显示。 |
| **服务商** | 决定可选的协议，以及网关如何适配这家服务商的差异（见下文）。 |
| **协议** | 上游接受哪种 API。Anthropic 上游同时发送 `x-api-key` 和 `Authorization: Bearer`，Chat Completions 和 Responses 上游只发送 Bearer。 |
| **Base URL** | 服务商地址，网关在后面拼接接口路径（见[Base URL 的拼接规则](#base-url)）。 |
| **拼接协议接口路径** | 默认开启。只有 Base URL 已经是完整请求地址时才关闭（见[直接发往完整请求地址](#exact-endpoint)）。 |

### 各服务商的差异 {#provider-differences}

列表里没有的服务商、LiteLLM 和 new-api 这类兼容代理，以及 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 这类把订阅账号包装成 API 的反向代理，都选*通用*，见[自定义上游](#custom-upstreams)。

- *通用*、*Anthropic* 和 *OpenAI* 转发请求的方式相同。
- *DeepSeek*：请求带工具时，DeepSeek 要求历史中之前每条回复都带推理内容，而很多聊天应用不回传它。DeepSeek 上游默认开启[**补全推理内容回传**](#restore-reasoning)，所以请求保持思考模式也能用 OpenViking 工具。关闭这项后，只有请求关闭了思考模式（`"thinking": {"type": "disabled"}`）才提供 OpenViking 工具；标准 Responses 请求没有 `thinking` 字段，这时也得不到工具。记忆照常补充。
- *火山方舟*和 *BytePlus 方舟*行为相同：Chat Completions 和 Responses 的每段对话使用一个固定的 `prompt_cache_key`，方舟的前缀缓存就能跟着对话走。请求的模型、思考模式、采样参数、系统提示词或工具和对话的第一个请求不同时，方舟会重建缓存，请求被标为**缓存参数有变化**（见[降级请求的异常](12-troubleshooting.md#issues-on-degraded-requests)）。

### Base URL 的拼接规则 {#base-url}

Base URL 按服务商 SDK 文档填写，网关在后面拼接协议的接口路径：

| 协议 | 拼接的路径 | 说明 |
| --- | --- | --- |
| Chat Completions | `/chat/completions` | 只填域名时不会自动补 `/v1`，OpenAI 要写 `https://api.openai.com/v1`。 |
| Responses | `/responses` | 同 Chat Completions。 |
| Anthropic Messages | `/v1/messages` | Base URL 不能以 `/v1` 结尾，否则保存时被拒绝。 |

- 末尾的斜杠会被去掉。`/api/paas/v4` 这类带版本路径的地址可以直接使用。
- Base URL 可以带查询参数，例如 `?api-version=…`。客户端的查询参数接在后面，同名参数以 Base URL 里的为准。
- 客户端用方舟路径（`/gateway/api/v3/`、`/gateway/api/compatible/v1/`）发来的请求，按同样的规则拼接。
- 一个上游只有一个 Base URL。同一服务商的不同协议地址不同时，要分别建上游。

编辑页会实时预览最终的请求地址；OpenAI、方舟这类地址漏掉路径，或者 Anthropic 地址多了 `/v1` 时，会给出提示和一键修正。

一些没有预设的服务商的 Base URL，同样按上面的规则拼接：

| 服务 | 协议 | Base URL |
| --- | --- | --- |
| Azure OpenAI（v1 接口） | Chat Completions 或 Responses | `https://RES.openai.azure.com/openai/v1` |
| Microsoft Foundry 上的 Claude | Anthropic Messages | `https://RES.services.ai.azure.com/anthropic` |
| Gemini（OpenAI 兼容接口） | Chat Completions | `https://generativelanguage.googleapis.com/v1beta/openai` |
| 智谱 | Chat Completions | `https://open.bigmodel.cn/api/paas/v4` |
| 智谱 | Responses | `https://open.bigmodel.cn/api/v1` |
| 方舟 Coding Plan | Chat Completions、Responses | `…/api/coding/v3` |
| 方舟 Coding Plan | Anthropic Messages | `…/api/coding` |

### 直接发往完整请求地址 {#exact-endpoint}

如果 Base URL 已经是完整的请求地址，就关闭**拼接协议接口路径**。以 Azure OpenAI 部署为例：

- **协议**选 *Chat Completions*，**服务商**选*通用*
- **Base URL** 填 `https://RES.openai.azure.com/openai/deployments/DEPLOYMENT/chat/completions?api-version=2024-10-21`
- 关闭**拼接协议接口路径**
- **密钥发送方式**选 *api-key 请求头*

关闭后，网关把该协议的请求直接发往这个 URL，不再拼接协议路径。子路径接在后面：Anthropic 的 Token 计数发往 `…/count_tokens`。Embeddings、模型列表等其他接口不会路由到这样的上游，连接测试也会跳过它。

## 凭证 {#credentials}

**凭证**部分决定网关用什么凭证访问服务商。默认由网关保管服务商的 API Key，客户端只需要[网关密钥](09-gateway-keys.md#gateway-keys)。

- **API Key 由谁提供**：
  - **由网关保管 API Key**（默认）：网关发送上游的 API Key。
  - **每个客户端自带 API Key**：每个请求除了网关密钥，还要在 `X-OpenViking-Upstream-Key` 请求头里带上客户端自己的服务商 API Key，缺少时返回 401 "Upstream API key is missing"。网关按**密钥发送方式**转发这个 API Key，Base URL 含 `{api_key}` 时则填进地址；`X-OpenViking-*` 请求头本身从不转发。各客户端怎么加这个请求头，见[客户端自带服务商 API Key 时](03-connect-clients.md#bring-your-own-provider-key)。
- **密钥发送方式**（仅服务商为*通用*时）：`Authorization: Bearer`（默认）、`api-key` 请求头（Azure OpenAI）或 `x-api-key` 请求头。
- **密钥放在地址里**：服务商要求把密钥写在地址里时，在 Base URL 的路径或查询参数中写 `{api_key}`，例如 `?key={api_key}`。网关发送时填入 URL 编码后的密钥，并且不再发送鉴权请求头。不要把密钥本身写进地址：能查看上游的人都能看到 Base URL。
- **API Key**：加密保存，之后不再显示。留空表示保留已存的密钥。
- **额外请求头**加在发往这个上游的每个请求上，例如服务商的版本或组织请求头，并覆盖客户端发送的同名请求头。取值和 API Key 一样只写不读：留空表示保留已存的值，删除这一行就删除这个请求头。不能设置 `Host`、`Content-Length`、`Transfer-Encoding` 和 `Connection`。

> **修改 Base URL 会清除已存的 API Key 和请求头取值。** 每次改地址都要重新填写，已存的密钥不会被发往新地址。

### Coding Plan 和订阅密钥 {#coding-plan-keys}

服务商通常只允许 Coding Plan 这类订阅密钥用在自家的编程工具里，通过共享网关使用可能导致订阅被封禁。网关无法从密钥本身分辨它是不是订阅密钥，需要你自己勾选**这是 Coding Plan 或订阅密钥**来标记。

标记后，发往这个上游的请求一律被拒绝，返回 403 "Coding Plan upstreams are disabled; configure a model API key"，除非同时勾选**允许通过网关使用这个套餐**。请先确认套餐条款允许在服务商自己的工具之外使用，再勾选这一项。

Claude 订阅令牌（`sk-ant-oat…`）无论怎么勾选都会被拒绝，见[使用须知](01-how-it-works.md#what-to-expect)。

## 模型 {#models}

**模型**部分决定[上游](#upstreams)提供哪些模型名，以及它们在服务商那边叫什么。默认列表为空，表示接受任何模型名。

| 字段 | 作用 |
| --- | --- |
| **模型** | 这个上游提供的模型名。只有列出的名称和别名会出现在模型列表（`/gateway/v1/models`）里。 |
| **模型别名** | 把客户端使用的名称映射为发给上游的模型，例如把 Claude Code 请求的 Claude 模型名映射到服务商提供的模型。别名出现在模型列表中，请求日志显示客户端使用的名称。 |
| **上下文窗口** | 按模型记录窗口大小（token，至少 1,024），键是别名转换之后、实际发给上游的模型名。 |

长对话按上下文窗口的比例触发压缩，所以窗口小于默认值的模型都要列出来，见[设置模型的上下文窗口](05-long-conversations.md#context-window)。

## 路由 {#routing}

**路由**部分决定有多个上游可选时网关如何选择。对每个请求，网关在密钥绑定的已启用[上游](#upstreams)中，找出使用这种 API 并提供所请求模型的上游，选**优先级**最高的一个。对话一旦开始，只要原来的上游还能服务就一直用它。

| 字段 | 默认值 | 作用 |
| --- | --- | --- |
| **优先级** | 0 | 数值大的优先。修改只影响新对话，见[修改何时生效](06-manage-in-studio.md#when-changes-take-effect)。 |
| **启用** | 开启 | 停用的上游不接收请求，也不出现在模型列表里。 |
| **允许 OpenViking 工具** | 开启 | 决定能否通过这个上游提供 [OpenViking 工具](04-openviking-tools.md#choose-tools)。关闭后立即生效，已经带工具的对话也受影响。 |
| **补全推理内容回传** | 取决于服务商 | 把客户端丢掉的推理内容放回后续请求（见下文）。 |
| **最小可缓存长度** | 1,024 token | 仅火山方舟和 BytePlus 方舟：模型能缓存的最短提示词长度，只影响请求日志如何报告缓存资格。 |

停用一个上游后，原本使用它的对话会转到下一个候选上游，造成一次服务商缓存未命中，并丢掉之前的 Claude 思考块，这些请求被标为**上游已切换**（见[降级请求的异常](12-troubleshooting.md#issues-on-degraded-requests)）。想保留恢复的余地，就停用而不是删除。

网关不做故障转移：选中的上游出错时，客户端收到这个错误；服务商无法访问时返回 502 "Model upstream is unavailable"。

### 按请求指定上游 {#upstream-url-header}

部署在网关前面的服务可以用 `X-OpenViking-Upstream-URL` 请求头为每个请求选择上游，客户端也可以发送这个请求头。它的值必须与密钥绑定的某个上游的 **Base URL** 相同（没有已存上游的 OpenViking 密钥，对应[部署默认上游](#deployment-default-upstreams)），只忽略末尾的 `/`。这个请求头把路由范围缩小到这些上游，再按常规的 API、模型和**优先级**规则从中选择，所以 Chat Completions 上游和 Responses 上游可以共用一个 Base URL。

- 没有绑定的上游使用这个 Base URL：请求返回 403 "Upstream URL is not allowed"。网关从不向未配置的地址发请求。
- 网关照常使用选中上游的凭证。**每个客户端自带 API Key** 时，API Key 来自 `X-OpenViking-Upstream-Key`；Coding Plan 上游未放行时仍会被拒绝。
- 对话改用另一个 Base URL 时转到那个上游，与停用原上游一样标为**上游已切换**。
- 不带这个请求头时，路由不变。与所有 `X-OpenViking-*` 请求头一样，它不会转发给服务商。

### 补全推理内容回传 {#restore-reasoning}

客户端回传历史时，常常丢掉模型之前回复里的推理内容。**补全推理内容回传**在 DeepSeek、火山方舟和 BytePlus 方舟上默认开启，其他服务商默认关闭。

开启后，网关记下经它转发的每条回复的推理内容，在后续请求中放回原处：

- Chat Completions：补回 `reasoning_content`。
- Anthropic Messages：放回 thinking 块。
- Responses：把带明文内容的 reasoning 项放回对应条目之前。

补回的内容每次逐字节相同，服务商的提示词缓存照常命中，但计入输入 token。客户端自己回传了推理内容的回复保持不变。

有些请求即使开启也补不回推理内容：对话换了上游（**上游已切换**）、Claude 对话丢了记忆记录（**记忆记录缺失**），或者客户端把思考内容当作正文发回。DeepSeek 上游的这类请求同样得不到 OpenViking 工具。

### 所有请求都失败的上游 {#blocked-upstreams}

上游列表会用两个标记提示发往该上游的请求都会失败：

| 标记 | 原因 | 客户端收到 |
| --- | --- | --- |
| **缺少 API Key** | 由网关保管 API Key，却没有保存密钥，例如改过 Base URL 之后。 | 401 "Upstream API key is missing" |
| **请求被拒绝** | [Coding Plan 上游](#coding-plan-keys)未获准通过网关使用。 | 403 |

这样的上游照样参与路由：如果它在匹配的上游中优先级最高，请求会直接失败，不会转到优先级更低的上游。请修好它，或者停用它。

## 测试和删除 {#test-and-delete}

**测试**检查[上游](#upstreams)已保存的接口地址、密钥、模型和协议能否一起正常工作。测试的始终是已保存的设置，改动后要先保存。

1. 在上游列表或上游编辑页的页头点**测试**。网关先检查已保存配置的连接，应看到**连接可用**。完整请求地址没有模型列表可查，会跳过这一步。
2. 选择或输入模型名，点**运行测试**。网关使用已存的凭证发送一次很小的生成请求，应看到**模型可用**。服务商可能对这次请求计费。

模型测试在上游允许 OpenViking 工具时会带上这些工具，reasoning effort 使用模型默认值。各种结果的含义见[上游测试](../api/25-gateway.md#upstream-tests)。

以下情况不能测试：

- 没有写权限，这时**测试**按钮不显示；
- 上游设为**每个客户端自带 API Key**，网关没有可用于测试的密钥；
- 上游还没保存 API Key。

Coding Plan 上游在勾选**允许通过网关使用这个套餐**之前，仍会阻止模型测试。

**删除**会连同保存的 API Key 和请求头一起删掉上游。有密钥在用的上游不能删除：删除对话框会改为提供**停用**，也可以先吊销这些密钥。

## 导入 OpenViking 模型配置 {#import-openviking-model-settings}

导入把 OpenViking 服务端的 VLM 凭证转成当前账号的上游，省去重新填写，只在嵌入部署中可用。在上游页头点**从 OpenViking 导入**；上游列表还是空的时候，这个按钮叫**使用 OpenViking 的模型配置**。没有设置管理密钥时只有 root（包括本地 dev）能导入；设置了管理密钥时需要先[解锁编辑](06-manage-in-studio.md#unlock-editing)。

结果分三组显示：已导入、需要确认、未导入。

- **需要确认**：Base URL 路径中有一段名为 `coding` 或 `plan`（例如 `/api/coding/v3`）的上游会被标为 [Coding Plan](#coding-plan-keys)。确认服务商条款允许后，勾选**允许通过网关使用这个套餐**才能使用。
- **Azure OpenAI** 凭证（服务商 `azure`，或 LiteLLM 的 `azure/<deployment>`）会导入为完整请求地址的上游，带上配置的 `api_version`，密钥放在 `api-key` 请求头里发送。
- **未导入**：Codex OAuth 凭证、缺少 `api_base` 的 LiteLLM 凭证，以及不使用 OpenAI Chat Completions API 的 LiteLLM 路由。原因见[导入 VLM 配置](../api/25-gateway.md#import-vlm-settings)。

重复导入不会产生重复的上游：

| 按服务端配置刷新 | 保留你在网关里的修改 |
| --- | --- |
| 地址、API Key、请求头、协议、服务商、Coding Plan 标记、**拼接协议接口路径**、**密钥发送方式** | 名称、启用状态、优先级、模型、别名、上下文窗口、**允许通过网关使用这个套餐** |

如果你取消了某个导入上游的 Coding Plan 标记，下次导入时会重新标上，在你勾选**允许通过网关使用这个套餐**之前，这个上游的请求都会被拒绝。

## 自定义上游：代理与订阅额度 {#custom-upstreams}

任何兼容这三种 API 之一的服务都能添加为上游，不一定是模型服务商本身。两种常见用法：

- **计费、配额和负载均衡。** 网关不做这些。需要时在网关后面接一层 LiteLLM、new-api 这类服务，把它添加为上游。
- **使用订阅额度。** 客户端不能用订阅账号登录网关，但可以把 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 这类反向代理添加为上游。它把 ChatGPT（Codex）、Claude 等订阅账号包装成 API，例如 Codex 就能经网关使用 ChatGPT 订阅额度。Codex 的这种用法得到了 OpenAI 的认可：OpenAI Codex 负责人 Tibo 曾[公开介绍](https://x.com/thsottiaux/status/2076119366647894371)过用 CLIProxyAPI 接入 Codex 订阅的做法，具体步骤见[使用 ChatGPT 订阅](03-connect-clients.md#codex-chatgpt-subscription)。**其他订阅服务能否这样使用，请自行确认其使用条款。**

添加时服务商选*通用*，然后按[添加上游](#add-an-upstream)操作。

每个字段的 API 名称、默认值和取值范围见[上游设置](13-configuration.md#upstream-settings)。

## 下一步 {#next-steps}

- [签发网关密钥](09-gateway-keys.md#issue-a-key)，选中你的上游。
- [接入客户端](03-connect-clients.md#connect-clients)。
- [创建上下文配置](08-context-profiles.md#context-profiles)，设置召回和保存。
- 请求失败时，见[上游返回错误](12-troubleshooting.md#the-upstream-returned-an-error)。
