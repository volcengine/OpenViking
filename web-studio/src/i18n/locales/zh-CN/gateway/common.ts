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
    view: '查看',
    back: '返回',
    viewAll: '查看全部',
    viewKeys: '查看密钥',
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
    title: '先连接 OpenViking',
    invalid: 'OpenViking API 密钥无效',
    description:
      '网关用你的 OpenViking API 密钥确认你是谁。请先在连接设置中填写它。',
    invalidDescription:
      'OpenViking 拒绝了连接设置中的 API 密钥，它可能已被重新生成或删除。请换一个有效的密钥。',
    action: '打开连接设置',
  },
  management: {
    title: '输入网关管理密钥',
    label: '网关管理密钥',
    description:
      '网关管理密钥由部署这台网关的人设置，用来修改上游和上下文配置。它不是 OpenViking API 密钥，也不是客户端用的网关密钥。',
    where: '在哪里找？',
    prefix:
      'openviking-server init 和 openviking-gateway init 生成的值以 ovgm_ 开头。',
    environment: '也可能设在环境变量里，它优先于配置文件：',
    keyPlaceholder: '<至少 32 个字符>',
    session: '只保存在当前浏览器标签页，关闭后自动锁定。',
    wrongKey: '这是网关密钥（给客户端用的），请输入网关管理密钥。',
    invalid:
      '网关管理密钥不正确。请向部署这台网关的人确认。注意，这里要填的不是 OpenViking 的管理员 API 密钥。',
    unlock: '解锁',
    unlocked: '已解锁，现在可以修改上游和上下文配置',
    lock: '锁定',
    editing: '可修改上游和上下文配置',
    expired: '网关管理密钥已失效，上游和上下文配置恢复为只读。',
    readOnly:
      '上游和上下文配置由网关管理员统一提供。你可以查看、测试连接，并在签发密钥时选用。',
    adminReadOnly: 'OpenViking 的管理员和 Root 也需要网关管理密钥才能修改。',
    shared:
      '你正在修改本账号所有用户共用的配置，保存后立即对使用它的密钥生效。',
    publicTitle: '这台网关对所有 OpenViking 商业版用户开放',
    publicDescription:
      '它连接的是公共 OpenViking 服务，又没有设置网关管理密钥，所以任何持有商业版 API 密钥的人都能登录这台网关、用它转发模型请求，还能修改自己账号下的上游。如果这台网关只给你自己或团队使用，请设置网关管理密钥，然后重启网关。',
    generate: '可以用这条命令生成密钥：',
    dismiss: '关闭提示',
  },
  unavailable: {
    notEnabled: {
      title: '这台 OpenViking 没有开启网关',
      description:
        '运行 openviking-server init 并选择 Update gateway；也可以在 ov.conf 中加入下面的配置，然后重启 OpenViking。',
    },
    contact: '请联系部署 OpenViking 的人开启网关。',
    trusted: {
      title: '网关不支持 Trusted 模式',
      description:
        '网关要靠 API 密钥识别每个用户。请在 ov.conf 中把 server.auth_mode 改为 api_key，然后重启 OpenViking。',
    },
    openvikingUnreachable: {
      title: '网关连不上 OpenViking（{{url}}）',
      description:
        '确认 OpenViking 正在运行，并检查网关的 openviking_url（gateway.conf 或 OPENVIKING_GATEWAY_OPENVIKING_URL）是否指向它，然后重试。',
    },
    failed: {
      title: '无法加载 OpenViking 网关',
    },
    fixLabel: '需要修改的配置',
    terminal: '终端',
    environment: '环境变量',
    docs: '部署说明',
    enable: {
      description: 'Studio 可以直接开启：把设置写入 ov.conf，然后重启服务。',
      descriptionManualRestart:
        'Studio 可以把设置写入 ov.conf。这台服务不能从 Studio 重启，保存后请自行重启。',
      action: '开启网关',
      confirmTitle: '开启网关？',
      confirmRestart:
        'Studio 会在 ov.conf 中写入 gateway.enabled: true，然后重启服务。重启期间请求会中断几秒。',
      confirmManualRestart:
        'Studio 会在 ov.conf 中写入 gateway.enabled: true。重启服务后网关才会开启。',
      protect: '只有持有网关管理密钥的人才能修改上游和上下文配置',
      protectHint:
        'Studio 会生成密钥，写入 ov.conf 的 gateway.management_key，保存后只显示一次。',
      checking: '正在读取 ov.conf…',
      saving: '正在保存 ov.conf…',
      restarting: '正在保存 ov.conf 并重启服务…',
      savedRestart:
        '已保存到 ov.conf。重启服务后网关才会开启，重启完成后点“重试”。',
      readOnly:
        '这台服务的 ov.conf 是只读的（例如由 Helm ConfigMap 挂载），Studio 改不了它。请在部署配置中开启网关，然后重启服务：',
      helm: 'Helm values',
      stillOff:
        '服务已重启，但网关仍未开启。请检查 OPENVIKING_GATEWAY_ENABLED 是否设成了 false 之类的值，它会覆盖 ov.conf。',
      rolledBack:
        '服务拒绝了新配置，继续使用原来的配置，所以网关仍未开启。原因请查看服务日志。',
      conflict: 'ov.conf 在页面加载后被改过，请重新读取后再试。',
      reload: '重新读取 ov.conf',
      failed: '开启网关失败：{{message}}',
      restartFailed:
        'ov.conf 已保存，但无法确认服务是否重启成功：{{message}}。请检查服务状态，然后点“重试”。',
      keyTitle: '保存网关管理密钥',
      keyOnce:
        '密钥只显示这一次。修改上游和上下文配置时需要它；关闭这个浏览器标签页之前，当前页面保持解锁。',
      copyKey: '复制网关管理密钥',
      copyFailed: '复制失败，密钥已选中，请按 Ctrl+C 或 ⌘C 复制。',
      keyOnceManual:
        '密钥只显示这一次。修改上游和上下文配置时需要它；重启服务后，在网关页面点“输入网关管理密钥”并粘贴。',
      keySaved: '已保存',
      continue: '已保存，打开网关',
    },
  },
  errors: {
    reasons: {
      management_key_required:
        '上游和上下文配置由网关管理员统一提供，输入网关管理密钥后才能修改。',
      management_key_invalid: '网关管理密钥不正确，请向部署这台网关的人确认。',
      vlm_import_forbidden: '你没有导入 OpenViking 模型配置的权限。',
      vlm_import_unavailable:
        '这台网关不在 OpenViking 服务内运行，读不到服务端的模型配置，请手动添加上游。',
      in_use_one: '仍有 {{count}} 个密钥在使用它。',
      in_use_other: '仍有 {{count}} 个密钥在使用它。',
      upstream_key_missing:
        '这个上游没有保存 API Key，或者由客户端自带 API Key，网关无法发起测试。',
      trusted_mode_unsupported: '网关不支持 Trusted 模式。',

      not_enabled: '这台 OpenViking 没有开启网关。',
      conflict: '这项修改与现有配置冲突。',
      invalid: '网关拒绝了这些设置，请检查填写的值后重试。',
      forbidden: '没有执行此操作的权限。',
      not_found: '该项已不存在，请刷新后重试。',
      unauthorized: 'OpenViking API 密钥被拒绝，请检查连接设置。',
      other: '操作失败，请重试。',
    },
    inUse: '仍有密钥在使用它，请先吊销这些密钥。',
    invalidSettings: '网关拒绝了这些设置，请检查各项取值和范围。',
    invalidKeyRequest: '网关拒绝了这个密钥，请检查填写内容后重试。',
    unknownProfile: '所选的上下文配置已不存在。',
    unknownUpstream: '所选的上游中有一个已不存在。',
    keyNotFound: '这段对话使用的网关密钥已被吊销，无法再重新同步。',
    sessionNotFound: '网关中已没有这段对话的记录。',
    subscriptionKey: '不支持 Claude 订阅登录，请使用模型 API Key。',
  },
  validation: {
    anthropicBaseUrl: '去掉末尾的 /v1 才能保存',
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
    noLimit: '不限',
    advanced: '高级设置',
    sectionInvalid: '这一部分有设置需要修改，改好后才能保存。',
    storedSecret: '已保存，留空则保持不变',
  },
  keyValue: {
    add: '添加',
    remove: '移除',
    none: '未设置',
    duplicate: '名称重复，只保留最后一项',
  },
  tags: {
    remove: '移除 {{value}}',
    suggestions: '建议',
  },
}

export default common
