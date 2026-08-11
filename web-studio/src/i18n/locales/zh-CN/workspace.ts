const workspace = {
  appShell: {
    footer: {
      agentIntegrations: 'Agent 接入',
      connection: '连接设置',
      docs: '文档站',
      github: 'GitHub',
      sdkApi: 'SDK 与 API',
      users: '用户管理',
    },
    header: {
      currentUser: {
        account: 'Account',
        accountSummary: 'Account · {{account}}',
        openMenu: '查看当前用户 {{user}}',
        signedInAs: '当前数据访问身份',
        unset: '未设置',
        user: 'User',
      },
      defaultTitle: 'OpenViking Studio',
    },
    navigation: {
      home: {
        title: '首页',
      },
      crossDeviceVerify: {
        title: 'OAuth 验证',
      },
      operations: {
        title: '运维',
      },
      requestLogs: {
        title: '请求日志',
      },
      monitoring: {
        title: '监控',
      },
      skills: {
        title: '技能',
      },
      tasks: {
        title: '任务中心',
      },
      watches: {
        title: '定时同步',
      },
      retrieval: {
        title: '检索',
      },
      sessions: {
        title: '会话',
      },
      playground: {
        title: '实验场',
      },
    },
    sidebar: {
      groups: {
        operations: '活动',
        resources: '资源',
        settings: '设置',
        workspace: '工作区',
      },
      loadingSessions: '加载中...',
      noSessions: '暂无会话',
      workspaceGroupLabel: 'OpenViking Studio',
    },
  },
  monitoringPage: {
    title: '监控',
    description: '查看 OpenViking 各组件的实时健康状态。',
    version: 'v{{version}}',
    refresh: '刷新',
    updatedAt: '更新于 {{time}}',
    loading: '正在加载监控数据...',
    loadFailed: '监控数据加载失败',
    health: {
      healthy: '正常',
      unhealthy: '异常',
    },
    summary: {
      healthy: '所有组件运行正常',
      unhealthy: '部分组件需要关注',
      components: '{{healthy}} / {{total}} 个组件正常',
    },
    tabs: {
      label: '监控类型',
      overview: '总览',
      queue: '任务队列',
      vikingdb: 'VectorDB',
      models: '模型',
      filesystem: '文件系统',
      lock: '锁',
      retrieval: '检索',
    },
    detail: {
      noData: '暂无监控数据',
      descriptions: {
        queue: '资源处理、语义生成和会话提交队列。',
        vikingdb: '向量数据存储与索引服务。',
        models: 'VLM、Embedding 和 Rerank 模型服务。',
        filesystem: 'OpenViking 文件系统与挂载点。',
        lock: '事务锁与并发控制服务。',
        retrieval: '上下文检索服务。',
      },
    },
    offline: {
      title: '尚未连接 OpenViking 服务',
      description: '配置服务地址和访问凭证后即可查看监控数据。',
      action: '打开连接设置',
    },
  },
  skillsPage: {
    title: '技能',
    description: '查看当前用户和当前空间可用的 Agent 技能。',
    refresh: '刷新',
    loading: '正在加载技能...',
    empty: '暂无可用技能',
    emptyDescription: '添加技能后，会按用户技能和共享技能分开展示。',
    emptyScope: '暂无{{scope}}',
    emptyScopeDescription: '添加对应作用域的技能后，会在这里展示。',
    loadFailed: '技能加载失败',
    networkError: '无法连接 OpenViking 服务，请检查服务地址和连接状态。',
    connectionSettings: '打开连接设置',
    detail: '详情',
    openPlayground: '在 Playground 打开',
    viewDetail: '查看 {{name}} 详情',
    detailLoading: '正在加载技能详情...',
    detailLoadFailed: '技能详情加载失败',
    directory: '目录',
    none: '无',
    metrics: {
      files: '文件',
      scope: '作用域',
    },
    sections: {
      allowedTools: '允许工具',
      content: 'SKILL.md',
      description: '简介',
      files: '文件',
      overview: 'Overview',
      tags: '标签',
    },
    scopes: {
      user: '用户技能',
      agent: '共享技能',
    },
    tabs: {
      agent: '共享技能',
      label: '技能作用域',
      user: '用户技能',
    },
  },
  tasksPage: {
    title: '任务中心',
    description: '集中查看资源处理、会话提交和 Reindex 等后台任务。',
    refresh: '刷新',
    loading: '正在加载任务...',
    empty: '暂无后台任务',
    emptyDescription: '异步任务启动后，会在这里显示执行状态和更新时间。',
    emptyFiltered: '没有匹配的任务',
    emptyFilteredDescription: '可以调整或清除筛选条件后重新查看。',
    loadFailed: '任务加载失败',
    detail: {
      title: '任务详情',
      loading: '正在加载任务详情...',
      loadFailed: '任务详情加载失败',
      retry: '重试',
      openLabel: '查看任务 {{taskId}} 的详情',
      fields: {
        status: '任务状态',
        type: '任务类型',
        stage: '当前阶段',
        resource: '关联资源',
        createdAt: '创建时间',
        updatedAt: '更新时间',
      },
      error: '失败原因',
      result: '执行结果',
      noResult: '暂无执行结果',
      noResultDescription: '任务完成后，接口返回的结果会显示在这里。',
      noResultFailedDescription: '该任务未返回结果，请查看上方失败原因。',
      noResultCancelledDescription: '该任务已取消，未返回执行结果。',
    },
    filters: {
      label: '筛选',
      type: '任务类型',
      status: '任务状态',
      allTypes: '全部类型',
      allStatuses: '全部状态',
      clear: '清除筛选',
    },
    pagination: {
      next: '下一页',
      page: '第 {{page}} 页',
      pageSize: '每页条数',
      pageSizeValue: '每页 {{count}} 条',
      previous: '上一页',
      scope: '当前展示最近 {{count}} 条任务（接口最多返回 {{limit}} 条）',
    },
    table: {
      task: '任务',
      type: '类型',
      resource: '关联资源',
      createdAt: '创建时间',
      status: '状态',
    },
    status: {
      cancelled: '已取消',
      cancelling: '取消中',
      completed: '已完成',
      failed: '失败',
      pending: '等待中',
      running: '进行中',
      unknown: '未知',
    },
    types: {
      session_commit: '会话提交',
      add_resource: '资源处理',
      add_skill: '技能导入',
      connector_import: '连接器导入',
      admin_reindex: 'Reindex',
      snapshot_restore_reindex: '快照恢复索引',
      legacy_migration: '旧数据迁移',
      legacy_cleanup: '旧数据清理',
    },
  },
  watchesPage: {
    title: '定时同步',
    description: '通过 Watch 任务定期检查远程资源更新，并集中管理同步计划。',
    refresh: '刷新',
    add: '添加',
    adding: '添加中...',
    loading: '正在加载定时同步...',
    loadFailed: '定时同步加载失败',
    empty: '暂无定时同步',
    emptyDescription: '添加远程资源并开启定时同步后，会在这里显示。',
    never: '尚未同步',
    cancel: '取消',
    save: '保存',
    creation: {
      title: '正在创建定时同步',
      description: '资源已提交到后台，列表会自动刷新，请稍候。',
    },
    columns: {
      resource: '资源',
      source: '来源',
      status: '状态',
      interval: '同步周期',
      lastRun: '上次同步',
      nextRun: '下次同步',
      actions: '操作',
    },
    status: {
      active: '已启用',
      disabled: '已关闭',
    },
    actions: {
      trigger: '立即同步',
      syncing: '同步中...',
      disable: '关闭',
      enable: '启用',
      more: '更多',
      history: '处理记录',
      edit: '编辑',
      delete: '删除',
    },
    interval: {
      minutes_one: '每分钟',
      minutes_other: '每 {{count}} 分钟',
      hours_one: '每小时',
      hours_other: '每 {{count}} 小时',
      days_one: '每天',
      days_other: '每 {{count}} 天',
    },
    addDialog: {
      title: '添加定时同步',
      description: '添加远程资源，并设置 OpenViking 检查更新的周期。',
    },
    editDialog: {
      title: '编辑定时同步',
      interval: '同步周期（分钟）',
      intervalHint: '例如每小时填写 60，每天填写 1440。',
      reason: '添加原因（可选）',
      reasonPlaceholder: '为什么要持续同步这个资源？',
      instruction: '处理指令（可选）',
      instructionPlaceholder: '针对该资源的特殊处理指令。',
    },
    deleteDialog: {
      title: '删除定时同步？',
      description: '资源 {{uri}} 会继续保留，但不会再自动同步更新。',
    },
    history: {
      title: '处理记录',
      description:
        '按当前资源筛选后台处理任务，其中可能包含首次导入、手动处理和定时同步。',
      loading: '正在加载处理记录...',
      loadFailed: '处理记录加载失败',
      empty: '暂无处理记录',
      emptyDescription: '该资源暂时没有可查询的后台处理任务。',
      stage: '处理阶段',
    },
    toast: {
      creating: '正在创建定时同步，请稍候...',
      created: '定时同步已添加',
      createTimeout: '暂未查询到新任务，请稍后手动刷新',
      updated: '定时同步已更新',
      triggered: '同步任务已调度',
      deleted: '定时同步已删除',
    },
  },
  accountSwitcher: {
    create: '新建 Account',
    dialog: {
      accountLabel: 'Account',
      accountPlaceholder: 'team-account',
      adminLabel: '初始 Admin user',
      cancel: '取消',
      description:
        '创建一个新的空间和首个管理员。创建完成后将自动切换到该空间。',
      submit: '创建并切换',
      title: '新建 Account',
    },
    empty: '没有匹配的 Account',
    errors: {
      loadAccounts: '加载 Account 失败',
      noCreatedKey: 'Account 已创建，但服务端没有返回可用于切换的数据凭证。',
      noUsableKey: '该 Account 没有可用于数据访问的明文 User API Key。',
      noUsers: '该 Account 下没有可用用户。',
    },
    loading: '正在加载 Accounts...',
    manualSwitch: {
      description:
        '服务端没有返回 {{account}} 下的明文凭证。请输入该 Account 中任意用户的 User API Key。',
      hint: 'API Key 只用于校验并切换当前数据身份，不会修改或重新生成服务端凭证。',
      keyLabel: 'User API Key',
      keyPlaceholder: '粘贴目标 Account 的 User API Key',
      manageOnly: '仅管理该 Account',
      submit: '验证并切换',
      title: '输入 User API Key',
    },
    memberCount: '{{count}} 个用户',
    searchPlaceholder: '搜索 Account',
    toast: {
      created: '已创建并切换到 {{account}}',
      createdSwitchFailed:
        '{{account}} 已创建，但数据身份切换失败：{{error}}。仍可进入该 Account 管理用户。',
      managementSwitched:
        '管理空间已切换到 {{account}}。访问租户数据前，请先选择或创建 User Key。',
      switched: '已切换到 {{account}}',
    },
    unset: '未选择 Account',
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
      apiKey: {
        label: 'API Key',
        placeholder: '输入 X-API-Key 或 Bearer token',
      },
      adminApiKey: {
        label: 'Admin API key',
        placeholder: 'Root 或 account-admin key',
      },
      baseUrl: {
        label: '服务地址',
        placeholder: 'http://127.0.0.1:1933',
      },
      credentials: {
        title: '身份与凭证',
      },
      dataApiKey: {
        label: 'User API key',
      },
      userId: {
        label: 'User',
        placeholder: 'default',
      },
    },
  },
  settings: {
    actions: {
      addAccount: '新增 account',
      addUser: '新增 user',
      cancel: '取消',
      changeRole: '修改 {{user}} 的角色',
      confirmDeleteAccount: '永久删除 Account',
      confirmRemoveUser: '删除用户',
      confirmRoleChange: '确认修改',
      copy: '复制',
      currentIdentity: '当前身份',
      deleteAccount: '删除 Account',
      refresh: '刷新',
      regenerate: '重新生成',
      removeUser: '删除 {{user}}',
      save: '保存',
      switchIdentity: '切换身份',
      use: '使用',
    },
    connection: {
      accountListLimited:
        '当前 key 不能列出所有 account；如果它有 account-admin 权限，仍可管理选中的 account。',
      adminError: '校验 Root API Key 失败：{{message}}',
      description:
        '租户数据 API 使用 User API Key；控制 API 可单独使用 Root 或 account-admin key。',
      devMode: '当前为开发模式 — 身份自动确定，无需 API key。',
      keyGuide: {
        control: {
          primary:
            '当前 User API Key 已可用于 Playground 和数据访问，普通用户无需配置控制凭证。',
          secondary:
            '如需切换 Account 或管理用户，请向部署管理员索取 Root Key，或向当前 Account 管理员索取 Admin Key。Root Key 位于服务端 ov.conf 的 server.root_api_key。',
          title: '需要管理 Account 或用户？',
        },
        data: {
          primary:
            'Root/Admin API Key 主要用于管理操作，Playground 和租户数据访问需要绑定用户身份的 User API Key。',
          secondary:
            '请在「用户管理」中选择、创建用户或重新生成 User Key，然后将它用作 User API Key。',
          title: '还缺少 User API Key',
        },
        empty: {
          primary: '普通用户：请向当前 Account 管理员索取 User API Key。',
          secondary:
            '部署管理员：Root API Key 位于服务端 ov.conf 的 server.root_api_key；填入后可在「用户管理」中创建或重新生成 User Key。',
          title: '还没有 OpenViking API Key？',
        },
        learnMore: '查看 API Key 获取方式',
        trusted: {
          primary:
            '当前 Trusted 服务启用了 Root Key 校验，浏览器需要配置同一 Root API Key 才能访问管理和租户数据接口。',
          secondary:
            '请向部署管理员索取 Root Key；它位于服务端 ov.conf 的 server.root_api_key。Trusted 模式的数据身份由 Account/User 断言确定，不需要 User API Key。',
          title: 'Trusted 服务需要 Root API Key',
        },
      },
      rootHint: '用于列出 account / user，以及生成或轮换 key。',
      title: '连接设置',
      unsupportedAuthMode: {
        description:
          'Web Studio 不支持 {{mode}} 认证模式。请使用 {{ov}} CLI 或 Python SDK 连接此服务器。',
        primary: '该服务器配置了 {{mode}} 认证。',
        title: '不支持的认证模式',
      },
      userHint: '供 Playground 和租户数据 API 使用。',
    },
    connectionPage: {
      description: '配置 OpenViking 服务连接、控制面凭证和当前数据访问凭证。',
      title: '连接设置',
    },
    dialogs: {
      addAccount: {
        description:
          '创建一个工作区 account 和第一个 admin user。新 key 只会在创建后展示一次。',
        title: '新增 account',
      },
      addUser: {
        currentAccountDescription:
          '在 {{accountId}} 空间下创建用户。生成的 key 只会在创建后展示一次。',
        description:
          '在已有 account 下注册 user。生成的 key 只会在创建后展示一次。',
        title: '新增 user',
      },
      changeRole: {
        description:
          '将 {{account}} / {{user}} 的角色修改为 {{role}}。新的权限会立即生效。',
        title: '修改用户角色？',
      },
      deleteAccount: {
        confirmHint: 'Account 名称必须完全一致。',
        confirmLabel: '请输入 {{account}} 以确认',
        description:
          '此操作将永久删除 {{account}}，之后将无法再访问该 Account，且无法撤销。',
        title: '删除这个 Account？',
      },
      regenerate: {
        description:
          '要重新生成 {{account}} / {{user}} 的 API key 吗？当前 key 会立即失效。',
        title: '重新生成 API key？',
      },
      removeUser: {
        description:
          '确定从 {{account}} 空间移除 {{user}} 吗？该用户的 API Key 会立即失效，此操作不可撤销。',
        title: '删除用户？',
      },
    },
    empty: {
      adminDescription:
        '使用 root 或 account admin API key 后，可以列出用户、复制 key、新增身份或轮换凭证。',
      adminTitle: '需要 admin 权限',
      usersDescription: '创建一个 user 来生成第一个 API key。',
      usersTitle: '选中的 accounts 下没有 user',
    },
    fields: {
      account: 'Account',
      adminUser: 'Admin user',
      adminApiKey: 'Admin API key',
      apiKey: 'API key',
      baseUrl: '服务地址',
      dataApiKey: 'User API key',
      rootApiKey: 'Root or Admin API Key',
      userApiKey: 'User API Key',
      role: '角色',
      user: 'User',
    },
    health: {
      admin: '控制面权限',
      data: '数据访问',
      state: {
        checking: '检查中',
        error: '异常',
        ok: '正常',
        skipped: '未检查',
      },
    },
    keyResult: {
      description:
        '请现在复制保存。离开当前状态后，OpenViking 可能只展示前缀。',
      dismiss: '收起',
      title: '新的 API key',
    },
    loading: '正在加载身份...',
    management: {
      accountFilter: 'Accounts',
      accessDeniedDescription:
        '只有配置并通过校验的 Root 或 Account Admin API Key 才能管理用户。',
      accessDeniedTitle: '无用户管理权限',
      currentAccountDescription: '管理 {{account}} 空间下的用户和访问凭证。',
      description:
        '查看选中 accounts 下的 users 和凭证，并在网页端新增 user 或轮换 key。',
      memberListDescription:
        '“切换身份”会将该用户设为 Playground、检索等数据页面的访问身份，不会改变当前 Root/Admin 管理凭证。',
      memberListDescriptionRoot:
        '可直接修改成员角色；“切换身份”只会改变 Playground、检索等数据页面的访问身份，不会改变当前 Root 管理凭证。',
      memberListTitle: '空间成员',
      cannotRemoveCurrentIdentity: '不能删除当前正在使用的身份。',
      cannotRemoveLastManager: '不能删除空间内最后一个管理员。',
      noUsableKey: '该用户没有可用于数据访问的明文 API Key。',
      openConnection: '打开连接设置',
      title: '用户管理',
    },
    page: {
      adminDescription:
        '配置当前 OpenViking Studio 身份，并管理账号、用户和 API key。',
      description:
        '配置当前 OpenViking Studio 的服务地址和 API key，查看当前身份下的数据。',
      title: '连接与身份',
    },
    placeholders: {
      account: 'team-account',
      adminApiKey: 'Root 或 account-admin key',
      apiKey: '输入 X-API-Key 或 Bearer token',
      baseUrl: 'http://127.0.0.1:1933',
      devModeApiKey: '[dev mode，无需 API key]',
      userApiKey: 'User API key',
      user: 'default',
    },
    roles: {
      admin: 'Admin',
      root: 'Root',
      user: 'User',
    },
    serverMode: {
      api_key: 'API key 模式',
      checking: '检查中...',
      dev: '开发模式',
      ldap: 'LDAP 模式',
      offline: '离线',
      oidc: 'OIDC 模式',
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
      accountDeleted: '{{account}} 已删除',
      accountDeletedRecoveryFailed:
        'Account 已删除，但无法加载剩余 Account 列表：{{error}}',
      connectionSaved: '连接已保存',
      copyFailed: '复制失败',
      copied: '已复制',
      dataKeySelected: '已切换数据访问身份',
      keyRegenerated: 'API key 已重新生成',
      roleUpdated: '{{user}} 的角色已修改为 {{role}}',
      userCreated: 'User 已创建',
      userRemoved: '{{user}} 已删除',
    },
  },
  home: {
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
      description: '包含文件、技能与用户记忆，用于衡量当前上下文资源规模。',
      files: '文件',
      memories: '记忆',
      skills: '技能',
      title: '上下文数据量',
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
    usageAccessRequired:
      '当前连接未获取到 admin/root 权限，无法显示 Usage/Audit 数据。请在连接与身份中配置具备 Console Usage/Audit 权限的 API Key。',
  },
} as const

export default workspace
