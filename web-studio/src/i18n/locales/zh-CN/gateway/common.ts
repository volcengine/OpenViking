const common = {
  title: 'OpenViking 网关',
  description:
    '让任何使用 API Key 的模型客户端都能用上 OpenViking 记忆。把客户端指向网关后，网关会为每条新消息补充相关记忆，并把对话保存回 OpenViking。',
  beta: {
    label: 'Beta',
    hint: '测试版：OpenViking 网关仍在完善，后续版本可能调整设置和接口。',
  },
  tabs: {
    label: 'OpenViking 网关分区',
    overview: '概览',
    upstreams: '上游',
    profiles: '上下文配置',
    keys: '密钥',
    requests: '请求日志',
    connect: '接入',
  },
  address: {
    label: '网关地址',
    copy: '复制网关地址',
  },
  actions: {
    cancel: '取消',
    copy: '复制',
    delete: '删除',
    duplicate: '复制一份',
    edit: '编辑',
    more: '更多操作',
    refresh: '刷新',
    remove: '移除',
    retry: '重试',
    revoke: '吊销',
    save: '保存',
    viewAll: '查看全部',
  },
  states: {
    loading: '加载中…',
    saving: '保存中…',
    updatedAt: '更新于 {{time}}',
    any: '不限',
    on: '开启',
    off: '关闭',
  },
  copy: {
    done: '已复制到剪贴板',
    failed: '复制失败，请选中文本后手动复制。',
  },
  access: {
    title: '需要账号管理员权限',
    description:
      '管理 OpenViking 网关需要本账号的账号管理员密钥或 Root 密钥。请先在连接设置中填写。',
    action: '打开连接设置',
  },
  unavailable: {
    notEnabled: {
      title: 'OpenViking 网关未开启',
      description:
        '这台服务器没有开启 OpenViking 网关。请在 ov.conf 中开启它，重启 OpenViking，再启动网关。',
    },
    tokenMissing: {
      title: '缺少管理令牌',
      description:
        'OpenViking 需要和网关使用同一个管理令牌才能管理网关。请为 OpenViking 和网关设置下面的环境变量（至少 32 个字符），然后重启两者。',
    },
    tokenMismatch: {
      title: '管理令牌不一致',
      description:
        'OpenViking 和网关启动时用的管理令牌不同，所以网关拒绝了 OpenViking 的请求。请把下面的环境变量设成同一个值，然后重启两者。',
    },
    unsupported: {
      title: '这台 OpenViking 服务器无法管理 OpenViking 网关',
      description:
        'Studio 连接的 OpenViking 服务器不支持 OpenViking 网关。请升级 OpenViking Server 并重启，然后重试。',
    },
    devMode: {
      title: 'OpenViking 运行在开发模式',
      description:
        '开发模式下每个密钥都以 Root 身份操作，而网关不接受 Root 密钥，所以签发不了网关密钥。请把 OpenViking 切换到 API 密钥模式并重启，再到连接设置中填写账号管理员密钥。',
    },
    unreachable: {
      title: 'OpenViking 连不上网关',
      description:
        '网关没有运行，或者 ov.conf 里的 gateway.url 指向了错误的地址。请启动网关，并确认 OpenViking 能访问这个地址。',
    },
    failed: {
      title: '无法加载 OpenViking 网关',
    },
    fixLabel: '需要修改的配置',
    terminal: '终端',
    environment: '环境变量',
    docs: '部署说明',
  },
  errors: {
    reasons: {
      not_enabled: '这台服务器没有开启 OpenViking 网关。',
      token_missing: '没有设置管理令牌，OpenViking 无法管理网关。',
      token_mismatch:
        '网关拒绝了 OpenViking 的管理令牌，两者必须使用同一个令牌。',
      unreachable: 'OpenViking 连不上网关。',
      unsupported: '这台 OpenViking 服务器不支持 OpenViking 网关。',
      conflict: '这项修改与现有配置冲突。',
      invalid: '网关拒绝了这些设置，请检查填写的值后重试。',
      forbidden: '没有执行此操作的权限。',
      not_found: '该项已不存在，请刷新后重试。',
      unauthorized: '密钥被拒绝，请检查连接设置。',
      other: '操作失败，请重试。',
    },
    inUse: '仍有密钥在使用它，请先吊销这些密钥。',
    invalidSettings: '网关拒绝了这些设置，请检查各项取值和范围。',
    invalidKeyRequest: '网关拒绝了这个密钥，请检查填写内容后重试。',
    unknownProfile: '所选的上下文配置已不存在。',
    unknownUpstream: '所选的上游中有一个已不存在。',
    keyNotFound: '这段对话使用的网关密钥已被吊销，无法再重新同步。',
    sessionNotFound: '网关中已没有这段对话的记录。',
    upstreamKeyMissing:
      '没有可用于测试的已保存 API Key：这个上游由客户端自带 API Key，或者还没有填写 API Key。',
    subscriptionKey: '不支持 Claude 订阅登录，请使用模型 API Key。',
  },
  validation: {
    required: '必填',
    number: '请输入数字',
    integer: '请输入整数',
    min: '不能小于 {{min}}',
    max: '不能大于 {{max}}',
    range: '取值范围为 {{min}} 到 {{max}}',
    rangeExclusive: '须大于 {{min}}，且不超过 {{max}}',
    greaterThan: '须大于 {{min}}',
    selectOneSource: '至少选择一个来源',
    unknownCategory: '未知分类“{{name}}”',
    quotasAllZero: '至少把一个分类设为大于 0，或关闭分类限额',
    softBelowHard: '须低于硬提醒时机',
    baseUrl:
      '请输入 http:// 或 https:// 地址，不能包含账号密码、查询参数或片段',
    subscriptionKey:
      '不支持 Claude 订阅令牌（sk-ant-oat…），请使用模型 API Key。',
    apiKeyRequired: '请填写 API Key，或改为由每个客户端自带 API Key',
    headerInvalid: '请求头“{{name}}”的名称或取值无效',
    headerReserved: '“{{name}}”由网关设置，不能修改',
    headerValue: '请填写“{{name}}”的取值',
    aliasTarget: '请填写“{{name}}”对应的上游模型',
    contextWindow: '“{{name}}”的上下文窗口至少为 {{min}} Token',
    protocolUnsupported:
      '$t(enums.vendor.{{vendor}}) 不提供 $t(enums.protocol.{{protocol}}) 接口。请换一种协议，或者把服务商改成“$t(enums.vendor.generic)”。',
  },
  units: {
    bytes: '字节',
    characters: '字符',
    entries: '条',
    messages: '条消息',
    rounds: '轮',
    seconds: '秒',
    tokens: 'Token',
  },
  field: {
    default: '默认值：{{value}}',
    notSet: '未设置',
    advanced: '高级设置',
    sectionInvalid: '这一部分有设置需要修改，改好后才能保存。',
    storedSecret: '已保存，留空则保持不变',
  },
  keyValue: {
    add: '添加',
    remove: '移除',
    duplicate: '名称重复，只保留最后一项',
  },
  tags: {
    remove: '移除 {{value}}',
    suggestions: '建议',
  },
}

export default common
