---
description: 开启 OpenViking 网关，签发网关密钥，发送测试请求并确认记忆生效。
---

# OpenViking 网关快速上手

跟着本页走完，你会从一套能正常工作的 OpenViking 服务出发，拿到一个[网关密钥](../guides/15-gateway.md#key-terms)，经网关发出一次测试请求，并亲眼确认记忆生效。开始前，`ov.conf` 中需要已经配好 embedding 和 VLM 模型（见[快速开始](../getting-started/02-quickstart.md)）。网关包含在核心包中，不需要另外安装。

先按服务端的情况选一条路径：

| 你的服务端 | 跟着做 |
| --- | --- |
| 绑定在 `127.0.0.1`，使用 API Key 认证 | [本地服务](#local-server-no-api-key) |
| 通过网络访问，使用 `api_key` 认证 | [远程服务](#remote-server-api-keys) |
| 已有 OpenViking 服务，不想改它的配置 | 先按[独立部署](../guides/22-gateway-operations.md#standalone-deployment)运行网关，再回到[发送测试请求](#send-a-test-request) |

## 本地服务 {#local-server-no-api-key}

启用嵌入式网关后，本地服务也使用 API Key 认证。`openviking-server init` 会把原来的本地 Trusted 配置切换为 API Key 模式。

1. **开启网关。** 运行 `openviking-server init`，在 **Enable OpenViking Gateway on the same port?** 处回答 yes；已有配置时，在菜单中选择 **Update gateway**。也可以手动把 `{"gateway": {"enabled": true}}` 合并进 `ov.conf`。
2. **启动或重启 `openviking-server`。** 网关和 OpenViking 共用同一个端口，地址是 `http://127.0.0.1:1933/gateway`。
3. **创建普通 OpenViking 身份。** 运行 `ov config`，使用 root key 连接，在**用户管理**中创建或选择 `default/default`，并保存其 User/Admin key。root key 不能作为网关业务密钥。
4. **在 Studio 中打开网关。** 打开 <http://127.0.0.1:1933/studio/>，用该 User/Admin key 登录，在侧边栏选择 **OpenViking 网关**。可用**快速模式**导入可用 VLM 配置、创建默认上下文配置并签发网关密钥，也可以逐项手动配置。导入的 Coding Plan 上游需要确认后才能使用。
5. **复制网关密钥。** 完整的 `ovgw_…` 只显示一次。

网关地址是 `http://127.0.0.1:1933/gateway`。接下来[发送测试请求](#send-a-test-request)。

## 远程服务（使用 API 密钥） {#remote-server-api-keys}

远程服务指通过网络访问、使用 `api_key` 认证的服务。每个人用自己的 OpenViking API 密钥登录 Studio。

不想在服务端主机上运行 init 的话，可以用 root API 密钥登录 Studio，在 OpenViking 网关页面点**开启网关**，代替第 1-3 步；确认对话框里也能生成网关管理密钥。见[在 Server 中启用](../guides/22-gateway-operations.md#enable-it-in-the-server)。

1. **开启网关。** 在服务端主机上运行 `openviking-server init`，绑定地址选择 **Remote (0.0.0.0)**，在 **Enable OpenViking Gateway on the same port?** 处回答 yes。
2. **决定谁能修改上游。** 接下来的 **Allow users to configure their own gateway upstreams?** 决定谁能修改上游和上下文配置。回答 yes，每个登录的用户都能配置本账号的上游和上下文配置。回答 no，则改由[网关管理密钥](../guides/22-gateway-operations.md#gateway-management-key)把关：init 生成一个以 `ovgm_` 开头的密钥，打印出来并写入 `ov.conf` 的 `gateway.management_key`，只有带着这个密钥的请求才能修改上游和上下文配置。请保存好打印出的密钥，第 6 步要用。
3. **重启服务。** 网关和 OpenViking 共用同一个端口，地址是 `https://ov.example.com/gateway`。
4. **准备 OpenViking API 密钥。** 用你自己的用户 API 密钥，或者按[认证](../guides/04-authentication.md)用 root API 密钥创建账号和用户。
5. **登录 Studio。** 打开 `https://ov.example.com/studio/`，在**连接设置**中填入 API 密钥，然后在侧边栏选择 **OpenViking 网关**，应该能看到“概览”标签页。
6. **添加上游。** 在“上游”标签页点**添加上游**，选择服务商和客户端使用的协议（下面的测试请求用 Chat Completions），粘贴服务商的 API Key 后保存。然后在上游列表里点**测试**，看到**连接可用**或**模型可用**，就说明网关能连上服务商。如果标签页是只读的，说明设置了网关管理密钥：点**输入网关管理密钥**解锁编辑（见[解锁编辑](06-manage-in-studio.md#unlock-editing)），或者联系部署网关的人。
7. **创建上下文配置。** 在“上下文配置”标签页点**使用推荐设置创建**，卡片列表里应该出现这份新的上下文配置。
8. **签发网关密钥**，做法同本地路径第 4 步，签发给你自己。随后会弹出**复制网关密钥**对话框，密钥只显示这一次，请立即复制。要给其他用户签发，见[签发密钥](09-gateway-keys.md#issue-a-key)。

网关地址是 `https://ov.example.com/gateway`，其中 `ov.example.com` 换成你的服务端地址。

## 发送测试请求 {#send-a-test-request}

> **提示**：[确认记忆生效](#confirm-that-memory-works)的第 3 步要等对话停顿 10 分钟才会提取记忆。想更快试用，可以先建一份上下文配置，把**最新回复等待时长**调短（例如 30 秒），用它签发一个密钥，下面的 `GATEWAY_KEY` 就用这个密钥。上下文配置的修改只对之后开始的对话生效（见[修改何时生效](06-manage-in-studio.md#when-changes-take-effect)）。

用 curl 发两个请求：第一个列出密钥可用的模型，第二个经网关向模型提一个问题。把 `GATEWAY_URL` 设为你的网关地址（本地服务是 `http://127.0.0.1:1933/gateway`），并把 `<model>` 换成上游提供的模型：

```bash
export GATEWAY_URL=https://ov.example.com/gateway
export GATEWAY_KEY='ovgw_...'

curl -s "$GATEWAY_URL/v1/models" -H "Authorization: Bearer $GATEWAY_KEY"

curl -s "$GATEWAY_URL/v1/chat/completions" \
  -H "Authorization: Bearer $GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -H "X-OpenViking-Session: quickstart-1" \
  -d '{"model": "<model>", "messages": [{"role": "user", "content": "记住：我喜欢简短的回答。"}]}'
```

| 命令 | 预期结果 |
| --- | --- |
| `GET /v1/models` | 密钥绑定的已启用上游里配置的模型名和别名；密钥限定了可用模型时，只列出允许的那些 |
| `POST /v1/chat/completions` | 服务商的一次正常回复 |

模型列表不全甚至为空，请求也可能照样成功：模型列表留空的上游接受任意模型名，但不往这个列表里加任何名称。`X-OpenViking-Session` 请求头用来标识对话，带同一个值的下一个请求会接着这段对话往下走，详见[网关如何识别对话](03-connect-clients.md#how-conversations-are-recognized)。

## 确认记忆生效 {#confirm-that-memory-works}

依次确认三件事：请求经过了网关，对话已经保存，新对话里能召回记忆。

1. **请求经过了网关。** 打开“请求日志”标签页，刚才的请求应该显示为**新消息**，状态 200。OpenViking 里有相关内容时，**记忆**列会显示补充的条数，例如 `+3`；全新账号下这一列为空。
2. **对话已保存。** 用同一个 `X-OpenViking-Session` 值再发一个请求，它的 `messages` 要包含整段对话：你的第一条消息、收到的回复和一条新消息。聊天客户端会自动带上这些内容，用 curl 时要自己补上：

   ```bash
   curl -s "$GATEWAY_URL/v1/chat/completions" \
     -H "Authorization: Bearer $GATEWAY_KEY" \
     -H "Content-Type: application/json" \
     -H "X-OpenViking-Session: quickstart-1" \
     -d '{"model": "<model>", "messages": [
       {"role": "user", "content": "记住：我喜欢简短的回答。"},
       {"role": "assistant", "content": "<收到的回复>"},
       {"role": "user", "content": "谢谢。"}]}'
   ```

   这个请求一到，第一轮对话就会保存（[为什么保存总是晚一轮](01-how-it-works.md#when-conversations-are-saved)）。打开 Studio 的**会话**页面，应该能看到一个 `gateway-…` 会话。会话属于密钥所属的用户：如果密钥是为别人签发的，需要在**连接设置**中填入该用户的 OpenViking API 密钥登录后才能看到。
3. **记忆已提取。** 对话停顿达到**最新回复等待时长**（默认 10 分钟）后，网关提交会话，OpenViking 在后台提取记忆，需要稍等片刻。然后换一个会话值开始新对话，问一个依赖这条记忆的问题，例如“你的回答应该多长？”。**记忆**列应显示召回的条数，回复也应该用上了这条记忆。

如果**记忆**列一直为空，或者看不到会话，见[没有补充记忆](12-troubleshooting.md#no-memory-is-added)和[OpenViking 中看不到对话](12-troubleshooting.md#conversations-do-not-appear-in-openviking)。

## 下一步 {#next-steps}

- [接入客户端](03-connect-clients.md#connect-clients)：把 Claude Code、Codex CLI 或聊天应用指向网关。
- [签发密钥](09-gateway-keys.md#issue-a-key)：给其他用户签发各自的网关密钥。
- [部署与运维](../guides/22-gateway-operations.md)：用 Docker Compose、Helm 或独立部署为团队运行网关。
