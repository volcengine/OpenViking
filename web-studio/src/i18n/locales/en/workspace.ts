const workspace = {
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
      watches: {
        title: 'Scheduled Sync',
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
        retrieval: 'Context retrieval service.',
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
      noResultCancelledDescription:
        'This task was cancelled before it returned a result.',
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
      scope:
        'Showing the latest {{count}} tasks (the API returns at most {{limit}})',
    },
    table: {
      task: 'Task',
      type: 'Type',
      resource: 'Resource',
      createdAt: 'Created',
      status: 'Status',
    },
    status: {
      cancelled: 'Cancelled',
      cancelling: 'Cancelling',
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
  watchesPage: {
    title: 'Scheduled Sync',
    description:
      'Keep remote resources current with recurring Watch tasks and manage their schedules.',
    refresh: 'Refresh',
    add: 'Add',
    adding: 'Adding...',
    loading: 'Loading scheduled syncs...',
    loadFailed: 'Could not load scheduled syncs',
    empty: 'No scheduled syncs',
    emptyDescription:
      'Add a remote resource and enable scheduled sync to get started.',
    never: 'Not synced yet',
    cancel: 'Cancel',
    save: 'Save',
    creation: {
      title: 'Creating scheduled sync',
      description:
        'The resource was submitted in the background. The list will refresh automatically.',
    },
    columns: {
      resource: 'Resource',
      source: 'Source',
      status: 'Status',
      interval: 'Interval',
      lastRun: 'Last sync',
      nextRun: 'Next sync',
      actions: 'Actions',
    },
    status: {
      active: 'Enabled',
      disabled: 'Disabled',
    },
    actions: {
      trigger: 'Sync now',
      syncing: 'Syncing...',
      disable: 'Disable',
      enable: 'Enable',
      more: 'More',
      history: 'Processing history',
      edit: 'Edit',
      delete: 'Delete',
    },
    interval: {
      minutes_one: 'Every minute',
      minutes_other: 'Every {{count}} minutes',
      hours_one: 'Every hour',
      hours_other: 'Every {{count}} hours',
      days_one: 'Every day',
      days_other: 'Every {{count}} days',
    },
    addDialog: {
      title: 'Add scheduled sync',
      description:
        'Add a remote resource and configure how often OpenViking checks for updates.',
    },
    editDialog: {
      title: 'Edit scheduled sync',
      interval: 'Interval (minutes)',
      intervalHint: 'For example, 60 for hourly or 1440 for daily.',
      reason: 'Reason (optional)',
      reasonPlaceholder: 'Why should this resource stay synchronized?',
      instruction: 'Processing instruction (optional)',
      instructionPlaceholder:
        'Special processing instructions for this resource.',
    },
    deleteDialog: {
      title: 'Delete scheduled sync?',
      description:
        'The resource {{uri}} will remain available, but it will no longer update automatically.',
    },
    history: {
      title: 'Processing history',
      description:
        'Background tasks filtered by this resource. Results may include the initial import, manual processing, and scheduled syncs.',
      loading: 'Loading processing history...',
      loadFailed: 'Could not load processing history',
      empty: 'No processing history',
      emptyDescription:
        'No background processing tasks were found for this resource.',
      stage: 'Stage',
    },
    toast: {
      creating: 'Creating scheduled sync. Please wait...',
      created: 'Scheduled sync added',
      createTimeout: 'The new task is not visible yet. Refresh again shortly.',
      updated: 'Scheduled sync updated',
      triggered: 'Sync scheduled',
      deleted: 'Scheduled sync deleted',
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
      confirmDeleteAccount: 'Delete account permanently',
      confirmRemoveUser: 'Delete user',
      confirmRoleChange: 'Confirm change',
      copy: 'Copy',
      currentIdentity: 'Current identity',
      deleteAccount: 'Delete account',
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
      unsupportedAuthMode: {
        description:
          'Web Studio does not support the {{mode}} authentication mode. Please use the {{ov}} CLI or Python SDK to interact with this server.',
        primary: 'This server is configured with {{mode}} authentication.',
        title: 'Unsupported authentication mode',
      },
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
      deleteAccount: {
        confirmHint: 'The account name must match exactly.',
        confirmLabel: 'Enter {{account}} to confirm',
        description:
          'This permanently deletes {{account}} and removes access to it. This action cannot be undone.',
        title: 'Delete this account?',
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
      ldap: 'LDAP mode',
      offline: 'Offline',
      oidc: 'OIDC mode',
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
      accountDeleted: '{{account}} deleted',
      accountDeletedRecoveryFailed:
        'The account was deleted, but the remaining account list could not be loaded: {{error}}',
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
} as const

export default workspace
