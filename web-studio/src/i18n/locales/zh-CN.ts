const zhCN = {
  appShell: {
    footer: {
      connection: '连接与身份',
    },
    header: {
      defaultTitle: 'OpenViking Studio',
    },
    navigation: {
      home: {
        title: '首页',
      },
      addResource: {
        title: '添加资源',
      },
      fileSystem: {
        title: '文件系统',
      },
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
  home: {
    memoryStats: {
      category: {
        cases: '案例',
        entities: '实体',
        events: '事件',
        patterns: '模式',
        preferences: '偏好',
        profile: '档案',
        skills: '技能',
        tools: '工具',
      },
      subtitle: '记忆分类分布',
      title: '记忆统计',
    },
    recentTasks: {
      empty: '暂无任务',
      subtitle: '后台任务',
      title: '近期任务',
    },
    requestFailed: '请求失败',
    sessions: {
      empty: '暂无会话',
      subtitle: '会话列表',
      title: '会话',
    },
    statCard: {
      memoryTotal: '记忆总数',
      tokenUsage: 'Token 用量',
      vectorCount: '向量数量',
      vectorCountSub: '已索引向量',
      memoryTotalSub: '跨全部分类',
      tokenUsageSub: '累计使用',
    },
    systemHealth: {
      allOperational: '所有系统正常运行',
      close: '关闭',
      dialogDescription: '查看当前组件的详细错误信息。',
      dialogTitle: '错误详情',
      issuesDetected: '检测到异常',
      noDetails: '当前没有返回更详细的错误信息。',
      title: '系统健康',
      viewErrorAria: '查看 {{component}} 的错误详情',
      viewDetails: '查看详情',
      nIssues: '检测到 {{count}} 个异常',
      copyError: '复制错误',
      copied: '已复制',
      statusDetail: '状态详情',
      rawJson: 'Raw JSON',
      operational: '正常',
      degraded: '降级',
      error: '异常',
      healthy: '健康',
      unhealthy: '异常',
      errors: '错误列表',
    },
  },
  operations: {
    page: {
      placeholder: '运维面板能力尚未接入。',
    },
  },
  addResource: {
    title: '添加资源',
    description: '上传本地文件到服务器，文件类型通过 magic bytes 自动检测。',
    dropzone: {
      title: '拖拽文件到此处，或点击选择文件',
      hint: '每次只能上传一个文件。',
      supportedFormats: '支持 PDF、Word、PPTX、Excel、Markdown、代码文件、图片等',
    },
    fileInfo: {
      name: '文件',
      size: '大小',
      type: '类型',
      unknown: '未知类型',
      remove: '移除',
    },
    targetUri: '目标 URI',
    'targetUri.hint': '选择资源的存储位置，默认为 viking://resources/。',
    'targetUri.browse': '浏览',
    advancedOptions: '高级选项',
    upload: '上传文件',
    'upload.progress': '正在上传... {{progress}}%',
    'upload.processing': '文件已上传，正在处理中...',
    uploading: '上传中…',
    result: {
      success: '上传完成！',
      skippedFiles: '{{count}} 个文件被跳过（不支持的格式）',
    },
    continueUpload: '继续上传',
    cancelUpload: '取消',
    success: '资源添加成功',
    fileBlocked: '"{{name}}" 不是支持的文件类型。',
    error: '请求失败',
    dirPicker: {
      title: '选择目录',
      select: '选择',
      cancel: '取消',
      empty: '空目录',
      error: '加载目录失败',
      selected: '已选择：',
    },
    mode: {
      upload: '上传文件',
      remote: '远程资源',
    },
    remoteUrl: '远程资源地址',
    'remoteUrl.placeholder': 'https://github.com/org/repo',
    'remoteUrl.hint': 'HTTP(S) 链接、Git 仓库地址或其他远程资源地址。',
    submit: '添加资源',
    strict: '严格模式',
    'strict.hint': '开启时，服务器会拒绝不支持或无法识别类型的文件，而非静默跳过。',
    directlyUploadMedia: '直接上传媒体文件',
    'directlyUploadMedia.hint': '开启时，媒体文件（图片、音频、视频）原样存储。关闭后，媒体文件会先通过 AI 视觉/音频管道提取内容再存储。',
    reason: '添加原因',
    'reason.placeholder': '为什么要添加这个资源？',
    instruction: '处理指令',
    'instruction.placeholder': '针对该资源的特殊处理指令。',
    directoryScan: {
      title: '目录扫描选项',
      ignoreDirs: '忽略目录',
      'ignoreDirs.placeholder': 'node_modules, .git, __pycache__',
      include: '包含模式',
      'include.placeholder': '*.py, *.md',
      exclude: '排除模式',
      'exclude.placeholder': '*.log, *.tmp',
    },
  },
  resources: {
    page: {
      placeholder: '资源工作区能力尚未接入。',
    },
    searchPalette: {
      placeholder: '搜索... 输入 / 浏览目录',
      scope: {
        global: '搜索范围: 全局',
        current: '搜索范围: {{name}}',
      },
      scopeState: {
        validatingTitle: '正在校验搜索范围',
        validatingPrefix: '正在检查',
        validatingSuffix: '是否存在',
        switchTitle: '切换搜索范围',
        switchPrefix: '按',
        switchMiddle: '切换到',
        invalidTitle: '搜索范围不存在',
        invalidPrefix: '路径',
        invalidSuffix: '无法访问，不能切换',
      },
      empty: {
        title: '语义搜索知识库',
      },
      browseDirHint: {
        before: '输入',
        after: '浏览目录结构',
      },
      globalScopeHint: {
        before: '输入',
        after: '切换搜索范围到全局',
      },
      error: '搜索出错',
      emptyResults: {
        title: '没有找到匹配的内容',
        subtitle: '试试换个关键词？',
      },
      footer: {
        dirMode: {
          select: '选择',
          level: '层级',
          confirm: '确定',
          cancel: '取消',
        },
        resultMode: {
          navigate: '导航',
          open: '打开',
          close: '关闭',
          count: '{{count}} 个结果',
        },
      },
    },
    dirBrowser: {
      back: '返回上一级',
      loading: '正在加载目录',
      filesSection: '文件',
      empty: {
        title: '空目录',
        subtitle: '这一层目前没有可继续展开的子目录',
      },
    },
  },
  sessions: {
    page: {
      placeholder: '会话与 Bot 工作区能力尚未接入。',
    },
    threadList: {
      title: '会话',
      newSession: '新建会话',
    },
    chat: {
      placeholder: '输入消息...',
      emptyState: '选择或创建一个会话开始聊天。',
      thinking: '思考中...',
      toolCall: '工具调用',
      send: '发送',
      cancel: '停止',
    },
  },
} as const

export default zhCN
