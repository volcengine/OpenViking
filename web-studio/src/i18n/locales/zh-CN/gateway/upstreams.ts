const upstreams = {
  import: {
    action: '从 OpenViking 导入',
    useSettings: '使用 OpenViking 的模型配置',
    hint: '即服务端的 VLM 配置',
    running: '导入中…',
    summary_one: '已导入或更新 {{count}} 个上游。',
    summary_other: '已导入或更新 {{count}} 个上游。',
    nothing: 'OpenViking 的模型配置里没有可导入的凭证。',
    needsConfirmation:
      '以下 Coding Plan 上游会拦截所有请求，确认套餐允许通过网关使用后才能启用：',
    skipped_one: '{{count}} 个凭证未导入：',
    skipped_other: '{{count}} 个凭证未导入：',
    confirm: '去确认',
    dismiss: '关闭',
    repeat:
      '再次导入会按 OpenViking 的配置更新地址和凭证，不会重复创建；你改过的启用状态、优先级和模型设置会保留。',
    reasons: {
      invalid_configuration: '模型配置无效，请检查服务端配置后重新导入。',
      openai_codex:
        'Codex 的 OAuth 凭证会定期刷新，网关无法长期使用。请手动添加上游。',
      litellm_no_api_base:
        '这个 LiteLLM 凭证没有填写 api_base，网关无法确定地址。请在服务端配置里补上 api_base 后重新导入。',
      litellm_unsupported_route:
        '这个 LiteLLM 路由不是 OpenAI 兼容协议，网关无法直接转发。请手动添加上游。',
      unknown: '无法导入这个凭证，请检查服务端模型配置。',
    },
  },
  readOnly: {
    empty: '等待网关管理员添加上游。',
    managedKey: 'API Key 由网关管理员保管。',
    clientKey: 'API Key 由每个客户端自带。',
    codingAllowed: 'Coding Plan，网关管理员已允许',
    codingBlocked: 'Coding Plan，请求已拦截',
  },
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
    connection: '测试',
    running: '测试中…',
    hint: '先检查上游连接，再选择模型发送真实生成请求。',
    savedOnly: '测试的是已保存的设置。要测试改动后的设置，请先保存。',
    passthrough: '这个上游由客户端自带 API Key，网关没有可用于测试的 API Key。',
    noKey: '请先填写 API Key。',
    codingPlan: '这个 Coding Plan 尚未授权通过网关发起模型请求。',
    result: {
      connected: '连接可用 · {{status}}',
      connectionUnverified: '连接可用 · {{status}}',
      ok: '模型可用 · {{status}}',
      failed: '失败 · {{status}}',
      unreachable: '无法连接',
      error: '无法测试',
    },
    explain: {
      connected: '服务商接受了密钥，并返回了模型列表。',
      connectionUnverified:
        '地址可达，但服务商不提供模型列表，API Key 尚未验证；仍可继续测试模型。',
      ok: '所选模型已成功生成回复。',
      auth: '服务商拒绝了 API Key 或某个请求头。',
      rateLimited: '服务商限制了请求频率，本次模型生成没有完成。',
      requestRejected:
        '服务商拒绝了生成请求（HTTP {{status}}）。请检查模型名、协议和完整请求地址。',
      invalidResponse: '服务商返回成功状态，但响应不符合所选模型协议。',
      status: '服务商返回了 HTTP {{status}}。',
      unreachable:
        '网关没有连上服务商或请求超时。请检查 Base URL，以及网关到服务商之间的网络。',
    },
    dialog: {
      title: '测试“{{name}}”',
      description:
        '先检查已保存配置的连接，再发送一次最小的非流式生成请求测试所选模型。',
      connection: '上游连接',
      checkingConnection: '正在检查连接…',
      exactEndpoint: '完整请求地址没有模型列表可检查，请直接测试模型。',
      model: '模型',
      modelPlaceholder: '输入服务商模型名',
      configuredModel: '可选择已配置的模型，也可以输入其他模型名。',
      unconfiguredModel: '这个上游未限制模型，请输入服务商支持的模型名。',
      response: '上游响应（JSON）',
      run: '运行测试',
    },
  },
  delete: {
    disable: '停用',
    disableHint: '也可以先停用它，停用后这些密钥会立即请求不到这个上游。',
    inUse_one: '仍有 {{count}} 个密钥在使用它。',
    inUse_other: '仍有 {{count}} 个密钥在使用它。',
    action: '删除',
    blocked_one: '有 {{count}} 个密钥在使用，请先吊销',
    blocked_other: '有 {{count}} 个密钥在使用，请先吊销',
    title: '删除“{{name}}”？',
    description:
      '上游会连同保存的 API Key 和请求头一起删除。还有密钥在使用它时无法删除。',
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
    missing: '还需填写或修正：{{fields}}',
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
        ark: 'Chat 和 Responses 填写 https://ark.cn-beijing.volces.com/api/v3，Anthropic 填写 https://ark.cn-beijing.volces.com/api/compatible；Coding Plan 分别填写 https://ark.cn-beijing.volces.com/api/coding/v3 和 https://ark.cn-beijing.volces.com/api/coding。网关会保持每段对话的 prompt_cache_key 不变，让缓存持续命中。',
        byteplus:
          'Chat 和 Responses 填写 https://ark.ap-southeast.bytepluses.com/api/v3，Anthropic 填写 https://ark.ap-southeast.bytepluses.com/api/compatible；Coding Plan 分别填写 https://ark.ap-southeast.bytepluses.com/api/coding/v3 和 https://ark.ap-southeast.bytepluses.com/api/coding。网关会保持每段对话的 prompt_cache_key 不变，让缓存持续命中。',
      },
    },
    baseUrl: {
      label: 'Base URL',
      description:
        '照服务商 SDK 文档填写 base_url，网关在后面拼接 /chat/completions、/responses 或 /v1/messages。同一服务商的不同协议地址不同时，请分别添加上游。地址里可以带查询参数，密钥请写成 {api_key}。',
      preview: '请求将发往',
      missingPath:
        '这个服务商的 base_url 需要带路径，只填域名会请求到错误地址。',
      anthropicVersion:
        'Anthropic 的 base_url 不需要 /v1，网关会自动拼接 /v1/messages。',
      removeVersion: '去掉 /v1',
      correctPath: '改用 {{url}}',
      useDefault: '使用默认地址',
    },
    appendEndpointPath: {
      label: '拼接协议接口路径',
      description:
        '在 Base URL 后拼接所选协议的接口路径。如果 Base URL 已经是完整的请求地址，请关闭此开关。',
    },
    keyHeader: {
      label: '密钥发送方式',
      authorization: 'Authorization: Bearer',
      apiKey: 'api-key 请求头（Azure OpenAI）',
      xApiKey: 'x-api-key 请求头',
      inUrl: '放在地址里（{api_key}），不发送鉴权请求头',
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
      addressChanged:
        '地址已修改，请重新填写 API Key。原来保存的 API Key 不会发往新地址。',
      storedDescription: '已加密保存。留空则保留原密钥，填写新密钥会替换它。',
    },
    headers: {
      label: '额外请求头',
      description:
        '随每个请求发给服务商。取值加密保存，之后不再显示：已保存的取值留空即保持不变，删除这一行则移除该请求头。',
      name: '请求头名称',
      value: '取值',
      add: '添加请求头',
      addressChanged:
        '地址已修改，请重新填写请求头的取值。原来保存的取值不会发往新地址。',
    },
    codingPlan: {
      label: '这是 Coding Plan 或订阅密钥',
      description: '订阅类密钥通常只授权在服务商自己的工具里使用。',
      warning:
        '网关默认拒绝所有发往这个上游的请求。请改用模型 API Key；如果套餐条款允许在服务商自己的工具之外使用，可以打开下面的选项。',
      allow: '允许通过网关使用这个套餐',
      allowDescription:
        '部分 Coding Plan 只授权在服务商自己的工具里使用。打开前请确认套餐条款允许，否则账号可能被服务商限制。',
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
