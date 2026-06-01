const zhCN = {
  appShell: {
    footer: {
      connection: '连接与身份',
      docs: '文档站',
      github: 'GitHub',
    },
    header: {
      defaultTitle: 'OpenViking Studio',
    },
    navigation: {
      home: {
        title: '首页',
      },
      oauthSetup: {
        title: 'OAuth 设置',
      },
      operations: {
        title: '运维',
      },
      requestLogs: {
        title: '请求日志',
      },
      resources: {
        title: '上下文管理',
      },
      retrieval: {
        title: '检索',
      },
      sessions: {
        title: '会话',
      },
      studio: {
        title: '实验场',
      },
    },
    sidebar: {
      loadingSessions: '加载中...',
      noSessions: '暂无会话',
      workspaceGroupLabel: 'OpenViking Studio',
    },
  },
  common: {
    action: {
      cancel: '取消',
      saveConnection: '保存连接',
      showAdvancedIdentityFields: '显示高级身份字段',
    },
    errorBoundary: {
      description:
        '路由渲染过程中出现未处理异常。可以先重试一次；如果问题持续，查看下方错误信息继续排查。',
      reload: '刷新页面',
      retry: '重试',
      title: '页面发生错误',
    },
    language: {
      current: '当前',
      label: '语言',
    },
    theme: {
      toggle: '切换主题',
    },
  },
  connection: {
    devMode: {
      description:
        '当前服务会自动提供身份，通常不需要填写 account、user 和 API key。',
      title: '服务端托管身份',
    },
    dialog: {
      title: '连接与身份',
    },
    identitySummary: {
      dev: '服务端隐式身份',
      named: '{{identity}}',
      unset: '未设置身份',
    },
    fields: {
      accountId: {
        label: 'Account',
        placeholder: 'default',
      },
      agentId: {
        label: 'Agent',
        placeholder: 'web-studio',
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
    oauthOtp: {
      title: 'OAuth 客户端 OTP',
      description:
        '生成一个短期一次性码，让 MCP 客户端凭此以所选身份完成 OAuth 授权。',
      generate: '生成 OTP',
      regenerate: '重新生成',
      copy: '复制',
      copied: '已复制',
      codeLabel: '一次性码',
      expiresIn: '{{seconds}} 秒后失效',
      expired: '已过期 —— 请重新生成。',
      generateError: '生成 OTP 失败：{{message}}',
    },
  },
  settings: {
    actions: {
      addAccount: '新增 account',
      addUser: '新增 user',
      cancel: '取消',
      copy: '复制',
      refresh: '刷新',
      regenerate: '重新生成',
      save: '保存',
      use: '使用',
    },
    connection: {
      adminError: '加载 admin 身份失败：{{message}}',
      description:
        '选择 Studio 调用 OpenViking API 时要携带的 account 和 user。',
      noKey:
        '输入具备 admin 权限的 API key 后，可以加载 account 和 user 可选项。',
      title: '连接设置',
    },
    dialogs: {
      addAccount: {
        description:
          '创建一个工作区 account 和第一个 admin user。新 key 只会在创建后展示一次。',
        title: '新增 account',
      },
      addUser: {
        description:
          '在已有 account 下注册 user。生成的 key 只会在创建后展示一次。',
        title: '新增 user',
      },
      regenerate: {
        description:
          '要重新生成 {{account}} / {{user}} 的 API key 吗？当前 key 会立即失效。',
        title: '重新生成 API key？',
      },
    },
    empty: {
      adminDescription:
        '使用 root 或 account admin API key 后，可以列出用户、复制 key、新增身份或轮换凭证。',
      adminTitle: '需要 admin 权限',
      usersDescription: '创建一个 user 来生成第一个 API key。',
      usersTitle: '当前 account 下没有 user',
    },
    fields: {
      account: 'Account',
      adminUser: 'Admin user',
      agent: 'Agent',
      apiKey: 'API key',
      baseUrl: '服务地址',
      role: '角色',
      user: 'User',
    },
    keyResult: {
      description:
        '请现在复制保存。离开当前状态后，OpenViking 可能只展示前缀。',
      dismiss: '收起',
      title: '新的 API key',
    },
    loading: '正在加载身份...',
    management: {
      accountFilter: '管理的 account',
      description:
        '查看某个 account 下的 users 和凭证，并在网页端新增 user 或轮换 key。',
      title: '用户管理',
    },
    page: {
      description:
        '在 Studio 中配置当前 OpenViking 身份，并管理 accounts、users 和 API keys。',
      title: '连接与身份',
    },
    placeholders: {
      account: 'team-account',
      agent: 'web-studio',
      apiKey: '输入 X-API-Key 或 Bearer token',
      baseUrl: 'http://127.0.0.1:1933',
      user: 'default',
    },
    roles: {
      admin: 'Admin',
      user: 'User',
    },
    serverMode: {
      api_key: 'API key 模式',
      checking: '检查中...',
      dev: '开发模式',
      offline: '离线',
      trusted: 'Trusted 模式',
    },
    stats: {
      accounts: 'Accounts 总数',
      apiKeys: '可见 API keys',
      users: 'Users',
    },
    table: {
      account: 'Account',
      actions: '操作',
      apiKey: 'API key',
      role: '角色',
      user: 'User',
    },
    toast: {
      accountCreated: 'Account 已创建',
      connectionSaved: '连接已保存',
      copied: '已复制',
      keyRegenerated: 'API key 已重新生成',
      userCreated: 'User 已创建',
    },
  },
  oauthSetup: {
    page: {
      title: 'OAuth 设置',
      intro:
        '当你需要让 MCP 客户端（例如 Claude.ai / Claude Desktop / ChatGPT / Cursor）通过 OAuth 完成鉴权时使用本页。在这里生成一个短期 OTP，粘到 MCP 客户端，它就会用所选身份完成 OAuth 授权。',
      docsLink: '阅读 OAuth 接入指南',
    },
  },
  home: {
    agentAccess: {
      description:
        '去重统计今日访问过 OpenViking 的 Agent，并展示最近访问时间。',
      empty: '今日暂无 Agent 访问',
      title: 'Agent 访问数',
    },
    contextCommits: {
      description:
        '按 4 小时聚合资源、技能、会话消息和提交写入，鼠标悬停可查看明细。',
      empty: '过去一年暂无上下文提交',
      hourRange: '{{start}}-{{end}}',
      legend: {
        high: '高',
        intense: '密集',
        low: '低',
        medium: '中',
        more: '多',
        none: '少',
        title: '提交强度',
      },
      operations: {
        addResource: '资源写入',
        addSkill: '技能写入',
        sessionAddMessage: '会话消息',
        sessionCommit: '会话提交',
      },
      stats: {
        activeDays: '活跃天数',
        peakDay: '峰值单日',
        recentDay: '最近提交',
      },
      title: '上下文提交统计',
      yearlyEmpty: '暂无上下文提交',
      yearlyTotal: '{{count}} 次上下文提交',
      tooltip: {
        total: '总提交',
      },
    },
    contextData: {
      description:
        '包含文件、技能、用户记忆与 Agent 记忆，用于衡量当前上下文资源规模。',
      files: '文件',
      memories: '记忆',
      skills: '技能',
      title: '上下文数据量',
    },
    menuIntro: {
      description:
        '左侧导航可折叠；常用入口包括总览、上下文管理、目录递归检索、请求日志、设置、GitHub 和文档站。',
      items: {
        github: {
          description: '打开 OpenViking 源码仓库。',
          title: 'GitHub',
        },
        overview: {
          description: '查看上下文规模与使用概览。',
          title: '总览',
        },
        playground: {
          description: '打开文档站和 Playground 入口。',
          title: 'Playground',
        },
        requestLogs: {
          description: '查看 Studio 发出的请求、状态与耗时。',
          title: '请求日志',
        },
        resources: {
          description: '管理文件、技能和上下文目录。',
          title: '上下文管理',
        },
        retrieval: {
          description: '使用 find() 与 search() 做语义检索。',
          title: '目录递归检索',
        },
        settings: {
          description: '配置服务地址、身份和 API Key。',
          title: '设置',
        },
      },
      title: 'Overview + 整体菜单介绍',
    },
    page: {
      description:
        '按产品需求对齐首页内容：菜单入口、上下文数据量、今日 tokens、今日检索、Agent 访问、tokens 趋势和上下文提交统计。',
      eyebrow: 'OpenViking Studio',
      settings: '连接与设置',
      title: 'Overview',
    },
    requestFailed: '请求失败',
    todayRetrievals: {
      description:
        '展示用户或 Agent 今日使用语义检索 find() 和 search() 的成功调用次数，每天零点刷新。',
      find: 'find',
      search: 'search',
      title: '今日检索次数',
    },
    todayTokens: {
      description: '展示今日实时 token 消耗，每天零点刷新。',
      embeddingInput: 'Embedding input tokens',
      title: '今日 Tokens 消耗',
      vlmInput: 'VLM input tokens',
      vlmOutput: 'VLM output tokens',
    },
    tokenTrend: {
      description:
        '展示最近 14 天每日 token 消耗，包含 VLM 输入、VLM 输出和 Embedding 输入。',
      empty: '最近 14 天暂无 token 消耗',
      title: 'tokens 总消耗统计',
    },
    usageDisabled: 'Usage/Audit 未初始化，暂无实时统计。',
  },
  operations: {
    page: {
      placeholder: '运维面板能力尚未接入。',
    },
  },
  requestLogs: {
    clear: '清空',
    description: '查看服务端审计到的 API 请求，包括状态、耗时和请求标识。',
    disabled: {
      description: 'Usage/Audit 未初始化，暂无服务端请求日志。',
      title: '审计日志不可用',
    },
    empty: {
      description: '先开始您的第一次可审计调用吧！',
      filteredDescription: '调整搜索内容或状态筛选，扩大可见日志范围。',
      filteredTitle: '没有匹配的请求',
      title: '当前无日志信息',
      upload: '上传文件',
    },
    error: {
      description: '无法从服务端加载审计请求日志。',
      title: '请求失败',
    },
    eyebrow: 'Studio 遥测',
    filters: {
      all: '所有日志',
      apiTypePlaceholder: 'API 类型',
      error: '错误日志',
      requestIdPlaceholder: '精确 Request ID',
      statusCodePlaceholder: '状态码',
    },
    loading: '正在加载请求日志...',
    metrics: {
      successRate: '成功率',
      total: '总调用次数',
    },
    pagination: {
      next: '下一页',
      pageSize: '每页条数',
      pageSizeValue: '每页 {{count}} 条',
      previous: '上一页',
      summary: '共 {{total}} 条，第 {{page}} / {{pageCount}} 页',
    },
    query: '查询',
    refresh: '刷新',
    reset: '重置',
    searchPlaceholder: '筛选方法、路径或状态码',
    status: {
      error: 'ERR',
      pending: 'PENDING',
      success: 'OK',
    },
    table: {
      accountId: 'Account ID',
      apiType: 'API 类型',
      duration: '耗时',
      method: '方法',
      path: '路径',
      requestId: 'Request ID',
      status: '状态',
      time: '时间',
      title: '捕获的请求',
      userId: 'User ID',
    },
    title: '请求日志',
  },
  addResource: {
    title: '添加资源',
    description: '上传本地文件到服务器，文件类型通过 magic bytes 自动检测。',
    dropzone: {
      title: '拖拽文件到此处，或点击选择文件',
      hint: '每次最多上传 10 个文件。',
      supportedFormats:
        '支持 PDF、Word、PPTX、Excel、Markdown、代码文件、图片等',
    },
    fileInfo: {
      name: '文件',
      size: '大小',
      type: '类型',
      unknown: '未知类型',
      remove: '移除',
    },
    targetUri: '目标 URI',
    'targetUri.placeholder': 'viking://resources/',
    'targetUri.hint': '选择资源的存储位置，默认为 viking://resources/。',
    'targetUri.browse': '浏览',
    advancedOptions: '高级选项',
    upload: '上传文件',
    'upload.processing': '文件已上传，正在处理中...',
    uploading: '上传中…',
    result: {
      success: '上传完成！',
      skippedFiles: '{{count}} 个文件被跳过（不支持的格式）',
    },
    cancelUpload: '取消',
    startProcessing: '开始处理',
    success: '资源添加成功',
    fileBlocked: '"{{name}}" 不是支持的文件类型。',
    fileTooLarge: '"{{name}}" 超过 {{size}} 文件大小限制。',
    tooManyFiles: '仅保留前 {{count}} 个文件，其余已忽略。',
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
    strict: '严格模式',
    'strict.hint':
      '开启时，服务器会拒绝不支持或无法识别类型的文件，而非静默跳过。',
    directlyUploadMedia: '直接上传媒体文件',
    'directlyUploadMedia.hint':
      '开启时，媒体文件（图片、音频、视频）原样存储。关闭后，媒体文件会先通过 AI 视觉/音频管道提取内容再存储。',
    createParent: '自动创建父文件夹',
    'createParent.hint':
      '开启时，若目标父目录不存在则自动创建。',
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
    toolbar: {
      parent: '返回父级',
      refresh: '刷新目录',
      search: '搜索 ⌘K',
      processingTasks: '文件处理任务',
      upload: '上传',
    },
    emptyState: {
      title: '您的上下文空间还是空的',
      upload: '上传文件',
    },
    uploadDialog: {
      title: '上传',
      description: '添加本地文件或远程资源到当前上下文资源库。',
    },
    processingNotice: {
      prefix: '文件正在处理中，点击',
      action: '文件处理任务',
      suffix: '查看处理进度与结果。',
    },
    processingTasks: {
      title: '文件处理任务',
      empty: '暂无处理任务',
      toggleError: '展开或收起错误详情',
      columns: {
        fileName: '文件名',
        status: '状态',
        size: '大小',
      },
      status: {
        processing: '处理中',
        success: '处理成功',
        failed: '处理失败',
      },
    },
    searchPalette: {
      ariaLabel: '搜索',
      openContainingDirectory: '打开所在目录',
      placeholder: '搜索',
      scope: {
        global: '搜索范围: 全局',
        current: '搜索范围: {{name}}',
        resetToGlobal: '点击重置为全局搜索',
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
        title: '搜索文件和目录',
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
        title: '没有找到匹配的文件或目录',
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
      error: '加载目录失败',
      empty: {
        title: '空目录',
        subtitle: '这一层目前没有可继续展开的子目录',
      },
    },
    fileList: {
      empty: '当前目录为空',
    },
    filePreview: {
      cancel: '取消',
      edit: '编辑',
      emptyFile: '(空文件)',
      emptyPrompt: '选择文件后在这里预览',
      imageFailed: '图片加载失败。',
      imageLoading: '正在加载图片...',
      largeFileSkipped: '文件较大，默认不自动加载。',
      loadingContent: '正在读取内容...',
      loadingEditor: '加载编辑器...',
      markdownPreview: '预览',
      markdownSource: '源码',
      save: '保存',
      unsupportedBinary: '二进制文件不支持文本预览。',
    },
    fileTree: {
      collapse: '收起',
      expand: '展开',
      loading: '加载中...',
    },
    findResults: {
      collapse: '收起',
      expandDetails: '展开详情',
      groups: {
        memories: '记忆',
        resources: '资源',
        skills: '技能',
      },
      noResults: '未找到相关结果',
    },
  },
  retrieval: {
    title: '检索',
    searchPlaceholder: '输入检索内容',
    send: '检索',
    controls: {
      function: '检索函数',
      modes: {
        find: 'find',
        search: 'search',
      },
      resultCount: '返回数量',
      path: '路径',
      pathPlaceholder: '/',
      scope: '检索范围',
      customScope: '自定义范围',
      customScopePlaceholder: 'resources/project 或 viking://...',
      effectiveScope: '范围',
      allContexts: '全部上下文',
      scopes: {
        all: {
          label: '全部上下文',
        },
        resources: {
          label: '资源库',
        },
        custom: {
          label: '自定义 URI',
        },
      },
      sessionId: 'Session ID',
      sessionPlaceholder: 'session_id（可选）',
    },
    results: {
      title: '检索结果',
      topN: '检索结果（Top{{count}}）',
    },
    types: {
      resource: 'Resources',
      memory: 'Memories',
      skill: 'Skills',
    },
    queryPlan: {
      title: '查询计划 {{count}} 条',
      more: '+{{count}} 条',
    },
    loading: {
      vector: '正在检索向量索引...',
      scan: '扫描知识库层级结构...',
      match: '匹配语义相关内容...',
      rerank: '对结果重排序...',
    },
    empty: {
      checking: '正在检查可检索上下文...',
      readyTitle: '已有可检索上下文',
      readyDescription: '输入关键词后按 Enter 开始检索',
      title: '当前还没有可检索的上下文',
      description: '先上传您的第一份资源吧～',
      upload: '上传文件',
    },
    error: '检索出错',
    noResults: {
      title: '没有找到匹配的内容',
      subtitle: '试试换个关键词或调整路径范围',
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
      copy: '复制',
      emptyDescription: '探索你的知识库，开始一段对话。',
      placeholder: '输入消息...',
      emptyState: '选择或创建一个会话开始聊天。',
      thinking: '思考中...',
      reasoning: '思考过程',
      iteration: '第 {{count}} 轮',
      toolCall: '工具调用',
      toolInput: '输入',
      toolResult: '结果',
      toolStatus: {
        completed: '完成',
        failed: '失败',
        running: '执行中...',
      },
      send: '发送',
      cancel: '停止',
    },
    empty: {
      description: '从侧边栏选择一个会话，或创建新会话。',
      title: '未选择会话',
    },
  },
  oauth: {
    identityPicker: {
      useCurrent: '以当前身份授权',
      noCurrent:
        '尚未配置身份。请先在“连接与身份”中登录，或在下方临时粘贴一个 API key。',
      useCustom: '使用其他 API key',
      customKeyLabel: 'API key',
      customKeyPlaceholder: '粘贴一个 API key（不会持久化）',
    },
    consent: {
      title: '授权 {{clientName}}',
      loading: '正在加载授权请求…',
      expired: '此次授权已过期或不再有效，请从 MCP 客户端重新发起。',
      missingPending: '缺少授权 ID，请打开 MCP 客户端给出的链接。',
      requestSummary: '{{clientName}} 请求访问你的 OpenViking 工作区。',
      redirectLabel: '回跳地址',
      scopesLabel: '权限范围',
      scopesNone: '（无）',
      signInRequired:
        '请先在“连接与身份”中登录 OpenViking Studio，或在下方临时粘贴 API key 完成授权。',
      openConnectionDialog: '打开连接与身份',
      authorize: '授权',
      deny: '拒绝',
      useAnotherDevice: '在另一台设备上授权 →',
      waitingRedirect: '已授权——正在回跳到客户端…',
      verifying: '正在验证…',
      denying: '正在拒绝…',
      denied: '已拒绝，可以关闭此页。',
      verifyError: '授权失败：{{message}}',
      noApiKey: '没有可用的 API key。请选择一个身份或粘贴 key。',
    },
    verify: {
      title: '跨设备验证',
      description: '请输入发起 MCP 客户端登录的那台设备上显示的 6 位验证码。',
      codeLabel: '验证码',
      codePlaceholder: '6 位验证码',
      submit: '授权',
      success: '已为 {{clientName}} 授权，可以关闭此页并回到原设备。',
      successUnknownClient: '已授权，可以关闭此页并回到原设备。',
      verifyError: '授权失败：{{message}}',
      noApiKey: '没有可用的 API key。请选择一个身份或粘贴 key。',
      signInRequired:
        '请先在“连接与身份”中登录 OpenViking Studio，或在下方临时粘贴 API key 完成授权。',
    },
  },
  studio: {
    copyUri: '复制当前 URI',
    copied: '已复制 URI',
    resizeContext: '调整上下文目录宽度',
    resizeAction: '调整 Terminal 和 Agent 宽度',
    readFailed: '无法读取 {{uri}}',
    tabs: {
      terminal: '终端',
      agent: 'Agent',
    },
    addResource: {
      title: '添加资源',
      description:
        '添加完成后左侧目录树会刷新，右侧 Terminal 可继续定位新资源。',
      submitted: '资源添加任务已提交',
    },
    explorer: {
      title: '上下文目录',
      addResource: '添加资源',
      refresh: '刷新目录',
      namespaces: {
        user: '用户个性化记忆',
        session: '用户与 Agent 的原始会话',
        agent: 'Agent 的能力、工具和经验',
        resources: 'Agent 可引用的外部资源',
      },
    },
    agent: {
      autoRetrieve: 'Agent 会根据消息和工具自主检索',
      history: '历史会话',
      newSession: '新建会话',
      creating: '正在创建 Studio 会话...',
      detectingBot: '正在检测 bot 模式...',
      createFailed: '创建会话失败：{{error}}',
      retry: '重试',
      botDisabledFooter: '开启 bot 模式后可使用 Agent 对话',
      historyTitle: 'Agent 会话历史',
      historyDescription:
        '这里只展示实验场右侧 Agent 使用过的会话；新建会话会开启一个空白 Agent 上下文。',
      loadingSessions: '正在加载会话...',
      noSessions: '暂无历史会话',
      createTimeout: '创建 Studio 会话超时，请检查连接设置后重试。',
      newSessionTitle: '新建 Studio 会话',
      botPrompt: {
        title: '请开启 bot 模式',
        description:
          '当前服务未启用 Agent 对话能力，请使用 bot 模式启动服务后重试。',
        retry: '重新检测',
      },
      empty: {
        heading: 'Agent 动作会和左侧目录联动',
        body: '发送问题后，tool call 输出里的 `viking://` 文件会变成可点击链接，点击即可在左侧定位并在中间打开。',
        prompts: [
          '总结当前目录',
          '递归查找相关文档',
          '解释这个资源和项目的关系',
        ],
      },
    },
    terminal: {
      welcomeTitle: 'Terminal 已连接上下文目录',
      welcomeBody:
        '可执行 /status、/ls、/search、/read、/add-resource。输出中的资源链接会定位左侧目录并打开中间预览。',
      opened: '已打开资源',
      onlineTitle: '服务在线',
      onlineBody: 'OpenViking API 正常响应，根目录下发现 {{count}} 个节点。',
      lsBody: '{{uri}} 下共展示 {{count}} 个节点。',
      fileEmpty: '文件为空，已在中间预览区打开。',
      searchUsage: '用法：{{name}} 查询词',
      readUsage: '用法：/read viking://resources/...',
      enterUri: '请输入 viking:// URI',
      hits: '命中 resources {{resources}} 条，memory {{memories}} 条，skill {{skills}} 条。',
      addResourceBody:
        '已打开添加资源弹窗。提交后左侧目录会刷新，也可以用 /ls 或 /search 继续定位新内容。',
      addResourceTitle: '添加资源',
      unknownCommand:
        '未知命令。可用命令：/status、/ls、/search、/find、/read、/add-resource。',
      commandFailed: '命令失败',
      running: '正在执行命令...',
      placeholder: '输入 CLI 命令，例如 /status',
      suggestionsTitle: '命令建议',
      suggestionsHint: '↑↓ 选择 · Tab 补全 · Enter 执行',
      groupLabels: {
        resources: '资源',
        memories: '记忆',
        skills: '技能',
      },
      commands: {
        status: {
          description: '检查 OpenViking API 和根目录',
          usage: '/status',
        },
        ls: {
          description: '列出当前目录或指定目录',
          usage: '/ls [viking://resources/...]',
        },
        search: {
          description: '在当前上下文范围内语义搜索',
          usage: '/search 查询词',
        },
        find: {
          description: '查找相关上下文资源',
          usage: '/find 查询词',
        },
        read: {
          description: '读取并打开一个资源文件',
          usage: '/read viking://resources/.../file.md',
        },
        addResource: {
          description: '打开添加资源表单',
          usage: '/add-resource',
        },
      },
    },
  },
} as const

export default zhCN
