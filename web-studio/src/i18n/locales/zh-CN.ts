const zhCN = {
  appShell: {
    footer: {
      connection: '连接与身份',
    },
    header: {
      defaultTitle: 'OpenViking Studio',
    },
    navigation: {
      operations: {
        title: '运维',
      },
      resources: {
        title: '资源',
      },
      sessions: {
        title: '会话',
      },
    },
    sidebar: {
      workspaceGroupLabel: '工作区',
    },
  },
  common: {
    action: {
      cancel: '取消',
      saveConnection: '保存连接',
      showAdvancedIdentityFields: '显示高级身份字段',
    },
    errorBoundary: {
      description: '路由渲染过程中出现未处理异常。可以先重试一次；如果问题持续，查看下方错误信息继续排查。',
      reload: '刷新页面',
      retry: '重试',
      title: '页面发生错误',
    },
    language: {
      current: '当前',
      label: '语言',
    },
    serverMode: {
      checking: '检测中',
      devImplicit: '开发模式',
      explicitAuth: '显式鉴权',
      offline: '未连接',
    },
  },
  connection: {
    devMode: {
      description: '当前服务使用隐式身份，通常不需要填写 account、user 和 API key。',
      title: '已检测到开发模式',
    },
    dialog: {
      title: '连接与身份',
    },
    identitySummary: {
      devImplicit: '服务端隐式身份',
      named: '{{identity}}',
      unset: '未设置身份',
    },
    fields: {
      accountId: {
        label: 'Account',
        placeholder: 'default',
      },
      apiKey: {
        label: 'API Key',
        placeholder: '输入 X-API-Key 或 Bearer token',
      },
      baseUrl: {
        label: '服务地址',
        placeholder: 'http://127.0.0.1:1933',
      },
      credentials: {
        title: '身份与凭证',
      },
      userId: {
        label: 'User',
        placeholder: 'default',
      },
    },
  },
  operations: {
    page: {
      placeholder: '运维面板能力尚未接入。',
    },
  },
  resources: {
    page: {
      placeholder: '资源工作区能力尚未接入。',
    },
  },
  sessions: {
    page: {
      placeholder: '会话工作区能力尚未接入。',
    },
  },
} as const

export default zhCN