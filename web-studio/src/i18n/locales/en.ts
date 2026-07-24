const en = {
  appShell: {
    footer: {
      agentIntegrations: 'Agent Integrations',
      connection: 'Connection Settings',
      docs: 'Documentation',
      github: 'GitHub',
      sdkApi: 'SDK & API',
      users: 'User Management',
    },
    header: {
      currentUser: {
        account: 'Account',
        accountSummary: 'Account · {{account}}',
        openMenu: 'View current user {{user}}',
        signedInAs: 'Current data identity',
        unset: 'Not set',
        user: 'User',
      },
      defaultTitle: 'OpenViking Studio',
    },
    navigation: {
      home: {
        title: 'Home',
      },
      crossDeviceVerify: {
        title: 'OAuth verify',
      },
      operations: {
        title: 'Operations',
      },
      requestLogs: {
        title: 'Request Logs',
      },
      monitoring: {
        title: 'Monitoring',
      },
      skills: {
        title: 'Skills',
      },
      tasks: {
        title: 'Task Center',
      },
      retrieval: {
        title: 'Retrieval',
      },
      sessions: {
        title: 'Sessions',
      },
      playground: {
        title: 'Playground',
      },
    },
    sidebar: {
      groups: {
        operations: 'Activity',
        resources: 'Resources',
        settings: 'Settings',
        workspace: 'Workspace',
      },
      loadingSessions: 'Loading...',
      noSessions: 'No sessions',
      workspaceGroupLabel: 'OpenViking Studio',
    },
  },
  monitoringPage: {
    title: 'Monitoring',
    description: 'View real-time health for OpenViking components.',
    version: 'v{{version}}',
    refresh: 'Refresh',
    updatedAt: 'Updated at {{time}}',
    loading: 'Loading monitoring data...',
    loadFailed: 'Could not load monitoring data',
    health: {
      healthy: 'Healthy',
      unhealthy: 'Unhealthy',
    },
    summary: {
      healthy: 'All components are healthy',
      unhealthy: 'Some components need attention',
      components: '{{healthy}} of {{total}} components healthy',
    },
    tabs: {
      label: 'Monitoring type',
      overview: 'Overview',
      queue: 'Task queue',
      vikingdb: 'VectorDB',
      models: 'Models',
      filesystem: 'Filesystem',
      lock: 'Locks',
      retrieval: 'Retrieval',
    },
    detail: {
      noData: 'No monitoring data',
      descriptions: {
        queue: 'Resource processing, semantic generation, and session queues.',
        vikingdb: 'Vector storage and indexing service.',
        models: 'VLM, embedding, and rerank model services.',
        filesystem: 'OpenViking filesystem and mount services.',
        lock: 'Transaction locks and concurrency control.',
        retrieval: 'Context retrieval and recall service.',
      },
    },
    offline: {
      title: 'OpenViking is not connected',
      description:
        'Configure the server URL and credentials to view monitoring data.',
      action: 'Open connection settings',
    },
  },
  skillsPage: {
    title: 'Skills',
    description:
      'View Agent skills available to the current user and workspace.',
    refresh: 'Refresh',
    loading: 'Loading skills...',
    empty: 'No skills available',
    emptyDescription:
      'User and shared skills will appear here after they are added.',
    loadFailed: 'Could not load skills',
    networkError:
      'Could not connect to the OpenViking service. Check the server URL and connection status.',
    connectionSettings: 'Open connection settings',
    detail: 'Details',
    viewDetail: 'View {{name}} details',
    detailLoading: 'Loading skill details...',
    detailLoadFailed: 'Could not load skill details',
    directory: 'Directory',
    none: 'None',
    metrics: {
      files: 'Files',
      scope: 'Scope',
    },
    sections: {
      allowedTools: 'Allowed tools',
      content: 'SKILL.md',
      description: 'Description',
      files: 'Files',
      overview: 'Overview',
      tags: 'Tags',
    },
    scopes: {
      user: 'User skill',
      agent: 'Shared skill',
    },
  },
  tasksPage: {
    title: 'Task Center',
    description:
      'Track background work such as resource processing, session commits, and reindexing.',
    refresh: 'Refresh',
    loading: 'Loading tasks...',
    empty: 'No background tasks',
    emptyDescription:
      'Asynchronous work will appear here with its status and update time.',
    emptyFiltered: 'No matching tasks',
    emptyFilteredDescription: 'Adjust or clear the filters to see other tasks.',
    loadFailed: 'Could not load tasks',
    detail: {
      title: 'Task details',
      loading: 'Loading task details...',
      loadFailed: 'Could not load task details',
      retry: 'Retry',
      openLabel: 'View details for task {{taskId}}',
      fields: {
        status: 'Task status',
        type: 'Task type',
        stage: 'Current stage',
        resource: 'Resource',
        createdAt: 'Created',
        updatedAt: 'Updated',
      },
      error: 'Failure reason',
      result: 'Result',
      noResult: 'No result yet',
      noResultDescription:
        'Results returned by the API will appear here when the task completes.',
      noResultFailedDescription:
        'This task did not return a result. See the failure reason above.',
    },
    filters: {
      label: 'Filter',
      type: 'Task type',
      status: 'Task status',
      allTypes: 'All types',
      allStatuses: 'All statuses',
      clear: 'Clear filters',
    },
    pagination: {
      next: 'Next',
      page: 'Page {{page}}',
      pageSize: 'Rows per page',
      pageSizeValue: '{{count}} per page',
      previous: 'Previous',
    },
    table: {
      task: 'Task',
      type: 'Type',
      resource: 'Resource',
      createdAt: 'Created',
      status: 'Status',
    },
    status: {
      completed: 'Completed',
      failed: 'Failed',
      pending: 'Pending',
      running: 'Running',
      unknown: 'Unknown',
    },
    types: {
      session_commit: 'Session commit',
      add_resource: 'Resource processing',
      add_skill: 'Skill import',
      connector_import: 'Connector import',
      admin_reindex: 'Reindex',
      snapshot_restore_reindex: 'Snapshot reindex',
      legacy_migration: 'Legacy migration',
      legacy_cleanup: 'Legacy cleanup',
    },
  },
  accountSwitcher: {
    create: 'Create account',
    dialog: {
      accountLabel: 'Account',
      accountPlaceholder: 'team-account',
      adminLabel: 'Initial admin user',
      cancel: 'Cancel',
      description:
        'Create a workspace and its first administrator. Studio switches to it after creation.',
      submit: 'Create and switch',
      title: 'Create account',
    },
    empty: 'No matching accounts',
    errors: {
      loadAccounts: 'Could not load accounts',
      noCreatedKey:
        'The account was created, but the server did not return a data credential.',
      noUsableKey:
        'This account has no plaintext user API key available for data access.',
      noUsers: 'This account has no available users.',
    },
    loading: 'Loading accounts...',
    manualSwitch: {
      description:
        'The server did not expose a plaintext credential for {{account}}. Enter a User API Key from that account.',
      hint: 'Studio only verifies the key and switches the active data identity. It will not modify or rotate the server credential.',
      keyLabel: 'User API Key',
      keyPlaceholder: 'Paste a User API Key for the target account',
      manageOnly: 'Manage without a User Key',
      submit: 'Verify and switch',
      title: 'Enter a User API Key',
    },
    memberCount: '{{count}} users',
    searchPlaceholder: 'Search accounts',
    toast: {
      created: 'Created and switched to {{account}}',
      createdSwitchFailed:
        'Created {{account}}, but data identity switching failed: {{error}}. The Account remains available for management.',
      managementSwitched:
        'Switched management to {{account}}. Select or create a User Key before opening tenant data.',
      switched: 'Switched to {{account}}',
    },
    unset: 'No account selected',
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
      apiKey: {
        label: 'API Key',
        placeholder: 'Enter X-API-Key or Bearer token',
      },
      adminApiKey: {
        label: 'Admin API key',
        placeholder: 'Root or account-admin key',
      },
      baseUrl: {
        label: 'Service URL',
        placeholder: 'http://127.0.0.1:1933',
      },
      credentials: {
        title: 'Identity & Credentials',
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
      addAccount: 'Add account',
      addUser: 'Add user',
      cancel: 'Cancel',
      changeRole: 'Change the role for {{user}}',
      confirmRemoveUser: 'Delete user',
      confirmRoleChange: 'Confirm change',
      copy: 'Copy',
      currentIdentity: 'Current identity',
      refresh: 'Refresh',
      regenerate: 'Regenerate',
      removeUser: 'Delete {{user}}',
      save: 'Save',
      switchIdentity: 'Switch identity',
      use: 'Use',
    },
    connection: {
      accountListLimited:
        'This key cannot list all accounts, but it can still manage the selected account if it has account-admin access.',
      adminError: 'Could not verify the Root API Key: {{message}}',
      description:
        'Use a User API Key for tenant data APIs and an optional Root or account-admin key for control APIs.',
      devMode:
        'Development mode is active — identity is automatic and no API key is required.',
      keyGuide: {
        control: {
          primary:
            'Your User API Key already enables the Playground and data access. Regular users do not need a control credential.',
          secondary:
            'To switch Accounts or manage users, request a Root Key from the deployment admin or an Admin Key from the current Account admin. The Root Key is stored at server.root_api_key in the server-side ov.conf.',
          title: 'Need to manage Accounts or users?',
        },
        data: {
          primary:
            'The Root/Admin API Key is mainly for management. The Playground and tenant data APIs require a User API Key bound to a user identity.',
          secondary:
            'Select or create a user in User Management, or regenerate its key, then use it as the User API Key.',
          title: 'A User API Key is still required',
        },
        empty: {
          primary:
            'Regular users should request a User API Key from their Account admin.',
          secondary:
            'Deployment admins can find the Root API Key at server.root_api_key in the server-side ov.conf. Add it here, then create or regenerate a User Key in User Management.',
          title: 'No OpenViking API Key yet?',
        },
        learnMore: 'Learn how to get an API Key',
        trusted: {
          primary:
            'This trusted server enforces Root Key validation. The browser needs the same Root API Key for management and tenant data requests.',
          secondary:
            'Request the Root Key from the deployment admin; it is stored at server.root_api_key in the server-side ov.conf. Trusted-mode data identity comes from Account/User assertions and does not need a User API Key.',
          title: 'This trusted server requires a Root API Key',
        },
      },
      rootHint: 'Lists accounts and users, and mints or rotates keys.',
      title: 'Connection settings',
      userHint: 'Used by the Playground and tenant data APIs.',
    },
    connectionPage: {
      description:
        'Configure the OpenViking server connection, control credential, and active data credential.',
      title: 'Connection settings',
    },
    dialogs: {
      addAccount: {
        description:
          'Create a workspace account and its first admin user. The new key will be shown once.',
        title: 'Add account',
      },
      addUser: {
        currentAccountDescription:
          'Create a user in {{accountId}}. The generated key is shown only once.',
        description:
          'Register a user under an existing account. The generated key will be shown once.',
        title: 'Add user',
      },
      changeRole: {
        description:
          'Change the role for {{account}} / {{user}} to {{role}}. The new permissions take effect immediately.',
        title: 'Change user role?',
      },
      regenerate: {
        description:
          'Regenerate the API key for {{account}} / {{user}}. The current key stops working immediately.',
        title: 'Regenerate API key?',
      },
      removeUser: {
        description:
          'Remove {{user}} from {{account}}? Their API key stops working immediately. This action cannot be undone.',
        title: 'Delete user?',
      },
    },
    empty: {
      adminDescription:
        'Use a root or account admin API key to list users, copy keys, add identities, or regenerate credentials.',
      adminTitle: 'Admin access required',
      usersDescription: 'Create a user to mint the first API key.',
      usersTitle: 'No users in the selected accounts',
    },
    fields: {
      account: 'Account',
      adminUser: 'Admin user',
      adminApiKey: 'Admin API key',
      apiKey: 'API key',
      baseUrl: 'Server URL',
      dataApiKey: 'User API key',
      rootApiKey: 'Root or Admin API Key',
      userApiKey: 'User API Key',
      role: 'Role',
      user: 'User',
    },
    health: {
      admin: 'Admin control',
      data: 'Data access',
      state: {
        checking: 'Checking',
        error: 'Error',
        ok: 'OK',
        skipped: 'Not checked',
      },
    },
    keyResult: {
      description:
        'Copy it now. OpenViking may only show a prefix after you leave this state.',
      dismiss: 'Dismiss',
      title: 'New API key',
    },
    loading: 'Loading identities...',
    management: {
      accountFilter: 'Accounts',
      accessDeniedDescription:
        'User management requires a validated Root or Account Admin API key.',
      accessDeniedTitle: 'User management unavailable',
      currentAccountDescription:
        'Manage users and access credentials in the {{account}} workspace.',
      description:
        'Review users and credentials for selected accounts, then add users or rotate keys from the web UI.',
      memberListDescription:
        '"Switch identity" uses that user for data pages such as Playground and Retrieval without changing the active Root/Admin management credential.',
      memberListDescriptionRoot:
        'You can change member roles here. "Switch identity" only changes the user used by data pages such as Playground and Retrieval; it does not change the active Root management credential.',
      memberListTitle: 'Workspace members',
      cannotRemoveCurrentIdentity: 'The active identity cannot be deleted.',
      cannotRemoveLastManager:
        'The last workspace administrator cannot be deleted.',
      noUsableKey:
        'This user has no plaintext API key available for data access.',
      openConnection: 'Open connection settings',
      title: 'User management',
    },
    page: {
      adminDescription:
        'Configure the active OpenViking Studio identity and manage accounts, users, and API keys.',
      description:
        'Configure the OpenViking Studio server URL and API key, then view data for the current identity.',
      title: 'Connection & Identity',
    },
    placeholders: {
      account: 'team-account',
      adminApiKey: 'Root or account-admin key',
      apiKey: 'Enter X-API-Key or Bearer token',
      baseUrl: 'http://127.0.0.1:1933',
      devModeApiKey: '[dev mode, no api key required]',
      userApiKey: 'User API key',
      user: 'default',
    },
    roles: {
      admin: 'Admin',
      root: 'Root',
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
      copyFailed: 'Copy failed',
      copied: 'Copied',
      dataKeySelected: 'Data access identity switched',
      keyRegenerated: 'API key regenerated',
      roleUpdated: "{{user}}'s role changed to {{role}}",
      userCreated: 'User created',
      userRemoved: '{{user}} deleted',
    },
  },
  home: {
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
        'Includes files, skills, and user memories to show the current context resource scale.',
      files: 'Files',
      memories: 'Memories',
      skills: 'Skills',
      title: 'Context Data Volume',
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
    usageAccessRequired:
      'Current connection has no admin/root role. Configure an API key with Console Usage/Audit access in Connection & Identity.',
  },
  operations: {
    page: {
      placeholder: 'Operations dashboard is under construction.',
    },
  },
  requestLogs: {
    accessRequired: {
      description:
        'Current connection has no admin/root role. Configure an API key with Console Usage/Audit access in Connection & Identity.',
      title: 'Admin access required',
    },
    clear: 'Clear',
    description:
      'Inspect server-side audited API requests for the current identity, including status, latency, and request identifiers.',
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
    eyebrow: 'Playground telemetry',
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
    scope: {
      currentIdentity: 'Current scope: Current API key identity',
      currentIdentityWithName:
        'Current scope: Current API key identity ({{identity}})',
    },
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
      noDirectoryContext: 'No abstract or overview available for this folder.',
      save: 'Save',
      selectDirectoryContext: 'Select a chip to show folder context.',
      unsupportedBinary: 'Binary files do not support text preview.',
      jsonl: {
        collapse: 'Collapse',
        dialogMode: 'Dialog',
        emptyJsonl: 'Empty JSONL.',
        emptyMessage: 'Empty message',
        expand: 'Expand',
        noArguments: 'No arguments',
        rawMode: 'JSONL',
        recordCount: '{{count}} record',
        recordCount_other: '{{count}} records',
        toolcall: 'toolcall',
      },
    },
  },
  retrieval: {
    title: 'Retrieval',
    searchPlaceholder: 'Search context',
    placeholders: {
      find: 'Enter a semantic query',
      search: 'Enter a question to interpret with session context',
      grep: 'Enter a regular expression or exact text',
      glob: 'Enter a file pattern, for example **/*.md',
    },
    send: 'Search',
    controls: {
      function: 'Retrieval Function',
      modes: {
        find: 'find',
        search: 'search',
        grep: 'grep',
        glob: 'glob',
      },
      ignoreCase: 'Ignore case',
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
      line: 'Line {{line}}',
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
      count: '{{count}} session',
      count_other: '{{count}} sessions',
      loading: 'Loading sessions...',
      emptyTitle: 'No sessions yet',
      emptyDescription: 'Select the plus button to start a new conversation.',
      deleteSession: 'Delete “{{title}}”',
      shortcut: '⌘ N to create a new session',
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
      loadMoreRefs: 'Load {{count}} more ({{remaining}} remaining)',
      relativeTime: {
        justNow: 'Just now',
        minutesAgo: '{{count}} minute ago',
        minutesAgo_other: '{{count}} minutes ago',
        hoursAgo: '{{count}} hour ago',
        hoursAgo_other: '{{count}} hours ago',
        daysAgo: '{{count}} day ago',
        daysAgo_other: '{{count}} days ago',
      },
      toolStatus: {
        completed: 'Completed',
        failed: 'Failed',
        running: 'Running...',
      },
      send: 'Send',
      cancel: 'Stop',
    },
    impact: {
      title: 'Memory impact',
      open: 'View memory changes caused by this session',
      description: '{{changes}} memory changes across {{commits}} commits',
      kinds: {
        add: 'Added',
        update: 'Updated',
        delete: 'Deleted',
      },
      before: 'Before',
      after: 'After',
      addedContent: 'Added content',
      deletedContent: 'Deleted content',
      emptyContent: 'No content to display',
    },
    empty: {
      description: 'Select a session from the list or create a new one.',
      title: 'No session selected',
    },
  },
  oauth: {
    identityPicker: {
      useCurrent: 'Authorize as the current identity',
      noCurrent:
        'No identity set. Open Connection & Identity to sign in first, or use a different API key below.',
      useSelect: 'Authorize a specific account / user',
      selectAccountLabel: 'Account',
      selectUserLabel: 'User',
      selectNoKey:
        'This user has no API key. Pick another user or regenerate a key in Connection & Identity.',
      selectAccountAdminHint:
        'You can authorize users in your own account only.',
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
      openConnectionSettings: 'Open Connection & Identity',
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
  playground: {
    copyUri: 'Copy current URI',
    copied: 'URI copied',
    copyFailed: 'Copy failed',
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
      abstractLevel: 'L0',
      empty: 'empty',
      loading: 'loading',
      overviewLevel: 'L1',
      search: 'Search context',
      refresh: 'Refresh tree',
      namespaces: {
        agent: 'Agent capabilities, tools, and experience',
        user: 'Personalized user memories',
        resources: 'External resources the Agent can reference',
      },
    },
    agent: {
      autoRetrieve: 'The Agent retrieves on its own from messages and tools',
      history: 'Session history',
      newSession: 'New session',
      creating: 'Creating Playground session...',
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
        'Creating the Playground session timed out. Check your connection settings and try again.',
      newSessionTitle: 'New Playground session',
      botPrompt: {
        title: 'Please enable bot mode',
        description:
          'The current service has not enabled Agent chat. Start the service in bot mode and try again.',
        command: 'openviking-server --with-bot',
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
        'Run /status, /ls, /search, /read, /add-resource. /search is global by default; add --scope . to use the current directory, or --scope viking://resources/... to limit it to a directory.',
      scopeLabel: 'cwd: {{uri}}',
      globalScope: 'global',
      opened: 'Resource opened',
      onlineTitle: 'Service online',
      onlineBody:
        'OpenViking API responded normally; found {{count}} nodes under the root.',
      lsBody: 'Showing {{count}} nodes under {{uri}}.',
      fileEmpty: 'File is empty; opened in the middle preview.',
      searchUsage: 'Usage: {{name}} <query> [--scope .|viking://resources/...]',
      searchScopeLine: 'Search scope: {{scope}}',
      helpParameters: 'Parameters',
      helpExamples: 'Examples',
      helpSubcommands: 'Subcommands',
      noParameters: 'No parameters',
      currentScopeAction: 'Use current directory',
      readUsage: 'Usage: /read viking://resources/...',
      enterUri: 'Please enter a viking:// URI',
      hits: 'Hit {{resources}} resources, {{memories}} memories, {{skills}} skills.',
      addResourceBody:
        'Opened the add-resource dialog. After submitting, the left tree refreshes; use /ls or /search to keep locating new content.',
      addResourceTitle: 'Add resource',
      sessionUsage:
        'Usage: /session [current|list|create|switch|get|context|messages|archive|commit|extract|message|used|tool-results|tool-result|tool-search|delete] ...',
      sessionDeleteUsage: 'Usage: /session delete <session_id>',
      sessionMissing:
        'No active session. Open the Agent panel to create one, or pass a session_id.',
      sessionCurrentBody: 'Current active session: {{id}}',
      sessionListBody: '{{count}} sessions.',
      sessionCreatedBody: 'Created and switched to session: {{id}}',
      sessionSwitchedBody: 'Switched to session: {{id}}',
      sessionDeletedBody: 'Deleted session: {{id}}',
      sessionMessageAddedBody: 'Added a message to session {{id}}.',
      unknownCommand:
        'Unknown command. Available: /status, /ls, /search, /find, /read, /session, /add-resource.',
      commandFailed: 'Command failed',
      running: 'Running command...',
      placeholder: 'Enter a CLI command, e.g. /status',
      suggestionsTitle: 'Command suggestions',
      suggestionsHint: '↑↓ select · Tab complete · Enter run',
      quickStart: {
        title: 'Quick start',
        addResource: {
          title: 'Add a resource',
          command: '/add-resource',
          code: 'Import docs or files into viking://resources',
        },
        addMemory: {
          title: 'Add memory',
          command: 'Agent remembers from chat',
          code: 'Send a message in the Agent panel, then commit the session',
        },
        find: {
          title: 'Find related context',
          command: '/find openviking value',
          code: 'Search resources, memories, and skills from the current scope',
        },
      },
      commandGroups: {
        core: 'Core commands',
        filesystem: 'Filesystem',
        search: 'Search and summaries',
        status: 'Status',
        resource: 'Resource paths',
        history: 'History',
      },
      commandParameters: {
        query: {
          name: 'query',
          description: 'Keywords or a semantic question to search for.',
        },
        scope: {
          name: '--scope <.|uri>',
          description:
            'Optional. Omit for global search; pass . for the current directory; pass uri for a specific directory.',
        },
        sessionAction: {
          name: 'subcommand',
          description:
            'current, list, create, switch, get, context, messages, archive, commit, extract, message, used, tool-results, tool-result, tool-search, delete.',
        },
        sessionId: {
          name: 'session_id',
          description:
            'Optional. Most subcommands use the current Agent session when omitted; delete requires an explicit ID.',
        },
        archiveId: {
          name: 'archive_id',
          description: 'Required when reading an archive.',
        },
        messageRole: {
          name: 'role',
          description: 'For the message subcommand. Use user or assistant.',
        },
        messageContent: {
          name: 'content',
          description:
            'For the message subcommand. Text to append to the session.',
        },
        contexts: {
          name: '--context uri',
          description:
            'Repeatable for the used subcommand. Records context actually used.',
        },
        skillJson: {
          name: '--skill-json JSON',
          description: 'For the used subcommand. Records skill usage details.',
        },
        keepRecent: {
          name: '--keep-recent count',
          description:
            'For commit. Keep the most recent N live messages after commit.',
        },
        tokenBudget: {
          name: '--token-budget count',
          description:
            'For context. Limits the token budget for assembled session context.',
        },
        toolName: {
          name: '--tool-name name',
          description: 'For tool-results. Filter by tool name.',
        },
        toolResultId: {
          name: 'tool_result_id',
          description:
            'Required when reading or searching an externalized tool result.',
        },
        limit: {
          name: '--limit count',
          description: 'Limits tool result list, read, or search results.',
        },
        offset: {
          name: '--offset count',
          description: 'For tool-result. Read from a character offset.',
        },
        contextChars: {
          name: '--context-chars count',
          description:
            'For tool-search. Controls context length around matches.',
        },
        timeout: {
          name: '--timeout seconds',
          description: 'Optional. Maximum time to wait for service readiness.',
        },
        uri: {
          name: 'uri',
          description:
            'A viking:// resource path. It may be optional or required by command usage.',
        },
      },
      commandExamples: {
        status: {
          default: {
            code: '/status',
            description: 'Check Agent and API connectivity',
          },
        },
        ls: {
          current: {
            code: '/ls',
            description: 'List the current directory',
          },
          target: {
            code: '/ls viking://resources/',
            description: 'List a specified directory',
          },
        },
        search: {
          global: {
            code: '/search agent',
            description: 'Search globally',
          },
          current: {
            code: '/search agent --scope .',
            description: 'Use the highlighted directory',
          },
          scoped: {
            code: '/search agent --scope viking://resources/',
            description: 'Search only within a directory',
          },
        },
        find: {
          global: {
            code: '/find agent',
            description: 'Find related resources globally',
          },
          current: {
            code: '/find agent --scope .',
            description: 'Use the highlighted directory',
          },
          scoped: {
            code: '/find agent --scope viking://resources/',
            description: 'Find only within a directory',
          },
        },
        read: {
          file: {
            code: '/read viking://resources/file.md',
            description: 'Read and open a file',
          },
        },
        addResource: {
          default: {
            code: '/add-resource',
            description: 'Open the add-resource form',
          },
        },
        session: {
          current: {
            code: '/session',
            description: 'Show the current active session',
          },
          list: {
            code: '/session list',
            description: 'List all sessions',
          },
          create: {
            code: '/session create [session_id]',
            description: 'Create and switch to a new session',
          },
          switch: {
            code: '/session switch <session_id>',
            description: 'Switch the Agent panel session',
          },
          get: {
            code: '/session get [session_id]',
            description: 'Show session metadata',
          },
          context: {
            code: '/session context [session_id] --token-budget 8000',
            description: 'Read assembled session context',
          },
          messages: {
            code: '/session messages [session_id]',
            description: 'Read session messages',
          },
          archive: {
            code: '/session archive [session_id] <archive_id>',
            description: 'Read an archive',
          },
          commit: {
            code: '/session commit [session_id] --keep-recent 10',
            description: 'Archive and trigger memory extraction',
          },
          extract: {
            code: '/session extract [session_id]',
            description: 'Extract memories from a session',
          },
          message: {
            code: '/session message [session_id] user hello',
            description: 'Append a message to a session',
          },
          used: {
            code: '/session used [session_id] --context viking://resources/...',
            description: 'Record actually used context or skill',
          },
          toolResults: {
            code: '/session tool-results [session_id] --limit 20',
            description: 'List externalized tool results',
          },
          toolResult: {
            code: '/session tool-result [session_id] <tool_result_id>',
            description: 'Read one tool result',
          },
          toolSearch: {
            code: '/session tool-search [session_id] <tool_result_id> query',
            description: 'Search inside a tool result',
          },
          delete: {
            code: '/session delete <session_id>',
            description: 'Delete a session',
          },
        },
        tree: {
          current: {
            code: '/tree',
            description: 'Show the current directory tree',
          },
          target: {
            code: '/tree viking://resources/',
            description: 'Show a specified directory tree',
          },
        },
        stat: {
          target: {
            code: '/stat viking://resources/file.md',
            description: 'Show resource metadata',
          },
        },
        abstract: {
          target: {
            code: '/abstract viking://resources/',
            description: 'Read the directory abstract',
          },
        },
        overview: {
          target: {
            code: '/overview viking://resources/',
            description: 'Read the directory overview',
          },
        },
        health: {
          default: {
            code: '/health',
            description: 'Show backend health',
          },
        },
        wait: {
          default: {
            code: '/wait',
            description: 'Wait for service readiness',
          },
          timeout: {
            code: '/wait --timeout 30',
            description: 'Set wait time in seconds',
          },
        },
      },
      resourceSuggestion: 'Resource path',
      historySuggestion: 'History',
      groupLabels: {
        resources: 'resource',
        memories: 'memory',
        skills: 'skill',
      },
      commands: {
        status: {
          description: 'Check connection',
          usage: '/status',
        },
        ls: {
          description: 'View resources',
          usage: '/ls [viking://resources/...]',
        },
        search: {
          description: 'Semantic search',
          usage: '/search <query>',
        },
        find: {
          description: 'Find related resources',
          usage: '/find <query>',
        },
        read: {
          description: 'Read a resource file',
          usage: '/read viking://resources/.../file.md',
        },
        addResource: {
          description: 'Add external resources',
          usage: '/add-resource',
        },
        session: {
          description: 'Manage Agent sessions',
          usage: '/session subcommand',
        },
        tree: {
          description: 'Show directory tree',
          usage: '/tree [viking://resources/...]',
        },
        stat: {
          description: 'Show resource metadata',
          usage: '/stat viking://resources/...',
        },
        abstract: {
          description: 'Read directory abstract',
          usage: '/abstract viking://resources/...',
        },
        overview: {
          description: 'Read directory overview',
          usage: '/overview viking://resources/...',
        },
        health: {
          description: 'Show backend health',
          usage: '/health',
        },
        wait: {
          description: 'Wait for service readiness',
          usage: '/wait [--timeout seconds]',
        },
      },
    },
  },
} as const

export default en
