const overview = {
  description:
    '客户端如何使用网关：请求量、提示词缓存和记忆召回，以及 OpenViking 的运行状态。',
  loadFailed: '无法加载概览',
  showIssues: '在请求日志中查看',
  sampleNote_one:
    '以上数据统计自请求日志中最新的 {{limit}} 条记录。日志保留 {{count}} 天。',
  sampleNote_other:
    '以上数据统计自请求日志中最新的 {{limit}} 条记录。日志保留 {{count}} 天。',
  setup: {
    title: '快速开始',
    description:
      '完成下面四步，就能让第一段对话用上记忆。客户端发出第一个请求后，这张卡片会自动隐藏。',
    progress: '已完成 {{done}}/{{total}}',
    done: '已完成',
    view: '查看',
    upstream: {
      title: '添加上游',
      description:
        '网关把请求转发给哪个模型服务商，例如 Anthropic、OpenAI 或火山方舟。',
      done_one: '已添加 {{count}} 个上游',
      done_other: '已添加 {{count}} 个上游',
      action: '添加上游',
    },
    profile: {
      title: '创建上下文配置',
      description:
        '决定网关如何为新消息补充记忆、如何保存对话。推荐设置适合大多数客户端。',
      done_one: '已有 {{count}} 个上下文配置',
      done_other: '已有 {{count}} 个上下文配置',
    },
    key: {
      title: '签发网关密钥',
      description:
        '客户端用这个密钥调用网关。每个密钥属于一个 OpenViking 用户，网关读写的就是这个用户的记忆。',
      blocked: '请先添加上游并创建上下文配置。',
      done_one: '已签发 {{count}} 个密钥',
      done_other: '已签发 {{count}} 个密钥',
      action: '签发密钥',
    },
    connect: {
      title: '接入客户端',
      description:
        '把 Claude Code、Codex CLI 或聊天应用指向网关地址并填入密钥，然后发一条消息。',
      action: '查看接入指南',
    },
  },
  kpi: {
    requests: {
      label: '请求数',
      hint: '经过网关的模型请求，不含记忆同步事件。',
      last: '最近一次请求：{{time}}',
      outputTokens: '输出 {{tokens}} Token',
      none: '还没有请求',
    },
    firstCall: {
      label: '首次调用缓存命中率',
      hint: '每条新用户消息第一次调用模型时，输入 Token 中命中服务商提示词缓存的比例。网关工作正常时，这个值应接近不经过网关时服务商能达到的命中率；明显下降通常说明之前消息里补充的记忆没有原样发回。',
      footnote_one: '{{number}} 条新消息',
      footnote_other: '{{number}} 条新消息',
    },
    continuation: {
      label: '轮内缓存命中率',
      hint: '同一轮对话里后续的模型调用（例如工具结果返回之后）命中提示词缓存的比例，包括模型使用 OpenViking 工具时产生的调用。',
      footnote_one: '{{number}} 个工具步骤',
      footnote_other: '{{number}} 个工具步骤',
    },
    recall: {
      label: '召回的记忆条数',
      hint: '网关为新消息补充的记忆条数。平均耗时只统计实际检索了 OpenViking 的消息。',
      footnote_one: '检索 {{number}} 次，平均 {{duration}}',
      footnote_other: '检索 {{number}} 次，平均 {{duration}}',
      none: '还没有召回',
    },
  },
  openviking: {
    title: 'OpenViking',
    description: '为新消息检索记忆，并保存对话。',
    version: '版本',
    authMode: '认证方式',
    authModes: {
      api_key: 'API 密钥模式',
      trusted: 'Trusted 模式',
      dev: '开发模式',
      oidc: 'OIDC 模式',
      ldap: 'LDAP 模式',
    },
    unreachable:
      '连不上 OpenViking 期间，请求照常发给模型，只是不带记忆；保存对话会一直重试，直到 OpenViking 恢复。',
    starting: '网关还没完成对 OpenViking 的首次检查，请稍后刷新。',
  },
  saving: {
    title: '保存对话',
    ok: '最近的请求中没有保存失败的对话。',
    retrying_one: '有 {{count}} 段对话保存到 OpenViking 失败，正在重试。',
    retrying_other: '有 {{count}} 段对话保存到 OpenViking 失败，正在重试。',
    paused_one:
      '有 {{count}} 段对话多次保存失败，已暂停保存；网关每 5 分钟重试一次。',
    paused_other:
      '有 {{count}} 段对话多次保存失败，已暂停保存；网关每 5 分钟重试一次。',
  },
  degraded: {
    title: '降级请求',
    description: '网关没能按常规处理记忆的请求。',
    suggestion: '建议操作：',
    empty: {
      title: '没有降级请求',
      description: '统计范围内的请求都按常规处理了记忆。',
    },
  },
  recent: {
    title: '最近请求',
    columns: {
      time: '时间',
      type: '类型',
      model: '模型',
      status: '状态',
      memory: '记忆',
    },
    empty: {
      title: '还没有请求',
      description: '客户端通过网关发出请求后，会显示在这里。',
    },
  },
}

export default overview
