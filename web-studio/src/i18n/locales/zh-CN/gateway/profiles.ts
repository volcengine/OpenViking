const profiles = {
  description:
    '决定对话如何使用 OpenViking 的记忆和工具。保存后的修改只对之后开始的对话生效。',
  defaultName: '默认',
  loadFailed: '无法加载上下文配置',
  actions: {
    new: '新建配置',
    createRecommended: '使用推荐设置创建',
    customize: '自定义',
    reset: '恢复推荐设置',
  },
  usedBy_one: '{{count}} 个密钥在用',
  usedBy_other: '{{count}} 个密钥在用',
  unused: '没有密钥在用',
  deleteBlocked_one: '有 {{count}} 个密钥在使用，请先吊销',
  deleteBlocked_other: '有 {{count}} 个密钥在使用，请先吊销',
  empty: {
    title: '还没有上下文配置',
    description:
      '每个网关密钥都需要一份上下文配置。推荐设置会为每条新消息召回记忆、保存对话、为长对话生成摘要，并让模型使用只读的 OpenViking 工具，之后可以随时修改。',
  },
  summary: {
    recallOn: '开启 · 每条消息 {{tokens}} Token',
    compactionOn: '用到 {{percent}} 时压缩',
    agentWindowsOn: 'Agent 自管窗口',
    toolsEnabled_one: '已启用 {{count}} 个工具',
    toolsEnabled_other: '已启用 {{count}} 个工具',
  },
  deleteDialog: {
    title: '删除“{{name}}”？',
    description: '没有网关密钥在用这份配置。删除后无法恢复。',
    confirm: '删除配置',
  },
  toast: {
    created: '已创建上下文配置“{{name}}”',
    saved: '已保存“{{name}}”，新对话会使用这些设置。',
    deleted: '已删除“{{name}}”',
    reset: '已恢复推荐设置，保存后生效。',
  },
  editor: {
    back: '上下文配置',
    newTitle: '新建上下文配置',
    create: '创建配置',
    banner: '保存后的修改只对新对话生效，进行中的对话沿用开始时的设置。',
    copyName: '{{name}} 副本',
    sourceMissing: '要复制的配置已不存在，已改为从推荐设置开始。',
    notFound: {
      title: '这份上下文配置已不存在',
      description: '它可能已被删除。请返回列表选择其他配置。',
    },
    sections: '配置分组',
    unsaved: '有未保存的修改',
    invalid: '请先修正标出的设置，再保存。',
    needsName: '填写名称后即可保存。',
    unitHint: '{{unit}}（{{value}}）',
  },
  name: {
    label: '名称',
    placeholder: '例如：编程',
    description: '为网关密钥选择配置时显示这个名称。',
  },
  recall: {
    title: '召回记忆',
    description:
      '每收到一条新的用户消息，就用它检索 OpenViking，并把相关内容附加到这条消息上。',
    profile: {
      label: '会话开头提供用户画像',
      description:
        '新对话开始时，向模型提供你的 OpenViking 用户画像。此设置独立于记忆召回。',
    },
    profileMaxTokens: {
      label: '开头内容预算',
      description:
        '限制开头的画像、记忆目录和技能目录总量。目录需要启用读取工具。这份预算独立于召回，设为 0 时三项都不提供。',
    },
    showRecall: {
      label: '显示召回摘要',
      description:
        '在回复开头用一行说明 OpenViking 附加了什么：几条、属于哪些类别、几个条目名，召回失败时说明原因。模型看不到这行摘要，它也不会保存到 OpenViking。只对新对话生效。',
    },
    sources: {
      label: '检索范围',
      description: '召回时检索哪些内容。',
      memory: '记忆',
      resource: '资源',
      skill: '技能',
    },
    maxTokens: {
      label: '单条消息预算',
      description: '一条消息最多附加多少记忆，按网关的估算计算。',
    },
    sessionMaxTokens: {
      label: '单个上下文窗口预算',
      description:
        '一个上下文窗口内累计最多附加多少记忆，对话每压缩一次就重新计算。设为 0 相当于关闭召回。',
    },
    scoreThreshold: {
      label: '相关度阈值',
      description:
        '只附加相关度不低于该值的结果，取值 0 到 1。值越高，附加的内容越少、越贴切。',
    },
    recallTimeout: {
      label: '超时时间',
      description:
        'OpenViking 没有及时返回时，这条消息会不带记忆直接发给模型。',
    },
    queryMaxChars: {
      label: '检索文本长度',
      description:
        '召回用最新一条用户消息作为检索文本，超出这个长度的部分会被截掉。',
    },
    quotas: {
      label: '按分类限量',
      description: '为每个分类设置最多附加的条数。只检索数量大于 0 的分类。',
      categories: {
        events: '事件',
        entities: '实体',
        preferences: '偏好',
        experiences: '经验',
        resources: '资源',
        skills: '技能',
      },
    },
  },
  capture: {
    title: '保存对话',
    description:
      '把已完成的轮次写入 OpenViking 会话，让 OpenViking 从中提取记忆。',
    idleSeconds: {
      label: '最新回复等待时长',
      description:
        '下一条消息到达时，上一轮会立即保存。最新一轮回复会先等待这段时间，如果没有后续消息，就保存下来，OpenViking 随即开始提取记忆。',
    },
    commitTokens: {
      label: '提交阈值',
      description:
        '已保存但未提交的对话达到这个量时提交一次。OpenViking 在提交时提取记忆。',
    },
    keepRecentMessages: {
      label: '保留最近消息',
      description: '每次提交至少在会话中留下最近这么多条消息，按整轮计算。',
    },
  },
  longConversations: {
    title: '长对话',
    description: '让长对话始终不超出模型的上下文窗口。',
    compaction: {
      label: '压缩',
      description:
        '对话接近模型的上下文窗口时，由同一个模型为它写一份摘要，用摘要替换之前的消息。',
    },
    threshold: {
      label: '压缩时机',
      description: '上下文窗口用到多大比例时压缩，取值 0.5 到 0.98。',
    },
    summaryMaxTokens: {
      label: '摘要长度上限',
      description: '模型写一份摘要最多能用多少 Token。',
    },
    contextWindow: {
      label: '默认上下文窗口',
      description:
        '上游没有列出该模型的上下文窗口时使用。两处都没有设置时，网关按 1,000,000 Token 计算，所以窗口更小的模型需要设置这一项。',
    },
    agentWindows: {
      label: 'Agent 自管上下文窗口',
      experimental: '实验性',
      description:
        '给模型两个工具，让它自己写好交接笔记、开启新的上下文窗口，并告诉它当前窗口用了多少。开启压缩时，压缩仍作为兜底。只在能使用 OpenViking 工具的对话中生效。',
      needsTools: '这份配置没有开启 OpenViking 工具，此设置不会生效。',
    },
    softRatio: {
      label: '软提醒时机',
      description:
        '上下文窗口用到这个比例时，提醒模型做完当前这一步就开启新窗口。每个窗口只提醒一次。',
    },
    hardRatio: {
      label: '硬提醒时机',
      description:
        '上下文窗口用到这个比例时，要求模型立即开启新窗口。模型开启新窗口之前每一步都会提醒，须高于软提醒。',
    },
  },
  tools: {
    title: 'OpenViking 工具',
    description:
      '让模型在回答时使用 OpenViking 工具。支持 Chat Completions、Responses 和 Anthropic Messages；客户端须回传完整对话。',
    available: {
      label: '工具',
      description:
        '推荐设置勾选只读工具，会修改数据的工具不勾选。以后新增的工具会自动启用。上游也需要允许使用 OpenViking 工具。',
    },
    empty: '签发网关密钥后即可加载工具清单。',
    loadFailed: '无法加载 OpenViking 工具',
    readOnly: '只读',
    modifiesData: '会修改数据',
    executionNotice:
      '网关执行所选工具时不经过客户端的权限确认，其中一些工具会修改或删除数据。“显示工具调用”只在回复里报告每次调用，不会事先征求同意。',
    showCalls: {
      label: '显示工具调用',
      description:
        '网关每执行一次 OpenViking 工具，就在回复里加一行提示，让用户看到这次调用。模型看不到这些提示，它们也不会保存到 OpenViking。',
    },
    maxRounds: {
      label: '每次请求的工具轮数',
      description:
        '达到这个轮数后，网关拒绝后续的 OpenViking 调用，模型用已有结果和客户端自己的工具继续。留空表示不限。',
    },
    timeoutSeconds: {
      label: '单次调用超时',
      description: '超时的调用会向模型返回错误。',
    },
    resultBytes: {
      label: '结果大小上限',
      description: '超出的部分会在交给模型前截掉。',
    },
    totalSeconds: {
      label: '总时长',
      description: '模型使用工具超过这个时长，请求就会失败。留空表示不限。',
    },
    totalTokens: {
      label: 'Token 预算',
      description:
        '一次请求中，工具调用和结果最多增加的 Token 数（估算值）。用完后，网关拒绝后续的 OpenViking 调用，模型用已有结果和客户端自己的工具继续。留空表示不限。',
    },
  },
}

export default profiles
