const connect = {
  description:
    '把模型客户端的请求地址换成网关，再用网关密钥认证。选择你使用的客户端，查看它的具体配置。',
  fullGuide: '完整指南',
  address: {
    title: '网关地址',
    description:
      '客户端把模型请求发到这里，而不是直接发给服务商。Anthropic 客户端直接使用这个地址，OpenAI 兼容客户端要在末尾加上 `/v1`。',
    anthropic: 'Claude Code 和 Anthropic SDK',
    openai: 'OpenAI 兼容的客户端和 SDK',
    loopback: {
      title: '只有网关所在的机器能使用这个地址',
      description:
        '其他机器上的客户端连不上它。请在 `ov.conf` 中把 `context_gateway.public_url` 设为它们能访问的地址，然后重启网关。',
    },
    notPublic: {
      title: '客户端可能连不上这个地址',
      description:
        '这是 OpenViking 访问网关用的地址。请在 `ov.conf` 中把 `context_gateway.public_url` 设为客户端应该使用的地址，然后重启网关。',
    },
    keys: '客户端用网关密钥（`ovcg_…`）认证，代替服务商的 API Key。',
    manageKeys: '管理密钥',
  },
  guide: {
    title: '配置客户端',
    description: '选择你使用的客户端。示例中已经填好了这个网关的地址。',
    clients: '客户端',
    protocol: '使用的协议',
    upstreams: '可用上游：{{names}}',
    upstreamsMore: '可用上游：{{names}}，另有 {{count}} 个',
    separator: '、',
    noUpstream: {
      title: '请先添加 {{protocols}} 上游',
      description:
        '已启用的上游都不支持 {{protocols}}。网关只会把请求转发给协议相同的上游。',
      descriptionPicked:
        '已启用的上游都不支持 {{protocols}}。网关只会把请求转发给协议相同的上游。请添加一个，或者换一种协议配置客户端。',
      action: '添加上游',
      hint: '还没有适用于这个客户端的已启用上游',
      protocolHint: '还没有使用这种协议的已启用上游',
    },
    placeholders: '替换占位符',
    goodToKnow: '注意事项',
  },
  placeholders: {
    key: '网关密钥，可在“密钥”标签页签发',
    model: '上游接受的模型名',
    session: '在同一段对话内保持不变的 ID，例如聊天 ID',
  },
  snippets: {
    env: '环境变量',
    config: '配置',
    key: '终端',
    settings: '客户端设置',
    python: 'Python',
    curl: '终端',
    connection: '连接',
    headers: '请求头',
    endpoints: '地址',
  },
  clients: {
    'claude-code': {
      name: 'Claude Code',
      intro: 'Anthropic 推出的命令行编程 Agent。',
      steps: {
        env: '在启动 Claude Code 的终端里设置这些环境变量，也可以把它们写进 shell 配置文件。',
        start: '照常启动 `claude`。',
      },
      identity:
        'Claude Code 会发送自己的会话 ID，网关能自动识别每段对话，`--resume` 之后也一样。',
      notes: {
        address: '地址不要带 `/v1`，Claude Code 会自己补全路径。',
        hints:
          '`CLAUDE_CODE_GATEWAY_HINT_HEADERS=1` 让 Claude Code 给子 Agent、上下文压缩和后台请求加上标记，网关就不会为这些请求搜索记忆，也不会把它们当成对话轮次保存。',
        models:
          '上游必须接受 Claude Code 请求的模型名：把上游的模型列表留空、列出这些名称，或者用模型别名映射到服务商的模型。',
        subscription:
          'Claude 订阅登录无法通过网关使用，请为上游配置服务商的 API Key。',
      },
    },
    codex: {
      name: 'Codex CLI',
      intro:
        'OpenAI 推出的命令行编程 Agent。它的每个请求都带完整历史，正好满足网关对 Responses 的要求。',
      steps: {
        config:
          '在 `~/.codex/config.toml` 中把网关添加为服务商。开头两行顶层设置必须写在所有 `[section]` 之前。',
        key: '在运行 Codex 的环境里设置网关密钥。',
        start: '照常启动 `codex`。',
      },
      identity:
        'Codex 会发送自己的会话 ID，网关能自动识别每段对话，`codex resume` 之后也一样。',
      notes: {
        websocket:
          'Codex 会先尝试 WebSocket 连接，网关拒绝后它会自动改用 HTTP。',
        metadata:
          '遇到不认识的模型名，Codex 可能提示缺少模型元数据，这个提示不影响请求。',
        login: 'ChatGPT 登录无法通过网关使用，请为上游配置服务商的 API Key。',
      },
    },
    chat: {
      name: '聊天客户端和 SDK',
      intro: '支持 OpenAI Chat Completions API 的客户端和 SDK 都能接入。',
      steps: {
        settings: '在客户端填写 OpenAI 兼容服务商的地方填入这些值。',
        python: '在代码中把 OpenAI SDK 指向网关。',
        curl: '也可以在终端里发一个测试请求。',
      },
      identity:
        '发送 `X-OpenViking-Session` 请求头，取值在同一段对话内保持不变，例如聊天 ID。不发送时，网关根据历史匹配对话，重试或内容相同的对话可能被当成新对话。',
      notes: {
        streaming:
          '流式调用时，用 `stream_options.include_usage` 请求返回用量。否则流式回复不带 Token 数，请求日志无法显示用量，网关也无法判断长对话是否快要撑满上下文窗口。',
        otherApis:
          '另外两种 API 的 SDK 用法相同：Anthropic SDK 使用不带 `/v1` 的地址；Responses API 的每个请求都要带完整历史并设置 `store: false`。上游使用的 API 必须和 SDK 一致。',
        models:
          '客户端获取模型列表时，网关返回密钥所绑定上游上配置的模型和别名。',
      },
    },
    'open-webui': {
      name: 'Open WebUI',
      intro: '可自托管的聊天界面。把网关添加为一个 OpenAI 兼容连接即可。',
      steps: {
        connection:
          '在 Admin Panel → Settings → Connections 中添加一个 OpenAI 兼容连接，填入这个地址和密钥。',
        headers:
          '给这个连接添加以下自定义请求头，这样每个聊天都是一段独立的对话，后台任务也能被识别出来。',
        env: '在 Open WebUI 的环境变量中设置这一项。这样从附件中检索到的内容会放进 system 消息，而不是临时改写你的消息，对话历史在请求之间就能保持稳定。',
      },
      identity: '`X-OpenViking-Session` 请求头让每个聊天各自成为一段对话。',
      notes: {
        sharedMemory:
          '一个连接密钥只对应一位记忆归属者：通过这个连接聊天的所有 Open WebUI 用户，读写的都是密钥背后那位 OpenViking 用户的记忆。',
        tasks:
          '网关能识别生成标题和摘要的请求。如果请求日志把其他后台任务（标签、追问建议）标成“{{kind}}”，就把 Open WebUI 的任务模型改成不经过网关的连接。',
      },
    },
    opencode: {
      name: 'OpenCode',
      intro:
        '开源的命令行编程 Agent。把网关添加为一个自定义服务商，协议选你的上游使用的那种。',
      steps: {
        config:
          '在 `~/.config/opencode/opencode.json` 中添加这个服务商，与已有的设置合并。',
        key: '在运行 OpenCode 的环境里设置网关密钥。',
        start: '启动 OpenCode，选择 `openviking/<model>`。',
      },
      identity: 'OpenCode 会发送自己的会话 ID，网关能自动识别每段对话。',
      notes: {
        plugin:
          '如果同时安装了 OpenViking 的 OpenCode 插件，网关会让出这些对话。',
      },
    },
    pi: {
      name: 'pi',
      intro:
        '命令行编程 Agent。把网关添加为一个自定义服务商，协议选你的上游使用的那种。',
      steps: {
        config:
          '在 pi 的模型配置 `~/.pi/agent/models.json` 中添加这个服务商。`apiKey` 从环境变量读取网关密钥，开头的 `$` 不能省，否则 pi 会把变量名本身当作密钥发出去。',
        key: '启动 pi 前先导出这个环境变量。',
      },
      identity:
        '除非 pi 发送了“{{section}}”中列出的某个请求头，否则网关会根据历史匹配它的对话。',
      notes: {
        extension:
          '如果 pi 自己的 OpenViking 扩展处于启用状态，网关会让出这些对话，两者选一个使用即可。',
      },
    },
    dsh: {
      name: 'DSH',
      intro:
        'DeepSeek 推出的开源 Agent 框架 DeepSeek Harness。把网关添加为一个自定义模型服务商，协议选你的上游使用的那种。',
      steps: {
        config:
          '在 profile 配置中添加这个服务商，`dsh web` 使用的 profile 是 `web`。如果文件里已经有 `llm-pi-ai` 条目，就把 `openviking` 加到它的 `providers` 下，不要再写第二个条目。',
        key: '在运行 DSH 的环境里设置网关密钥。`apiKeyEnv` 填的就是这个环境变量名。',
        start:
          '启动 `dsh web`，在模型选择器中选择 `openviking` 服务商下的 `<model>`。',
      },
      identity:
        '除非 DSH 发送了“{{section}}”中列出的某个请求头，否则网关会根据历史匹配它的对话。',
      notes: {
        webUi:
          '也可以在 Web UI 的 Settings → Models → Add model provider → Custom model API 中添加，它写入的是同一个文件。',
        oneProtocol:
          'DSH 的一个服务商只使用一种协议。要通过多种协议使用网关，就为每种协议各声明一个服务商。',
        plugin:
          '如果同一个 profile 里装了 OpenViking 的 DSH 插件，网关会让出这些对话。',
      },
    },
    ark: {
      name: '火山方舟与 BytePlus 方舟 SDK',
      intro:
        '已经配置好火山方舟或 BytePlus 方舟的客户端和 SDK，只需换掉地址和密钥。网关既接受方舟自己的路径，也接受标准的 `/v1` 路径。',
      steps: {
        endpoints:
          '把方舟地址换成对应的网关地址，把方舟 API Key 换成网关密钥。',
        python: '使用火山方舟 Python SDK：',
      },
      identity:
        '和其他聊天客户端一样，为每段对话发送 `X-OpenViking-Session` 请求头。',
      notes: {
        routing:
          '路径只决定客户端使用哪种 API。请求仍然发往密钥绑定的、使用这种 API 并提供该模型的上游，通常是服务商选为“火山方舟”或“BytePlus 方舟（海外站）”的上游。',
      },
    },
  },
  identity: {
    title: '网关如何识别对话',
    description:
      '记忆按对话召回和保存。网关按以下顺序，取第一个出现的请求头作为对话 ID：',
    fallback:
      '这些请求头都没有时，网关查看历史中最近一条助手回复。如果恰好有一段之前的对话产生过这条回复，请求就并入那段对话；否则开始一段新对话。重试、重新生成的回答和内容相同的对话存在歧义，所以只要客户端允许设置请求头，就发送 `X-OpenViking-Session`。',
    scope:
      '对话归属于密钥背后的 OpenViking 用户，并且按 API 分开。同一用户的两个网关密钥如果发送相同的 ID，会共享同一段对话。',
  },
  passthrough: {
    title: '客户端自带服务商 API Key',
    description:
      '如果上游的凭证方式是“{{mode}}”，每个请求还要在这个请求头里带上客户端自己的服务商 API Key。网关密钥仍然填在客户端原本填写 API Key 的位置。',
    note: '网关把这个密钥转交给服务商，不会记录它。',
    upstreams: '需要它的上游：{{names}}',
    none: '目前没有上游需要它。',
  },
  plugin: {
    title: '同时在用 OpenViking 插件？',
    description:
      '如果某段对话已经通过 OpenViking 插件获得记忆，网关会让出这段对话：不召回、不保存，也不提供 OpenViking 工具，避免重复补充记忆。其他对话不受影响。',
    detection:
      '网关根据插件注入的记忆块或 OpenViking 工具识别插件。集成方也可以通过这个请求头主动声明。',
    restart:
      '这个状态会持续到对话结束。要重新使用网关的记忆，请在不启用插件的情况下开始一段新对话。',
  },
}

export default connect
