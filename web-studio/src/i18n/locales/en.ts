const en = {
  appShell: {
    footer: {
      connection: 'Connection & Identity',
      docs: 'Documentation',
      github: 'GitHub',
    },
    header: {
      defaultTitle: 'OpenViking Studio',
    },
    navigation: {
      home: {
        title: 'Home',
      },
      oauthSetup: {
        title: 'OAuth setup',
      },
      operations: {
        title: 'Operations',
      },
      requestLogs: {
        title: 'Request Logs',
      },
      resources: {
        title: 'Context Management',
      },
      retrieval: {
        title: 'Retrieval',
      },
      sessions: {
        title: 'Sessions',
      },
      studio: {
        title: 'Playground',
      },
    },
    sidebar: {
      loadingSessions: 'Loading...',
      noSessions: 'No sessions',
      workspaceGroupLabel: 'OpenViking Studio',
    },
  },
  common: {
    action: {
      cancel: 'Cancel',
      saveConnection: 'Save Connection',
      showAdvancedIdentityFields: 'Show Advanced Identity Fields',
    },
    errorBoundary: {
      description:
        'An unhandled exception occurred while rendering the route. Try again first; if it persists, inspect the error details below.',
      reload: 'Reload Page',
      retry: 'Retry',
      title: 'Something went wrong',
    },
    language: {
      current: 'Current',
      label: 'Language',
    },
    theme: {
      toggle: 'Toggle theme',
    },
  },
  connection: {
    devMode: {
      description:
        'This server provides identity automatically, so account, user, and API key are usually not required.',
      title: 'Server-managed identity',
    },
    dialog: {
      title: 'Connection & Identity',
    },
    identitySummary: {
      dev: 'Server-managed identity',
      named: '{{identity}}',
      unset: 'Identity not set',
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
    oauthOtp: {
      title: 'OAuth client OTP',
      description:
        'Generate a short-lived code that an MCP client can submit to authorize as the selected identity.',
      generate: 'Generate OTP',
      regenerate: 'Regenerate',
      copy: 'Copy',
      copied: 'Copied',
      codeLabel: 'One-time code',
      expiresIn: 'Expires in {{seconds}}s',
      expired: 'Expired — generate a new code.',
      generateError: 'Could not generate OTP: {{message}}',
    },
  },
  settings: {
    actions: {
      addAccount: 'Add account',
      addUser: 'Add user',
      cancel: 'Cancel',
      copy: 'Copy',
      refresh: 'Refresh',
      regenerate: 'Regenerate',
      save: 'Save',
      use: 'Use',
    },
    connection: {
      adminError: 'Could not load admin identities: {{message}}',
      description:
        'Select the account and user that Studio should send with OpenViking requests.',
      noKey:
        'Enter an API key with admin access to load account and user choices.',
      title: 'Connection settings',
    },
    dialogs: {
      addAccount: {
        description:
          'Create a workspace account and its first admin user. The new key will be shown once.',
        title: 'Add account',
      },
      addUser: {
        description:
          'Register a user under an existing account. The generated key will be shown once.',
        title: 'Add user',
      },
      regenerate: {
        description:
          'Regenerate the API key for {{account}} / {{user}}. The current key stops working immediately.',
        title: 'Regenerate API key?',
      },
    },
    empty: {
      adminDescription:
        'Use a root or account admin API key to list users, copy keys, add identities, or regenerate credentials.',
      adminTitle: 'Admin access required',
      usersDescription: 'Create a user to mint the first API key.',
      usersTitle: 'No users in this account',
    },
    fields: {
      account: 'Account',
      adminUser: 'Admin user',
      agent: 'Agent',
      apiKey: 'API key',
      baseUrl: 'Server URL',
      role: 'Role',
      user: 'User',
    },
    keyResult: {
      description:
        'Copy it now. OpenViking may only show a prefix after you leave this state.',
      dismiss: 'Dismiss',
      title: 'New API key',
    },
    loading: 'Loading identities...',
    management: {
      accountFilter: 'Managed account',
      description:
        'Review users and credentials for one account, then add users or rotate keys from the web UI.',
      title: 'User management',
    },
    page: {
      description:
        'Configure the active OpenViking identity and manage accounts, users, and API keys from Studio.',
      title: 'Connection & Identity',
    },
    placeholders: {
      account: 'team-account',
      agent: 'web-studio',
      apiKey: 'Enter X-API-Key or Bearer token',
      baseUrl: 'http://127.0.0.1:1933',
      user: 'default',
    },
    roles: {
      admin: 'Admin',
      user: 'User',
    },
    serverMode: {
      api_key: 'API key mode',
      checking: 'Checking...',
      dev: 'Development mode',
      offline: 'Offline',
      trusted: 'Trusted mode',
    },
    stats: {
      accounts: 'Total accounts',
      apiKeys: 'Visible API keys',
      users: 'Users',
    },
    table: {
      account: 'Account',
      actions: 'Actions',
      apiKey: 'API key',
      role: 'Role',
      user: 'User',
    },
    toast: {
      accountCreated: 'Account created',
      connectionSaved: 'Connection saved',
      copied: 'Copied',
      keyRegenerated: 'API key regenerated',
      userCreated: 'User created',
    },
  },
  oauthSetup: {
    page: {
      title: 'OAuth setup',
      intro:
        'Use this page when authenticating an MCP client via OAuth — for example Claude.ai, Claude Desktop, ChatGPT, or Cursor. Generate a short-lived OTP here, then paste it into the MCP client to bind its connection to the selected identity.',
      docsLink: 'Read the OAuth integration guide',
    },
  },
  home: {
    agentAccess: {
      description:
        'Deduplicates agents that accessed OpenViking today and shows the latest visit time.',
      empty: 'No agent visits today',
      title: 'Agent Visits',
    },
    contextCommits: {
      description:
        'Groups resource, skill, session message, and session commit writes into 4-hour buckets. Hover a cell for details.',
      empty: 'No context commits in the last year',
      hourRange: '{{start}}-{{end}}',
      legend: {
        high: 'High',
        intense: 'Intense',
        low: 'Low',
        medium: 'Medium',
        more: 'More',
        none: 'Less',
        title: 'Commit intensity',
      },
      operations: {
        addResource: 'Resource writes',
        addSkill: 'Skill writes',
        sessionAddMessage: 'Session messages',
        sessionCommit: 'Session commits',
      },
      stats: {
        activeDays: 'Active days',
        peakDay: 'Peak day',
        recentDay: 'Recent commit',
      },
      title: 'Context Commit Stats',
      yearlyEmpty: 'No context commits',
      yearlyTotal: '{{count}} context commits',
      tooltip: {
        total: 'Total commits',
      },
    },
    contextData: {
      description:
        'Includes files, skills, user memories, and agent memories to show the current context resource scale.',
      files: 'Files',
      memories: 'Memories',
      skills: 'Skills',
      title: 'Context Data Volume',
    },
    menuIntro: {
      description:
        'The left navigation is collapsible. Primary entries include overview, context management, recursive retrieval, request logs, settings, GitHub, and docs.',
      items: {
        github: {
          description: 'Open the OpenViking source repository.',
          title: 'GitHub',
        },
        overview: {
          description: 'Review context scale and usage overview.',
          title: 'Overview',
        },
        playground: {
          description: 'Open the docs site and Playground entry.',
          title: 'Playground',
        },
        requestLogs: {
          description: 'Inspect Studio requests, status, and latency.',
          title: 'Request Logs',
        },
        resources: {
          description: 'Manage files, skills, and context directories.',
          title: 'Context Management',
        },
        retrieval: {
          description: 'Run semantic retrieval with find() and search().',
          title: 'Recursive Retrieval',
        },
        settings: {
          description: 'Configure service URL, identity, and API key.',
          title: 'Settings',
        },
      },
      title: 'Overview + Menu Guide',
    },
    page: {
      description:
        'Aligned with the product overview: menu entries, context data volume, today tokens, today retrievals, agent access, token trend, and context commit stats.',
      eyebrow: 'OpenViking Studio',
      settings: 'Connection & Settings',
      title: 'Overview',
    },
    requestFailed: 'Request failed',
    todayRetrievals: {
      description:
        'Shows successful semantic retrieval calls for find() and search() today. Resets at midnight.',
      find: 'find',
      search: 'search',
      title: 'Retrievals Today',
    },
    todayTokens: {
      description:
        'Shows real-time token consumption today. Resets at midnight.',
      embeddingInput: 'Embedding input tokens',
      title: 'Tokens Today',
      vlmInput: 'VLM input tokens',
      vlmOutput: 'VLM output tokens',
    },
    tokenTrend: {
      description:
        'Shows daily token usage over the last 14 days, including VLM input, VLM output, and embedding input.',
      empty: 'No token usage in the last 14 days',
      title: 'Total Token Consumption',
    },
    usageDisabled:
      'Usage/Audit is not initialized, so live usage stats are unavailable.',
  },
  operations: {
    page: {
      placeholder: 'Operations dashboard is under construction.',
    },
  },
  requestLogs: {
    clear: 'Clear',
    description:
      'Inspect server-side audited API requests, including status, latency, and request identifiers.',
    disabled: {
      description:
        'Usage/Audit is not initialized, so server-side request logs are unavailable.',
      title: 'Audit logs unavailable',
    },
    empty: {
      description: 'Start your first audited API call!',
      filteredDescription:
        'Adjust the query or status filter to broaden the visible log entries.',
      filteredTitle: 'No matching requests',
      title: 'No logs yet',
      upload: 'Upload File',
    },
    error: {
      description: 'Failed to load audited request logs from the server.',
      title: 'Request failed',
    },
    eyebrow: 'Studio telemetry',
    filters: {
      all: 'All logs',
      apiTypePlaceholder: 'API type',
      error: 'Error logs',
      requestIdPlaceholder: 'Exact Request ID',
      statusCodePlaceholder: 'Status code',
    },
    loading: 'Loading request logs...',
    metrics: {
      successRate: 'Success rate',
      total: 'Total calls',
    },
    pagination: {
      next: 'Next',
      pageSize: 'Rows per page',
      pageSizeValue: '{{count}} / page',
      previous: 'Previous',
      summary: '{{total}} total, page {{page}} / {{pageCount}}',
    },
    query: 'Query',
    refresh: 'Refresh',
    reset: 'Reset',
    searchPlaceholder: 'Filter method, path, or status',
    status: {
      error: 'ERR',
      pending: 'PENDING',
      success: 'OK',
    },
    table: {
      accountId: 'Account ID',
      apiType: 'API Type',
      duration: 'Duration',
      method: 'Method',
      path: 'Path',
      requestId: 'Request ID',
      status: 'Status',
      time: 'Time',
      title: 'Captured requests',
      userId: 'User ID',
    },
    title: 'Request Logs',
  },
  addResource: {
    title: 'Add Resource',
    description:
      'Upload a local file to the server. File type is auto-detected via magic bytes.',
    dropzone: {
      title: 'Drag & drop a file here, or click to select',
      hint: 'Up to 10 files at a time.',
      supportedFormats:
        'Supports PDF, Word, PPTX, Excel, Markdown, code files, images, and more',
    },
    fileInfo: {
      name: 'File',
      size: 'Size',
      type: 'Type',
      unknown: 'Unknown type',
      remove: 'Remove',
    },
    targetUri: 'Target URI',
    'targetUri.placeholder': 'viking://resources/',
    'targetUri.hint':
      'Choose where to store this resource. Defaults to viking://resources/.',
    'targetUri.browse': 'Browse',
    advancedOptions: 'Advanced Options',
    upload: 'Upload File',
    'upload.processing': 'File uploaded, processing...',
    uploading: 'Uploading\u2026',
    result: {
      success: 'Upload complete!',
      skippedFiles: '{{count}} file(s) skipped (unsupported format)',
    },
    cancelUpload: 'Cancel',
    startProcessing: 'Start Processing',
    success: 'Resource added successfully',
    fileBlocked: '"{{name}}" is not a supported file type.',
    fileTooLarge: '"{{name}}" exceeds the {{size}} file size limit.',
    tooManyFiles: 'Only the first {{count}} files were kept.',
    error: 'Request Failed',
    dirPicker: {
      title: 'Select Directory',
      select: 'Select',
      cancel: 'Cancel',
      empty: 'Empty directory',
      error: 'Failed to load directory',
      selected: 'Selected:',
    },
    mode: {
      upload: 'Upload File',
      remote: 'Remote URL',
    },
    remoteUrl: 'Remote URL',
    'remoteUrl.placeholder': 'https://github.com/org/repo',
    'remoteUrl.hint':
      'HTTP(S) URL, Git repository, or other remote resource address.',
    strict: 'Strict Mode',
    'strict.hint':
      'When enabled, the server will reject files with unsupported or unrecognized types instead of skipping them silently.',
    directlyUploadMedia: 'Directly Upload Media',
    'directlyUploadMedia.hint':
      'When enabled, media files (images, audio, video) are stored as-is. When disabled, media files are processed through AI vision/audio pipeline for content extraction first.',
    createParent: 'Auto-create Parent Folder',
    'createParent.hint':
      'When enabled, automatically creates the parent directory if it does not exist.',
    reason: 'Reason',
    'reason.placeholder': 'Why are you adding this resource?',
    instruction: 'Instruction',
    'instruction.placeholder':
      'Special processing instructions for this resource.',
    directoryScan: {
      title: 'Directory Scan Options',
      ignoreDirs: 'Ignore Directories',
      'ignoreDirs.placeholder': 'node_modules, .git, __pycache__',
      include: 'Include Pattern',
      'include.placeholder': '*.py, *.md',
      exclude: 'Exclude Pattern',
      'exclude.placeholder': '*.log, *.tmp',
    },
  },
  resources: {
    page: {
      placeholder: 'Resources workspace is under construction.',
    },
    toolbar: {
      parent: 'Go to Parent',
      refresh: 'Refresh Directory',
      search: 'Search ⌘K',
      processingTasks: 'File Processing Tasks',
      upload: 'Upload',
    },
    emptyState: {
      title: 'Your context space is empty',
      upload: 'Upload File',
    },
    uploadDialog: {
      title: 'Upload',
      description:
        'Add a local file or remote resource to the context resource library.',
    },
    processingNotice: {
      prefix: 'Files are being processed.',
      action: 'File Processing Tasks',
      suffix: 'shows progress and results.',
    },
    processingTasks: {
      title: 'File Processing Tasks',
      empty: 'No processing tasks',
      toggleError: 'Toggle error details',
      columns: {
        fileName: 'File Name',
        status: 'Status',
        size: 'Size',
      },
      status: {
        processing: 'Processing',
        success: 'Processed',
        failed: 'Processing failed',
      },
    },
    searchPalette: {
      ariaLabel: 'Search',
      openContainingDirectory: 'Open containing directory',
      placeholder: 'Search',
      scope: {
        global: 'Search scope: Global',
        current: 'Search scope: {{name}}',
        resetToGlobal: 'Click to reset to global search',
      },
      scopeState: {
        validatingTitle: 'Validating search scope',
        validatingPrefix: 'Checking whether',
        validatingSuffix: 'exists',
        switchTitle: 'Switch search scope',
        switchPrefix: 'Press',
        switchMiddle: 'to switch to',
        invalidTitle: 'Search scope not found',
        invalidPrefix: 'Path',
        invalidSuffix: 'is inaccessible and cannot be switched to',
      },
      empty: {
        title: 'Search files and directories',
      },
      browseDirHint: {
        before: 'Enter',
        after: 'to browse directories',
      },
      globalScopeHint: {
        before: 'Enter',
        after: 'to switch search scope to global',
      },
      error: 'Search failed',
      emptyResults: {
        title: 'No matching files or directories found',
        subtitle: 'Try another keyword?',
      },
      footer: {
        dirMode: {
          select: 'Select',
          level: 'Level',
          confirm: 'Confirm',
          cancel: 'Cancel',
        },
        resultMode: {
          navigate: 'Navigate',
          open: 'Open',
          close: 'Close',
          count: '{{count}} results',
        },
      },
    },
    dirBrowser: {
      back: 'Back',
      loading: 'Loading directory',
      filesSection: 'Files',
      error: 'Failed to load directory',
      empty: {
        title: 'Empty directory',
        subtitle:
          'There are currently no subdirectories to expand at this level',
      },
    },
    fileList: {
      empty: 'This directory is empty',
    },
    filePreview: {
      cancel: 'Cancel',
      edit: 'Edit',
      emptyFile: '(empty file)',
      emptyPrompt: 'Select a file to preview it here',
      imageFailed: 'Image failed to load.',
      imageLoading: 'Loading image...',
      largeFileSkipped: 'This file is large and was not loaded automatically.',
      loadingContent: 'Reading content...',
      loadingEditor: 'Loading editor...',
      markdownPreview: 'Preview',
      markdownSource: 'Source',
      save: 'Save',
      unsupportedBinary: 'Binary files do not support text preview.',
    },
    fileTree: {
      collapse: 'Collapse',
      expand: 'Expand',
      loading: 'Loading...',
    },
    findResults: {
      collapse: 'Collapse',
      expandDetails: 'Expand details',
      groups: {
        memories: 'Memories',
        resources: 'Resources',
        skills: 'Skills',
      },
      noResults: 'No matching results',
    },
  },
  retrieval: {
    title: 'Retrieval',
    searchPlaceholder: 'Search context',
    send: 'Search',
    controls: {
      function: 'Retrieval Function',
      modes: {
        find: 'find',
        search: 'search',
      },
      resultCount: 'Results',
      path: 'Path',
      pathPlaceholder: '/',
      scope: 'Scope',
      customScope: 'Custom scope',
      customScopePlaceholder: 'resources/project or viking://...',
      effectiveScope: 'Scope',
      allContexts: 'All contexts',
      scopes: {
        all: {
          label: 'All contexts',
        },
        resources: {
          label: 'Resources',
        },
        custom: {
          label: 'Custom URI',
        },
      },
      sessionId: 'Session ID',
      sessionPlaceholder: 'session_id (optional)',
    },
    results: {
      title: 'Search Results',
      topN: 'Search Results (Top{{count}})',
    },
    types: {
      resource: 'Resources',
      memory: 'Memories',
      skill: 'Skills',
    },
    queryPlan: {
      title: '{{count}} planned queries',
      more: '+{{count}} more',
    },
    loading: {
      vector: 'Searching vector indexes...',
      scan: 'Scanning context hierarchy...',
      match: 'Matching semantic context...',
      rerank: 'Reranking results...',
    },
    empty: {
      checking: 'Checking retrievable context...',
      readyTitle: 'Retrievable context is available',
      readyDescription: 'Enter a keyword and press Enter to search',
      title: 'No retrievable context yet',
      description: 'Upload your first resource to get started.',
      upload: 'Upload File',
    },
    error: 'Search failed',
    noResults: {
      title: 'No matching content found',
      subtitle: 'Try another keyword or adjust the path scope',
    },
  },
  sessions: {
    page: {
      placeholder: 'Sessions and Bot workspace is under construction.',
    },
    threadList: {
      title: 'Sessions',
      newSession: 'New Session',
    },
    chat: {
      copy: 'Copy',
      emptyDescription: 'Explore your knowledge base and start a conversation.',
      placeholder: 'Type a message...',
      emptyState: 'Select or create a session to start chatting.',
      thinking: 'Thinking...',
      reasoning: 'Reasoning',
      iteration: 'Round {{count}}',
      toolCall: 'Tool call',
      toolInput: 'Input',
      toolResult: 'Result',
      toolStatus: {
        completed: 'Completed',
        failed: 'Failed',
        running: 'Running...',
      },
      send: 'Send',
      cancel: 'Stop',
    },
    empty: {
      description: 'Select a session from the sidebar or create a new one.',
      title: 'No session selected',
    },
  },
  oauth: {
    identityPicker: {
      useCurrent: 'Authorize as the current identity',
      noCurrent:
        'No identity set. Open Connection & Identity to sign in first, or use a different API key below.',
      useCustom: 'Use a different API key',
      customKeyLabel: 'API key',
      customKeyPlaceholder: 'Paste an API key (not persisted)',
    },
    consent: {
      title: 'Authorize {{clientName}}',
      loading: 'Loading authorization request…',
      expired:
        'This authorization has expired or is no longer valid. Restart the flow from your MCP client.',
      missingPending:
        'Missing authorization id. Open the link your MCP client gave you.',
      requestSummary:
        '{{clientName}} is requesting access to your OpenViking workspace.',
      redirectLabel: 'Redirect',
      scopesLabel: 'Scopes',
      scopesNone: '(none)',
      signInRequired:
        'Sign in to OpenViking Studio (Connection & Identity) or paste an API key below to authorize this client.',
      openConnectionDialog: 'Open Connection & Identity',
      authorize: 'Authorize',
      deny: 'Deny',
      useAnotherDevice: 'Use another device →',
      waitingRedirect: 'Authorized — redirecting back to the client…',
      verifying: 'Verifying…',
      denying: 'Denying…',
      denied: 'Denied. You can close this tab.',
      verifyError: 'Authorization failed: {{message}}',
      noApiKey: 'No API key available. Select an identity or paste a key.',
    },
    verify: {
      title: 'Cross-device verify',
      description:
        'Enter the 6-character code shown on the device that started the MCP client login.',
      codeLabel: 'Verification code',
      codePlaceholder: '6-character code',
      submit: 'Authorize',
      success:
        'Authorized for {{clientName}}. You can close this tab and return to the original device.',
      successUnknownClient:
        'Authorized. You can close this tab and return to the original device.',
      verifyError: 'Authorization failed: {{message}}',
      noApiKey: 'No API key available. Select an identity or paste a key.',
      signInRequired:
        'Sign in to OpenViking Studio (Connection & Identity) or paste an API key below to verify.',
    },
  },
  studio: {
    copyUri: 'Copy current URI',
    copied: 'URI copied',
    resizeContext: 'Resize context tree width',
    resizeAction: 'Resize Terminal and Agent width',
    readFailed: 'Failed to read {{uri}}',
    tabs: {
      terminal: 'Terminal',
      agent: 'Agent',
    },
    addResource: {
      title: 'Add resource',
      description:
        'After it finishes, the context tree on the left refreshes and the Terminal on the right can locate the new resource.',
      submitted: 'Resource add task submitted',
    },
    explorer: {
      title: 'Context tree',
      addResource: 'Add resource',
      refresh: 'Refresh tree',
      namespaces: {
        user: 'Personalized user memories',
        session: 'Raw sessions between the user and the Agent',
        agent: "The Agent's capabilities, tools and experience",
        resources: 'External resources the Agent can reference',
      },
    },
    agent: {
      autoRetrieve: 'The Agent retrieves on its own from messages and tools',
      history: 'Session history',
      newSession: 'New session',
      creating: 'Creating Studio session...',
      detectingBot: 'Detecting bot mode...',
      createFailed: 'Failed to create session: {{error}}',
      retry: 'Retry',
      botDisabledFooter: 'Enable bot mode to chat with the Agent',
      historyTitle: 'Agent session history',
      historyDescription:
        'Only sessions used by the Agent panel are shown here; a new session opens a blank Agent context.',
      loadingSessions: 'Loading sessions...',
      noSessions: 'No session history yet',
      createTimeout:
        'Creating the Studio session timed out. Check your connection settings and try again.',
      newSessionTitle: 'New Studio session',
      botPrompt: {
        title: 'Please enable bot mode',
        description:
          'The current service has not enabled Agent chat. Start the service in bot mode and try again.',
        retry: 'Detect again',
      },
      empty: {
        heading: 'Agent actions sync with the tree on the left',
        body: 'After you send a question, `viking://` files in the tool call output become clickable links — click to locate them on the left and open them in the middle.',
        prompts: [
          'Summarize the current directory',
          'Recursively find related docs',
          'Explain how this resource relates to the project',
        ],
      },
    },
    terminal: {
      welcomeTitle: 'Terminal connected to the context tree',
      welcomeBody:
        'Run /status, /ls, /search, /read, /add-resource. Resource links in the output locate the left tree and open the middle preview.',
      opened: 'Resource opened',
      onlineTitle: 'Service online',
      onlineBody:
        'OpenViking API responded normally; found {{count}} nodes under the root.',
      lsBody: 'Showing {{count}} nodes under {{uri}}.',
      fileEmpty: 'File is empty; opened in the middle preview.',
      searchUsage: 'Usage: {{name}} <query>',
      readUsage: 'Usage: /read viking://resources/...',
      enterUri: 'Please enter a viking:// URI',
      hits: 'Hit {{resources}} resources, {{memories}} memories, {{skills}} skills.',
      addResourceBody:
        'Opened the add-resource dialog. After submitting, the left tree refreshes; use /ls or /search to keep locating new content.',
      addResourceTitle: 'Add resource',
      unknownCommand:
        'Unknown command. Available: /status, /ls, /search, /find, /read, /add-resource.',
      commandFailed: 'Command failed',
      running: 'Running command...',
      placeholder: 'Enter a CLI command, e.g. /status',
      suggestionsTitle: 'Command suggestions',
      suggestionsHint: '↑↓ select · Tab complete · Enter run',
      groupLabels: {
        resources: 'resource',
        memories: 'memory',
        skills: 'skill',
      },
      commands: {
        status: {
          description: 'Check the OpenViking API and root directory',
          usage: '/status',
        },
        ls: {
          description: 'List the current or a given directory',
          usage: '/ls [viking://resources/...]',
        },
        search: {
          description: 'Semantic search within the current context scope',
          usage: '/search <query>',
        },
        find: {
          description: 'Find related context resources',
          usage: '/find <query>',
        },
        read: {
          description: 'Read and open a resource file',
          usage: '/read viking://resources/.../file.md',
        },
        addResource: {
          description: 'Open the add-resource form',
          usage: '/add-resource',
        },
      },
    },
  },
} as const

export default en
