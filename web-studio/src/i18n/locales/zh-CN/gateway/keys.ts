const keys = {
  description:
    '每个密钥属于一个 OpenViking 用户，使用一份上下文配置和一组上游。密钥不能修改，需要调整时请签发新密钥并吊销旧密钥。',
  issue: '签发密钥',
  loadFailed: '无法加载网关密钥',
  prerequisites: {
    upstream: '签发密钥前，请先添加上游。',
    profile: '签发密钥前，请先创建上下文配置。',
    both: '签发密钥前，请先添加上游并创建上下文配置。',
    addUpstream: '添加上游',
    createProfile: '创建上下文配置',
  },
  empty: {
    title: '还没有网关密钥',
    description:
      '客户端用网关密钥代替模型 API Key。用它发出的请求会转发到已配置的上游，并读取和保存某个 OpenViking 用户的记忆。建议每个人、每个客户端各用一个密钥，便于单独吊销。',
  },
  table: {
    name: '名称',
    key: '密钥',
    user: 'OpenViking 用户',
    profile: '上下文配置',
    upstreams: '上游',
    models: '模型',
    created: '创建时间',
    actions: '操作',
  },
  missing: '已删除',
  missingProfile: '这个上下文配置已不存在',
  missingUpstream: '这个上游已不存在',
  extra: '+{{count}}',
  actions: {
    revokeKey: '吊销“{{name}}”',
    more: '“{{name}}”的更多操作',
    deleteUserData: '删除该用户的网关数据…',
  },
  revoke: {
    title: '吊销“{{name}}”？',
    description:
      '使用这个密钥的客户端会立即失去访问权限。对话和记忆都会保留，为同一用户签发新密钥后可以接着使用。尚未保存到 OpenViking 的内容（例如最新一条回复）只有在用新密钥继续这段对话时才会保存。',
    confirm: '吊销密钥',
    done: '已吊销“{{name}}”',
  },
  deleteUser: {
    title: '删除 {{user}} 的网关数据？',
    description_one:
      '这会吊销 {{user}} 的网关密钥，并删除网关为该用户保存的对话状态，其中包括尚未保存到 OpenViking 的对话内容。该用户的客户端会立即失去访问权限。已经存入 OpenViking 的记忆不受影响。',
    description_other:
      '这会吊销 {{user}} 的全部 {{count}} 个网关密钥，并删除网关为该用户保存的对话状态，其中包括尚未保存到 OpenViking 的对话内容。该用户的客户端会立即失去访问权限。已经存入 OpenViking 的记忆不受影响。',
    confirm: '删除网关数据',
    done: '已删除 {{user}} 的网关数据',
  },
  form: {
    title: '签发网关密钥',
    description: '密钥签发后立即可用。完整密钥只在签发成功后显示一次。',
    name: {
      label: '名称',
      placeholder: '例如：Alice · Claude Code',
      description: '用来区分不同的密钥，写上使用者和客户端最清楚。',
    },
    user: {
      label: 'OpenViking 用户',
      placeholder: '选择 OpenViking 用户',
      loading: '正在加载用户…',
      description:
        '网关会以这个用户的身份召回和保存记忆。该用户的 OpenViking 密钥在服务端读取，由网关加密保存，不会显示。',
      roles: {
        admin: '管理员',
        user: '用户',
      },
      unavailable: '服务端读不到这个用户的密钥，请改为粘贴',
      loadFailed: '无法加载本账号的用户，请直接粘贴密钥。',
      none: '服务端读不到本账号用户的密钥，请直接粘贴。',
      root: '使用 Root 密钥时无法在这里选择用户，请直接粘贴密钥。',
      choose: '选择用户',
    },
    openvikingKey: {
      label: 'OpenViking 密钥',
      description:
        '本账号中某个 OpenViking 用户的密钥。网关会以这个用户的身份召回和保存记忆。不接受 Root 密钥。密钥加密保存，之后不再显示。',
      gatewayKey: '这是网关密钥，请填写 OpenViking 用户自己的密钥。',
      paste: '粘贴 OpenViking 密钥',
    },
    profile: {
      label: '上下文配置',
      placeholder: '选择上下文配置',
      description: '决定使用这个密钥的对话如何召回和保存记忆。',
      empty: '还没有上下文配置。',
      create: '去创建',
    },
    upstreams: {
      label: '上游',
      description:
        '每个请求到达时，网关会从所选上游中挑一个协议与客户端一致、并且提供所请求模型的上游。',
      required: '至少选择一个上游',
      empty: '还没有上游。',
      create: '去添加',
      off: '已停用',
    },
    models: {
      label: '允许的模型',
      optional: '可选',
      placeholder: '输入模型名称后按回车',
      description: '留空则允许这些上游提供的全部模型。',
    },
    submit: '签发密钥',
  },
  errors: {
    title: '无法签发密钥',
    rootKey: '这是 Root 密钥。请改用本账号中某个 OpenViking 用户的密钥。',
    otherAccount: '这个 OpenViking 密钥属于其他账号，请使用本账号的用户密钥。',
    invalidKey:
      'OpenViking 拒绝了这个密钥。请检查密钥是否完整，以及是否已被重新生成。',
    unavailable: '网关连不上 OpenViking，无法校验这个密钥。请稍后重试。',
    versionMismatch:
      'OpenViking 版本低于网关的要求。请升级 OpenViking 后重试。',
    unknownUser: '这个用户已不在本账号中，请重新选择。',
    userKeyUnreadable: '服务端读不到这个用户的 OpenViking 密钥，请改为粘贴。',
  },
  secret: {
    title: '复制网关密钥',
    once: '完整密钥只显示这一次。',
    copyKey: '复制网关密钥',
    user: 'OpenViking 用户',
    profile: '上下文配置',
    connectTitle: '接入客户端',
    clients: {
      'claude-code': {
        intro: '在启动 Claude Code 的终端里设置这些环境变量。',
      },
      codex: {
        intro: '在 Codex 配置文件中把网关添加为服务商，再在终端里导出密钥。',
      },
      chat: {
        intro:
          '在任意兼容 OpenAI 的客户端或 SDK 中使用这些设置。同一段对话的每条消息都带上相同的 X-OpenViking-Session。',
      },
    },
    noProtocol: '这个密钥没有已启用的 {{protocol}} 上游，{{client}} 无法使用。',
    moreClients: '其他客户端和详细说明',
    done: '我已复制',
  },
}

export default keys
