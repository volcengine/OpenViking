const en = {
  admin: {
    page: {
      placeholder: 'Admin tools are not wired in yet.',
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
        title: 'Admin',
      },
      operations: {
        title: 'Operations',
      },
      resources: {
        title: 'Resources',
      },
      sessions: {
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
    errorBoundary: {
      description: 'An unhandled exception occurred while rendering the route. Try again first; if it persists, inspect the error details below.',
      reload: 'Reload Page',
      retry: 'Retry',
      title: 'Something went wrong',
    },
    language: {
      current: 'Current',
      label: 'Language',
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
      description: 'This server is using implicit identity, so account, user, and API key are usually not required.',
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
    page: {
      placeholder: 'Operations dashboard is under construction.',
    },
  },
  resources: {
    page: {
      placeholder: 'Resources workspace is under construction.',
    },
  },
  sessions: {
    page: {
      placeholder: 'Sessions workspace is under construction.',
    },
  },
} as const

export default en