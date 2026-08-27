const activity = {
  sessions: {
    page: {
      placeholder: '会话和 VikingBot 工作区功能正在开发中。',
    },
    threadList: {
      title: '会话',
      newSession: '新建会话',
      count: '{{count}} 个会话',
      count_other: '{{count}} 个会话',
      loading: '正在加载会话...',
      emptyTitle: '还没有会话',
      emptyDescription: '点击右上角的加号开始一段新对话。',
      deleteSession: '删除“{{title}}”',
      deleteConfirmTitle: '删除会话？',
      deleteConfirmDescription:
        '“{{title}}”及其对话记录将被永久删除，此操作无法撤销。',
      cancel: '取消',
      confirmDelete: '删除',
      deleting: '正在删除...',
      deleteSuccess: '会话已删除',
      deleteFailed: '会话删除失败：{{error}}',
      shortcut: '⌘ N 新建会话',
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
      loadMoreRefs: '加载更多 {{count}} 条（剩余 {{remaining}} 条）',
      relativeTime: {
        justNow: '刚刚',
        minutesAgo: '{{count}} 分钟前',
        minutesAgo_other: '{{count}} 分钟前',
        hoursAgo: '{{count}} 小时前',
        hoursAgo_other: '{{count}} 小时前',
        daysAgo: '{{count}} 天前',
        daysAgo_other: '{{count}} 天前',
      },
      toolStatus: {
        completed: '完成',
        failed: '失败',
        running: '执行中...',
      },
      send: '发送',
      cancel: '停止',
    },
    impact: {
      title: '记忆影响',
      open: '查看本次会话造成的记忆影响',
      description: '由 {{commits}} 次提交产生 {{changes}} 项记忆变更',
      kinds: {
        add: '新增',
        update: '更新',
        delete: '删除',
      },
      allTypes: '全部',
      filterByType: '按记忆分类筛选',
      before: '变更前',
      after: '变更后',
      addedContent: '新增内容',
      deletedContent: '删除内容',
      emptyContent: '没有可展示的内容',
      loading: '正在加载记忆变更...',
      loadFailed: '记忆变更加载失败',
      retry: '重试',
      empty: '本次会话提交没有产生记忆变更。',
    },
    empty: {
      description: '从左侧选择一个会话，或创建新会话。',
      title: '未选择会话',
    },
  },
  oauth: {
    identityPicker: {
      useCurrent: '以当前身份授权',
      noCurrent:
        '尚未配置身份。请先在“连接设置”中配置身份凭证，或在下方临时粘贴一个 API 密钥。',
      useSelect: '授权指定的账号 / 用户',
      selectAccountLabel: '账号',
      selectUserLabel: '用户',
      selectNoKey:
        '该用户没有 API 密钥，请选择其他用户，或在“连接设置”中重新生成 API 密钥。',
      selectAccountAdminHint: '你只能为本账号下的用户授权。',
      useCustom: '使用其他 API 密钥',
      customKeyLabel: 'API 密钥',
      customKeyPlaceholder: '粘贴一个 API 密钥（不会持久化）',
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
        '请先在“连接设置”中配置 OpenViking Studio 身份凭证，或在下方临时粘贴 API 密钥完成授权。',
      openConnectionSettings: '打开连接设置',
      authorize: '授权',
      deny: '拒绝',
      useAnotherDevice: '在另一台设备上授权 →',
      waitingRedirect: '已授权——正在回跳到客户端…',
      verifying: '正在验证…',
      denying: '正在拒绝…',
      denied: '已拒绝，可以关闭此页。',
      verifyError: '授权失败：{{message}}',
      noApiKey: '没有可用的 API 密钥。请选择一个身份或粘贴密钥。',
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
      noApiKey: '没有可用的 API 密钥。请选择一个身份或粘贴密钥。',
      signInRequired:
        '请先在“连接设置”中配置 OpenViking Studio 身份凭证，或在下方临时粘贴 API 密钥完成验证。',
    },
  },
  playground: {
    copyUri: '复制当前 URI',
    copied: '已复制 URI',
    copyFailed: '复制失败',
    resizeContext: '调整上下文树宽度',
    resizeAction: '调整终端和 Agent 面板宽度',
    readFailed: '无法读取 {{uri}}',
    tabs: {
      terminal: '终端',
      agent: 'Agent',
    },
    addResource: {
      title: '添加资源',
      description: '添加完成后左侧上下文树会刷新，右侧终端可继续定位新资源。',
      submitted: '资源添加任务已提交',
    },
    explorer: {
      title: '上下文树',
      addResource: '添加资源',
      abstractLevel: 'L0',
      collapseDirectory: '收起 {{name}}',
      empty: '空',
      expandDirectory: '展开 {{name}}',
      loading: '加载中',
      overviewLevel: 'L1',
      search: '搜索上下文',
      refresh: '刷新上下文树',
      namespaces: {
        agent: 'Agent 的能力、工具和经验',
        user: '用户个性化记忆',
        resources: 'Agent 可引用的外部资源',
      },
    },
    agent: {
      history: '历史会话',
      newSession: '新建会话',
      creating: '正在创建工作台会话...',
      detectingBot: '正在检查 VikingBot 是否可用...',
      createFailed: '创建会话失败：{{error}}',
      retry: '重试',
      botDisabledFooter: '启用 VikingBot 后即可与 Agent 对话',
      historyTitle: 'Agent 会话历史',
      historyDescription:
        '这里只显示 Agent 面板使用过的会话。新建会话会创建一段空白的 Agent 上下文。',
      loadingSessions: '正在加载会话...',
      noSessions: '暂无历史会话',
      createTimeout: '创建工作台会话超时，请检查连接设置后重试。',
      newSessionTitle: '新建工作台会话',
      botPrompt: {
        title: '请启用 VikingBot',
        description:
          '当前服务未启用 Agent 对话功能。请使用以下参数启动服务后重试。',
        command: 'openviking-server --with-bot',
        retry: '重新检测',
      },
      empty: {
        heading: 'Agent 操作会与左侧目录联动',
        body: '发送问题后，工具调用输出里的 `viking://` 文件会变成可点击链接，点击即可在左侧定位并在中间打开。',
        prompts: [
          '总结当前目录',
          '递归查找相关文档',
          '解释这个资源和项目的关系',
        ],
      },
    },
    terminal: {
      header: '终端',
      history: '命令历史',
      historyTitle: '命令历史',
      historyDescription: '查看当前浏览器中执行过的命令。',
      clearHistory: '清空命令历史',
      noHistory: '暂无命令历史',
      welcomeTitle: '终端已连接上下文树',
      welcomeBody:
        '可执行 /status、/ls、/search、/read、/add-resource。/search 默认全局检索，可通过 --scope . 使用当前目录，或通过 --scope viking://resources/... 指定目录。',
      scopeLabel: '目录：{{uri}}',
      globalScope: '全局',
      opened: '已打开资源',
      onlineTitle: '服务在线',
      onlineBody: 'OpenViking API 正常响应，根目录下发现 {{count}} 个节点。',
      lsBody: '{{uri}} 下共展示 {{count}} 个节点。',
      fileEmpty: '文件为空，已在中间预览区打开。',
      searchUsage: '用法：{{name}} 查询词 [--scope .|viking://resources/...]',
      searchScopeLine: '搜索范围：{{scope}}',
      helpParameters: '参数',
      helpExamples: '示例',
      helpSubcommands: '子命令',
      noParameters: '无参数',
      currentScopeAction: '使用当前目录',
      readUsage: '用法：/read viking://resources/...',
      enterUri: '请输入 viking:// URI',
      hits: '命中资源 {{resources}} 条、记忆 {{memories}} 条、技能 {{skills}} 条。',
      addResourceBody:
        '已打开添加资源弹窗。提交后左侧目录会刷新，也可以用 /ls 或 /search 继续定位新内容。',
      addResourceTitle: '添加资源',
      sessionUsage:
        '用法：/session [current|list|create|switch|get|context|messages|archive|commit|extract|message|used|tool-results|tool-result|tool-search|delete] ...',
      sessionDeleteUsage: '用法：/session delete <session_id>',
      sessionMissing:
        '当前没有会话，请先打开 Agent 面板创建会话，或指定 session_id。',
      sessionCurrentBody: '当前会话：{{id}}',
      sessionListBody: '共有 {{count}} 个会话。',
      sessionCreatedBody: '已创建并切换到会话：{{id}}',
      sessionSwitchedBody: '已切换到会话：{{id}}',
      sessionDeletedBody: '已删除会话：{{id}}',
      sessionMessageAddedBody: '已向会话 {{id}} 添加消息。',
      unknownCommand:
        '未知命令。可用命令：/status、/ls、/search、/find、/read、/session、/add-resource。',
      commandFailed: '命令失败',
      running: '正在执行命令...',
      placeholder: '输入 CLI 命令，例如 /status',
      suggestionsTitle: '命令建议',
      suggestionsHint: '↑↓ 选择 · Tab 补全 · Enter 执行',
      quickStart: {
        title: '快速开始',
        addResource: {
          title: '添加资源',
          command: '/add-resource',
          code: '导入文档或文件到 viking://resources',
        },
        addMemory: {
          title: '添加记忆',
          command: '通过 Agent 会话提取记忆',
          code: '在 Agent 面板发送消息，然后提交会话',
        },
        find: {
          title: '查找相关上下文',
          command: '/find openviking 价值',
          code: '在当前范围内搜索资源、记忆和技能',
        },
      },
      commandGroups: {
        core: '核心命令',
        filesystem: '文件系统',
        search: '搜索与摘要',
        status: '状态',
        resource: '资源路径',
        history: '历史记录',
      },
      commandParameters: {
        query: {
          name: '查询词',
          description: '要检索的关键词或语义问题。',
        },
        scope: {
          name: '--scope <.|uri>',
          description:
            '可选。不填则全局搜索；传入 . 使用当前目录，传入 URI 使用指定目录。',
        },
        sessionAction: {
          name: '子命令',
          description:
            'current、list、create、switch、get、context、messages、archive、commit、extract、message、used、tool-results、tool-result、tool-search、delete。',
        },
        sessionId: {
          name: 'session_id',
          description:
            '可选。省略时，多数子命令使用当前 Agent 会话；delete 必须显式指定。',
        },
        archiveId: {
          name: 'archive_id',
          description: '读取会话归档时必填。',
        },
        messageRole: {
          name: 'role',
          description: '用于 message 子命令，角色值支持 user 或 assistant。',
        },
        messageContent: {
          name: 'content',
          description: '用于 message 子命令，指定要追加到会话的文本内容。',
        },
        contexts: {
          name: '--context uri',
          description: 'used 子命令可重复传入，记录本轮实际使用的上下文。',
        },
        skillJson: {
          name: '--skill-json JSON',
          description: 'used 子命令使用，记录实际使用的技能信息。',
        },
        keepRecent: {
          name: '--keep-recent 数量',
          description: '用于 commit 子命令，提交后保留最近 N 条未归档消息。',
        },
        tokenBudget: {
          name: '--token-budget 数量',
          description: '用于 context 子命令，限制组装上下文的 Token 预算。',
        },
        toolName: {
          name: '--tool-name 名称',
          description: 'tool-results 子命令使用，按工具名过滤。',
        },
        toolResultId: {
          name: 'tool_result_id',
          description: '读取或搜索外部化工具结果时必填。',
        },
        limit: {
          name: '--limit 数量',
          description: '用于限制工具结果列表、读取或搜索的返回数量。',
        },
        offset: {
          name: '--offset 数量',
          description: 'tool-result 子命令使用，从指定字符偏移开始读取。',
        },
        contextChars: {
          name: '--context-chars 数量',
          description: 'tool-search 子命令使用，控制命中上下文长度。',
        },
        timeout: {
          name: '--timeout 秒',
          description: '可选。等待服务就绪的最长时间。',
        },
        uri: {
          name: 'uri',
          description: '可选或必填的 viking:// 资源路径，取决于命令用法。',
        },
      },
      commandExamples: {
        status: {
          default: {
            code: '/status',
            description: '检查 Agent 和 API 的连通状态',
          },
        },
        ls: {
          current: {
            code: '/ls',
            description: '列出当前目录',
          },
          target: {
            code: '/ls viking://resources/',
            description: '列出指定目录',
          },
        },
        search: {
          global: {
            code: '/search agent',
            description: '全局语义检索',
          },
          current: {
            code: '/search agent --scope .',
            description: '使用当前高亮目录',
          },
          scoped: {
            code: '/search agent --scope viking://resources/',
            description: '只在指定目录检索',
          },
        },
        find: {
          global: {
            code: '/find agent',
            description: '全局查找相关资源',
          },
          current: {
            code: '/find agent --scope .',
            description: '使用当前高亮目录',
          },
          scoped: {
            code: '/find agent --scope viking://resources/',
            description: '只在指定目录查找',
          },
        },
        read: {
          file: {
            code: '/read viking://resources/file.md',
            description: '读取并打开文件',
          },
        },
        addResource: {
          default: {
            code: '/add-resource',
            description: '打开添加资源表单',
          },
        },
        session: {
          current: {
            code: '/session',
            description: '查看当前会话',
          },
          list: {
            code: '/session list',
            description: '列出所有会话',
          },
          create: {
            code: '/session create [session_id]',
            description: '创建并切换到新会话',
          },
          switch: {
            code: '/session switch <session_id>',
            description: '切换 Agent 面板会话',
          },
          get: {
            code: '/session get [session_id]',
            description: '查看会话元数据',
          },
          context: {
            code: '/session context [session_id] --token-budget 8000',
            description: '读取组装后的会话上下文',
          },
          messages: {
            code: '/session messages [session_id]',
            description: '读取会话消息列表',
          },
          archive: {
            code: '/session archive [session_id] <archive_id>',
            description: '读取指定会话归档',
          },
          commit: {
            code: '/session commit [session_id] --keep-recent 10',
            description: '归档并触发记忆提取',
          },
          extract: {
            code: '/session extract [session_id]',
            description: '从会话中提取记忆',
          },
          message: {
            code: '/session message [session_id] user hello',
            description: '向会话追加消息',
          },
          used: {
            code: '/session used [session_id] --context viking://resources/...',
            description: '记录实际使用的上下文或技能',
          },
          toolResults: {
            code: '/session tool-results [session_id] --limit 20',
            description: '列出外部化工具结果',
          },
          toolResult: {
            code: '/session tool-result [session_id] <tool_result_id>',
            description: '读取一项外部化工具结果',
          },
          toolSearch: {
            code: '/session tool-search [session_id] <tool_result_id> query',
            description: '在外部化工具结果中搜索',
          },
          delete: {
            code: '/session delete <session_id>',
            description: '删除指定会话',
          },
        },
        tree: {
          current: {
            code: '/tree',
            description: '展示当前目录树',
          },
          target: {
            code: '/tree viking://resources/',
            description: '展示指定目录树',
          },
        },
        stat: {
          target: {
            code: '/stat viking://resources/file.md',
            description: '查看资源元信息',
          },
        },
        abstract: {
          target: {
            code: '/abstract viking://resources/',
            description: '读取目录摘要',
          },
        },
        overview: {
          target: {
            code: '/overview viking://resources/',
            description: '读取目录概览',
          },
        },
        health: {
          default: {
            code: '/health',
            description: '查看后端健康状态',
          },
        },
        wait: {
          default: {
            code: '/wait',
            description: '等待服务就绪',
          },
          timeout: {
            code: '/wait --timeout 30',
            description: '指定等待秒数',
          },
        },
      },
      resourceSuggestion: '资源路径',
      historySuggestion: '历史记录',
      groupLabels: {
        resources: '资源',
        memories: '记忆',
        skills: '技能',
      },
      commands: {
        status: {
          description: '检查连通状态',
          usage: '/status',
        },
        ls: {
          description: '查看已有资源',
          usage: '/ls [viking://resources/...]',
        },
        search: {
          description: '语义检索上下文',
          usage: '/search 查询词',
        },
        find: {
          description: '查找相关资源',
          usage: '/find 查询词',
        },
        read: {
          description: '读取资源文件',
          usage: '/read viking://resources/.../file.md',
        },
        addResource: {
          description: '添加外部资源',
          usage: '/add-resource',
        },
        session: {
          description: '管理 Agent 会话',
          usage: '/session 子命令',
        },
        tree: {
          description: '展示目录树',
          usage: '/tree [viking://resources/...]',
        },
        stat: {
          description: '查看资源元信息',
          usage: '/stat viking://resources/...',
        },
        abstract: {
          description: '读取目录摘要',
          usage: '/abstract viking://resources/...',
        },
        overview: {
          description: '读取目录概览',
          usage: '/overview viking://resources/...',
        },
        health: {
          description: '查看后端健康状态',
          usage: '/health',
        },
        wait: {
          description: '等待服务就绪',
          usage: '/wait [--timeout seconds]',
        },
      },
    },
  },
} as const

export default activity
