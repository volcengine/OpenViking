const upstreams = {
  description:
    '网关把请求转发给这些模型服务商。每个上游只支持一种协议，网关不做协议转换。',
  add: '添加上游',
  loadFailed: '无法加载上游',
  empty: {
    title: '还没有上游',
    description:
      '添加客户端要访问的模型服务商，例如 OpenAI、Anthropic、DeepSeek 或火山方舟。签发网关密钥时，再为每个密钥选择可以使用的上游。',
  },
  columns: {
    name: '名称',
    protocol: '协议',
    provider: '服务商',
    models: '模型',
    credentials: '凭证',
    priority: '优先级',
    usedBy: '使用情况',
    enabled: '启用',
    actions: '操作',
  },
  models: {
    any: '不限模型',
    more: '+{{count}}',
    aliases_one: '{{count}} 个别名',
    aliases_other: '{{count}} 个别名',
  },
  credentials: {
    keyMissing: '缺少 API Key',
    keyMissingHint:
      '没有保存 API Key，发往这个上游的请求都会失败。请在编辑页填写密钥。',
    blocked: '请求被拒绝',
    blockedHint:
      '这个上游标记为使用 Coding Plan 或订阅密钥，网关会拒绝所有发往它的请求。',
  },
  usedBy_one: '{{count}} 个密钥',
  usedBy_other: '{{count}} 个密钥',
  notUsed: '未被使用',
  toggle: {
    enable: '启用 {{name}}',
    disable: '停用 {{name}}',
  },
  test: {
    action: '测试',
    connection: '测试连接',
    running: '测试中…',
    hint: '用已保存的密钥和请求头向服务商请求模型列表。它只验证地址和密钥是否可用，不检查模型配置，也不发送对话请求。',
    savedOnly: '测试的是已保存的设置。要测试改动后的设置，请先保存。',
    passthrough: '这个上游由客户端自带 API Key，网关没有可用于测试的 API Key。',
    noKey: '请先填写 API Key。',
    result: {
      ok: '可连通 · {{status}}',
      failed: '失败 · {{status}}',
      unreachable: '无法连接',
      error: '无法测试',
    },
    explain: {
      ok: '服务商接受了密钥，并返回了模型列表。',
      auth: '服务商拒绝了 API Key 或某个请求头。',
      notFound:
        '这个地址下没有模型列表。请检查 Base URL；如果服务商本来就不提供模型列表，实际请求可能仍然正常。',
      status: '服务商返回了 HTTP {{status}}。',
      unreachable:
        '网关在 10 秒内没有连上服务商。请检查 Base URL，以及网关到服务商之间的网络。',
    },
  },
  delete: {
    action: '删除',
    blocked_one: '有 {{count}} 个密钥在使用，请先吊销',
    blocked_other: '有 {{count}} 个密钥在使用，请先吊销',
    title: '删除“{{name}}”？',
    description:
      '上游会连同保存的 API Key 和请求头一起删除。目前没有网关密钥使用它，不会影响任何客户端。',
    confirm: '删除上游',
  },
  toast: {
    created: '已添加“{{name}}”',
    saved: '已保存“{{name}}”',
    deleted: '已删除“{{name}}”',
  },
  editor: {
    back: '上游',
    newTitle: '新建上游',
    newDescription:
      '接入一个模型服务商的接口。签发网关密钥时，再选择每个密钥可以使用哪些上游。',
    editDescription: '保存后立即生效。优先级只影响之后新开始的对话。',
    notFound: {
      title: '这个上游已不存在',
      description: '它可能已被删除。请返回列表查看现有的上游。',
    },
    missing: '还需填写：{{fields}}',
    separator: '、',
    create: '添加上游',
  },
  sections: {
    endpoint: {
      title: '接口',
      description: '网关把请求发到哪里，以及使用哪种协议。',
    },
    credentials: {
      title: '凭证',
      description: '网关用什么凭证访问服务商。',
    },
    models: {
      title: '模型',
      description: '这个上游提供哪些模型名，以及它们在服务商那边叫什么。',
    },
    routing: {
      title: '路由',
      description: '有多个上游可选时，网关如何选择。',
    },
  },
  form: {
    name: {
      label: '名称',
      placeholder: '例如：OpenAI 生产环境',
      description: '在 Studio 中和签发网关密钥时显示。',
    },
    protocol: {
      label: '协议',
      description: '客户端必须用同一种协议调用网关，网关不做协议转换。',
      unsupported: '{{vendor}} 不提供这种接口',
      options: {
        anthropic: 'Claude Code 和 Anthropic SDK',
        chat: '大多数聊天客户端和 SDK',
        responses: 'Codex CLI，只支持携带完整历史的请求',
      },
    },
    vendor: {
      label: '服务商',
      description:
        '选定服务商后，下方只能选它提供的协议。列表里没有的服务商，以及 LiteLLM 这类兼容代理，请选“通用”。',
      hints: {
        deepseek:
          '请求带工具时，DeepSeek 要求回传之前每条回复的推理内容。网关会补回客户端丢掉的部分，所以 OpenViking 工具可以正常使用；如果在下方关闭补全，只有请求关闭了思考模式才会提供这些工具。',
        ark: '填写不带路径的方舟地址，例如 https://ark.cn-beijing.volces.com。网关会按协议补上 /api/v3 或 /api/compatible/v1，并让每段对话的 prompt_cache_key 保持不变，缓存才能持续命中。',
        byteplus:
          '填写不带路径的 BytePlus 方舟地址，例如 https://ark.ap-southeast.bytepluses.com。网关会按协议补上 /api/v3 或 /api/compatible/v1，并让每段对话的 prompt_cache_key 保持不变，缓存才能持续命中。',
      },
    },
    baseUrl: {
      label: 'Base URL',
      description:
        '服务商的 API 地址，选择服务商时会自动填入。末尾带不带 /v1 都可以。',
      preview: '请求将发往',
      useDefault: '使用默认地址',
    },
    authMode: {
      label: 'API Key 由谁提供',
      managed: {
        title: '由网关保管 API Key',
        description: '客户端只需要网关密钥。',
      },
      passthrough: {
        title: '每个客户端自带 API Key',
        description:
          '客户端除了网关密钥，还要在 {{header}} 请求头里带上自己的服务商 API Key。',
      },
    },
    apiKey: {
      label: 'API Key',
      placeholder: '粘贴服务商的 API Key',
      stored: '已保存',
      description: '加密保存，之后不再显示。',
      storedDescription: '已加密保存。留空则保留原密钥，填写新密钥会替换它。',
    },
    headers: {
      label: '额外请求头',
      description:
        '随每个请求发给服务商。取值加密保存，之后不再显示：已保存的取值留空即保持不变，删除这一行则移除该请求头。',
      name: '请求头名称',
      value: '取值',
      add: '添加请求头',
    },
    codingPlan: {
      label: '这是 Coding Plan 或订阅密钥',
      description: '订阅类密钥通常只授权在服务商自己的工具里使用。',
      warning:
        '勾选后，网关会拒绝所有发往这个上游的请求。请改用模型 API Key；如果套餐允许在其他客户端使用，可以选择仍然允许。',
      allow: '仍然允许',
      allowDescription: '网关会用这个密钥转发请求，请先确认套餐允许这样使用。',
    },
    models: {
      label: '模型',
      placeholder: '输入模型名后按回车',
      description: '客户端可以请求的模型名。留空则接受任意模型名。',
    },
    aliases: {
      label: '模型别名',
      description:
        '让客户端用另一个名字请求模型，网关转发前会换成上游的模型名。',
      name: '客户端使用的名称',
      target: '发给上游的模型',
      add: '添加别名',
    },
    contextWindows: {
      label: '上下文窗口',
      description:
        '长对话在接近模型的上下文窗口时压缩。没有列在这里的模型使用上下文配置里的默认上下文窗口，那里也没有设置时按 1,000,000 Token 计算。请填写发给上游的模型名，也就是别名转换之后的名称。',
      model: '上游模型',
      tokens: 'Token 数',
      add: '添加窗口',
    },
    priority: {
      label: '优先级',
      description: '多个上游提供同一模型时，新对话使用优先级最高的上游。',
    },
    enabled: {
      label: '启用',
      description:
        '停用的上游不再接收请求。正在使用它的对话会转到其他提供该模型的上游，前提是客户端的密钥允许使用。',
    },
    gatewayTools: {
      label: '允许 OpenViking 工具',
      description:
        '允许模型通过这个上游使用上下文配置中选定的 OpenViking 工具。关闭后，进行中的对话也会停用这些工具。',
    },
    replayReasoning: {
      label: '补全推理内容回传',
      description:
        '客户端回传历史时丢掉了模型的推理内容，网关会在后续请求中补回。DeepSeek 在带工具的请求里缺少推理内容会报错，所以 DeepSeek、火山方舟和 BytePlus 方舟默认开启。',
    },
    cacheMinTokens: {
      label: '最小可缓存长度',
      description:
        '只用于在请求日志里标记请求能否命中缓存。请设为该模型可缓存提示词的最小长度。',
    },
  },
}

export default upstreams
