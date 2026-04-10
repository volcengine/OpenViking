export const defaultLanguage = 'zh-CN' as const

export const resources = {
  en: {
    admin: {
      aside: {
        compatibility: {
          description: 'When development mode is detected, the navigation item is hidden to avoid entering an unavailable skeleton.',
          title: 'Development Mode Compatibility',
        },
        permissions: {
          description: 'This version only provides the skeleton. Specific permission checks and empty-state hints will be refined when APIs are wired in.',
          title: 'Permission Requirements',
        },
        subjects: {
          tags: {
            accounts: 'Accounts',
            roles: 'Roles',
            users: 'Users',
          },
          title: 'Managed Entities',
        },
      },
      highlights: {
        accountManagement: {
          description: 'Will connect account create/list/delete later.',
          title: 'Account Management',
        },
        keyRotation: {
          description: 'Will connect regenerate key later, and add frontend hints for root/admin permissions.',
          title: 'Key Rotation',
        },
        userManagement: {
          description: 'Will connect user register/list/delete and role updates later.',
          title: 'User Management',
        },
      },
      page: {
        description: 'The admin entry is used for multi-tenant account, user, role, and key operations. In development mode these APIs are not available, so the navigation is hidden automatically.',
        kicker: 'Admin Workspace',
        title: 'Account, User, and Key Management',
      },
    },
    appShell: {
      footer: {
        connection: 'Connection & Identity',
      },
      header: {
        defaultTitle: 'OpenViking Studio',
      },
      navigation: {
        admin: {
          description: 'Manage accounts, users, and keys when admin capability is available.',
          title: 'Admin',
        },
        operations: {
          description: 'Inspect service state, background tasks, debug information, and runtime metrics.',
          title: 'Operations',
        },
        resources: {
          description: 'Browse the resource tree, preview content, and locate context through the search modal.',
          title: 'Resources',
        },
        sessions: {
          description: 'Organize messages, context, archive, memory, and async tasks around a session.',
          title: 'Sessions',
        },
      },
      sidebar: {
        workspaceGroupLabel: 'Workspace',
      },
    },
    common: {
      action: {
        cancel: 'Cancel',
        saveConnection: 'Save Connection',
        showAdvancedIdentityFields: 'Show Advanced Identity Fields',
      },
      placeholder: {
        defaultState: {
          description: 'The page skeleton is already in place, and functional areas will be filled in over subsequent iterations.',
          title: 'Current Status',
        },
        layoutNote: 'This version provides the layout placeholder first, and specific functionality will be wired in later.',
      },
      serverMode: {
        checking: 'Detecting',
        devImplicit: 'Development Mode',
        explicitAuth: 'Explicit Auth',
        offline: 'Offline',
      },
    },
    connection: {
      devMode: {
        description: 'In the current environment the server uses implicit identity, so you usually do not need to fill in API Key, Account, or User.',
        title: 'Development Mode Detected',
      },
      dialog: {
        title: 'Connection & Identity',
      },
      identitySummary: {
        devImplicit: 'Server-managed identity',
        named: '{{identity}}',
        unset: 'Identity not set',
      },
      fields: {
        accountId: {
          label: 'Account',
          placeholder: 'default',
        },
        apiKey: {
          label: 'API Key',
          placeholder: 'Enter X-API-Key or Bearer token',
        },
        baseUrl: {
          label: 'Service URL',
          placeholder: 'http://127.0.0.1:1933',
        },
        credentials: {
          title: 'Identity & Credentials',
        },
        userId: {
          label: 'User',
          placeholder: 'default',
        },
      },
    },
    operations: {
      aside: {
        debug: {
          description: 'Only system-level debugging belongs here. It must not be mixed with resource or session operations.',
          title: 'Debug',
        },
        quality: {
          description: 'Prometheus, retrieval, and health quality metrics will be wired in later.',
          title: 'Metrics & Quality',
        },
        sources: {
          tags: {
            health: '/health',
            observer: 'observer.*',
            ready: '/ready',
            tasks: 'tasks',
          },
          title: 'Planned Data Sources',
        },
      },
      highlights: {
        debugMetrics: {
          description: 'Host metrics, vector debugging, and other runtime debugging entries.',
          title: 'Debug Metrics',
        },
        systemStatus: {
          description: 'Aggregate health, ready, observer.system, and system.status.',
          title: 'System Status',
        },
        tasks: {
          description: 'Poll and track background work such as session commit and resource reindex.',
          title: 'Background Tasks',
        },
      },
      page: {
        description: 'This area hosts system, observer, tasks, metrics, and debug runtime information, separated from business operations in the session workspace.',
        kicker: 'Operations Panel',
        title: 'Service Status and Background Tasks',
      },
    },
    resources: {
      aside: {
        plan: {
          description: 'Keep browsing and search within a single workflow.',
          title: 'Page Plan',
        },
        tags: {
          importExport: 'Import/Export',
          preview: 'Content Preview',
          relations: 'Relations',
          reindex: 'Reindex',
          searchModal: 'Search Modal',
          tree: 'Tree Browsing',
          uploadFlow: 'Wire in upload and pack flow later',
        },
      },
      highlights: {
        preview: {
          description: 'Reserve interaction slots for abstract, overview, download, and reindex.',
          title: 'Content Preview',
        },
        searchModal: {
          description: 'Search will not become a top-level page, but open from the current resource view.',
          title: 'Search Modal',
        },
        tree: {
          description: 'Reserve the left resource tree and directory navigation for fs.ls, fs.tree, and content.read.',
          title: 'Tree Browsing',
        },
      },
      page: {
        description: 'This area will host the resource tree, content preview, relation view, import/export, and the search modal. The current version sets up the overall skeleton first so features can be connected incrementally.',
        kicker: 'Resource Workspace',
        title: 'Resource Browsing and Search',
      },
    },
    sessions: {
      aside: {
        bot: {
          description: 'When enabled on the server side, Bot will be added as an optional interaction zone without affecting the session page itself.',
          title: 'Bot Integration',
        },
        layout: {
          description: 'The left-center-right layout will be implemented step by step in later iterations.',
          tags: {
            archive: 'Archive History',
            contextSidebar: 'Context Sidebar',
            messages: 'Messages & Actions',
            taskStatus: 'Task Status',
          },
          title: 'Primary Regions',
        },
        memory: {
          tags: {
            aggregateStats: 'Aggregate Memory Stats',
            commit: 'Commit',
            extract: 'Extract',
            sessionStats: 'Session Stats',
            standalone: 'Memory stays inside the session page for now',
          },
          title: 'Memory Consolidation',
        },
      },
      highlights: {
        context: {
          description: 'Reserve the get_session_context and archive expansion area to show the assembled payload.',
          title: 'Context Assembly',
        },
        memory: {
          description: 'The first version keeps extraction stats, commit results, and memory entry points inside the session page.',
          title: 'Memory Area',
        },
        sessionList: {
          description: 'Reserve the left session list and switching capability for sessions create/list/get/delete.',
          title: 'Session List',
        },
      },
      page: {
        description: 'The session page is not a monitoring wall. It is a workspace for messages, context assembly, archive, memory extraction, and async tasks. If Bot is enabled later, it will be attached here as an optional interaction zone.',
        kicker: 'Session Workspace',
        title: 'Sessions, Context, and Memory Consolidation',
      },
    },
  },
  'zh-CN': {
    admin: {
      aside: {
        compatibility: {
          description: '检测到开发模式时会隐藏该导航项，避免进入不可用骨架。',
          title: '开发模式兼容',
        },
        permissions: {
          description: '当前只做骨架，具体权限判断和空态提示在后续接入接口时完善。',
          title: '权限前提',
        },
        subjects: {
          tags: {
            accounts: 'Accounts',
            roles: 'Roles',
            users: 'Users',
          },
          title: '预期对象',
        },
      },
      highlights: {
        accountManagement: {
          description: '后续接入 account create/list/delete。',
          title: '账号管理',
        },
        keyRotation: {
          description: '后续接入 regenerate key，并对 root/admin 权限做前端提示。',
          title: '密钥轮换',
        },
        userManagement: {
          description: '后续接入 user register/list/delete 和 role 调整。',
          title: '用户管理',
        },
      },
      page: {
        description: '管理入口用于承接多租户账号、用户、角色与密钥操作。开发模式下这些接口并不成立，因此导航会自动隐藏。',
        kicker: '管理面',
        title: '账号、用户与密钥管理',
      },
    },
    appShell: {
      footer: {
        connection: '连接与身份',
      },
      header: {
        defaultTitle: 'OpenViking Studio',
      },
      navigation: {
        admin: {
          description: '账号、用户与密钥管理，仅在具备管理能力时使用。',
          title: '管理',
        },
        operations: {
          description: '查看服务状态、后台任务、调试信息与运行时指标。',
          title: '运维',
        },
        resources: {
          description: '浏览资源树、预览内容，并通过检索 modal 快速定位上下文。',
          title: '资源',
        },
        sessions: {
          description: '围绕 session 组织消息、上下文、archive、记忆和异步任务。',
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
      placeholder: {
        defaultState: {
          description: '页面骨架已经落位，功能区在后续迭代中逐步填充。',
          title: '当前状态',
        },
        layoutNote: '这一版先提供布局占位，后续接入具体功能。',
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
        description: '当前环境下服务端会使用隐式身份，通常不需要填写 API Key、Account 或 User 字段。',
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
      aside: {
        debug: {
          description: '这里只放系统级调试，不与资源或会话页面混用。',
          title: 'Debug',
        },
        quality: {
          description: 'Prometheus、retrieval 和健康指标会在后续接入。',
          title: '指标与质量',
        },
        sources: {
          tags: {
            health: '/health',
            observer: 'observer.*',
            ready: '/ready',
            tasks: 'tasks',
          },
          title: '数据源规划',
        },
      },
      highlights: {
        debugMetrics: {
          description: '承接 metrics、vector debug 与其他运行时调试入口。',
          title: '调试指标',
        },
        systemStatus: {
          description: '聚合 health、ready、observer.system 和 system.status。',
          title: '系统状态',
        },
        tasks: {
          description: '提供 session commit、资源 reindex 等后台任务的轮询与追踪。',
          title: '后台任务',
        },
      },
      page: {
        description: '这里用于承载 system、observer、tasks、metrics 与 debug 等运行时信息，和会话工作区里的业务操作面保持分离。',
        kicker: '运维面板',
        title: '服务状态与后台任务',
      },
    },
    resources: {
      aside: {
        plan: {
          description: '将浏览与检索收敛为一条操作流。',
          title: '本页规划',
        },
        tags: {
          importExport: '导入导出',
          preview: '内容预览',
          relations: '关系查看',
          reindex: '重建索引',
          searchModal: '检索 modal',
          tree: '树状浏览',
          uploadFlow: '后续接入资源上传与 pack 流程',
        },
      },
      highlights: {
        preview: {
          description: '预留 abstract、overview、download 和 reindex 的交互位。',
          title: '内容预览',
        },
        searchModal: {
          description: '检索不会独立成一级页面，而会从当前资源视图中弹出。',
          title: '检索 modal',
        },
        tree: {
          description: '预留左侧资源树和目录导航，后续接 fs.ls、fs.tree、content.read。',
          title: '树状浏览',
        },
      },
      page: {
        description: '这里会承载资源树、内容预览、关系查看、导入导出和检索 modal。当前版本先把整体骨架搭起来，方便后续逐块接功能。',
        kicker: '资源工作区',
        title: '资源浏览与检索',
      },
    },
    sessions: {
      aside: {
        bot: {
          description: '仅在服务端启用时作为可选交互区接入，不影响会话页本体成立。',
          title: 'Bot 集成',
        },
        layout: {
          description: '左中右三栏布局会在后续迭代中逐步落地。',
          tags: {
            archive: 'Archive 历史',
            contextSidebar: '上下文侧栏',
            messages: '消息与操作',
            taskStatus: 'Task 状态',
          },
          title: '主区块',
        },
        memory: {
          tags: {
            aggregateStats: 'Aggregate Memory Stats',
            commit: 'Commit',
            extract: 'Extract',
            sessionStats: 'Session Stats',
            standalone: '记忆先不单列一级入口',
          },
          title: '记忆沉淀',
        },
      },
      highlights: {
        context: {
          description: '预留 get_session_context 和 archive 展开区域，用于展示 assembled payload。',
          title: '上下文装配',
        },
        memory: {
          description: '首版把 extraction stats、commit 结果与 memory 入口收纳到会话页内。',
          title: '记忆区',
        },
        sessionList: {
          description: '占位左侧 session 列表与切换能力，后续接 sessions create/list/get/delete。',
          title: 'Session 列表',
        },
      },
      page: {
        description: '会话页不是监视大屏，而是承载消息、上下文装配、archive、记忆提取和异步任务的工作区。后续如开启 Bot，也会作为这里的可选交互子区接入。',
        kicker: '会话工作区',
        title: '会话、上下文与记忆沉淀',
      },
    },
  },
} as const

export const supportedLanguages = Object.keys(resources)