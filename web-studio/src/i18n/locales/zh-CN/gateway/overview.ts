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
    providedUpstreams_one: '网关管理员已提供 {{count}} 个上游',
    providedUpstreams_other: '网关管理员已提供 {{count}} 个上游',
    importedUpstreams_one: '已有 {{count}} 个上游，来自 OpenViking 的模型配置',
    importedUpstreams_other:
      '已有 {{count}} 个上游，来自 OpenViking 的模型配置',
    someImportedUpstreams:
      '已有 {{count}} 个上游，其中 {{imported}} 个来自 OpenViking 的模型配置',
    waitUpstreams: '等待网关管理员添加上游',
    providedProfiles_one: '网关管理员已提供 {{count}} 个上下文配置',
    providedProfiles_other: '网关管理员已提供 {{count}} 个上下文配置',
    waitProfiles: '等待网关管理员添加上下文配置',
    codingPlan:
      '{{name}} 是 Coding Plan 上游，确认套餐允许之前，发往它的请求都会被拦截。',
    confirm: '去确认',

    title: '快速开始',
    progress: '已完成 {{done}}/{{total}}',
    done: '已完成',
    view: '查看',
    upstream: {
      title: '添加上游',
      description: '网关转发请求的模型服务，例如 OpenAI 或火山方舟。',
      done_one: '已添加 {{count}} 个上游',
      done_other: '已添加 {{count}} 个上游',
      action: '添加上游',
      manual: '手动添加',
    },
    profile: {
      title: '创建上下文配置',
      description: '决定如何给新消息补充记忆、如何保存对话。',
      done_one: '已有 {{count}} 个上下文配置',
      done_other: '已有 {{count}} 个上下文配置',
      action: '使用推荐设置',
    },
    key: {
      title: '签发网关密钥',
      description: '客户端凭这个密钥调用网关。',
      blocked: '完成前两步后即可签发。',
      name: '我的密钥',
      waiting: '网关管理员添加上游和上下文配置后，才能签发密钥。',
      done_one: '已签发 {{count}} 个密钥',
      done_other: '已签发 {{count}} 个密钥',
      action: '签发密钥',
    },
    connect: {
      title: '接入客户端',
      description: '把客户端指向网关地址、填入密钥，发一条消息。',
      action: '查看接入指南',
    },
  },
  quick: {
    title: '快速模式',
    description: '跳过下面的步骤，一键拿到可以直接使用的网关密钥。',
    upstreamImport:
      '上游：导入 OpenViking 服务端的模型配置，沿用它的 API Key 和额度',
    upstreamUse: '上游：使用 {{names}}（{{models}}，{{protocol}}）',
    profileCreate: '上下文配置：用推荐设置新建“{{name}}”',
    profileUse: '上下文配置：使用“{{name}}”',
    keySelf: '密钥：为 {{user}} 签发',
    keyPick: '密钥：下一步选择密钥归属的 OpenViking 用户',
    action: '一键生成密钥',
    running: '生成中…',
    retry: '重试',
    locked: '创建上下文配置或导入模型需要网关管理密钥。',
    noUsable:
      'OpenViking 的模型配置里没有网关能直接转发的模型。处理下面列出的问题，或者在第 1 步手动添加上游。',
    keyName: '快速模式',
    divider: '或者按步骤手动配置',
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
  cacheMiss: {
    title_one: '有 {{count}} 段长对话连续没有命中提示词缓存',
    title_other: '有 {{count}} 段长对话连续没有命中提示词缓存',
    description_one:
      '最近连续 {{streak}} 次模型调用的输入都不少于 {{tokens}} Token，缓存命中率都是 0%。常见原因是请求开头的内容（系统提示词、工具定义）每次都在变，或者上游把请求分给了不同的账号或渠道。',
    description_other:
      '最近连续 {{streak}} 次模型调用的输入都不少于 {{tokens}} Token，缓存命中率都是 0%。常见原因是请求开头的内容（系统提示词、工具定义）每次都在变，或者上游把请求分给了不同的账号或渠道。',
    action:
      '对照服务商后台的缓存记录：那边有命中，说明上游没有把缓存用量传回网关；那边也没有，检查系统提示词里有没有时间这类每次都变的内容，以及中转服务是否在多个渠道之间轮换。',
    noWrites:
      '上游也没有写入缓存，客户端很可能没有在请求里设置 cache_control。对照服务商后台的缓存记录：那边有命中，说明上游没有把缓存用量传回网关。',
    streak_one: '连续 {{count}} 次未命中',
    streak_other: '连续 {{count}} 次未命中',
  },
  cacheUnreported: {
    title_one: '上游 {{upstreams}} 最近没有返回过缓存命中',
    title_other: '上游 {{upstreams}} 最近没有返回过缓存命中',
    description_one:
      '相关长对话的缓存命中率一直是 0%，统计时都按未命中计算。可能是服务商不支持提示词缓存、客户端没有开启缓存，或者中转服务没有把缓存用量传回网关。',
    description_other:
      '相关长对话的缓存命中率一直是 0%，统计时都按未命中计算。可能是服务商不支持提示词缓存、客户端没有开启缓存，或者中转服务没有把缓存用量传回网关。',
    separator: '、',
  },
  openviking: {
    title: 'OpenViking',
    description: '为新消息检索记忆，并保存对话。',
    version: '版本',
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
