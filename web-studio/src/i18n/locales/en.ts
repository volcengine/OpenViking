const en = {
  appShell: {
    footer: {
      connection: 'Connection & Identity',
    },
    header: {
      defaultTitle: 'OpenViking Studio',
    },
    navigation: {
      home: {
        title: 'Home',
      },
      addResource: {
        title: 'Add Resource',
      },
      fileSystem: {
        title: 'File System',
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
  home: {
    memoryStats: {
      category: {
        cases: 'Cases',
        entities: 'Entities',
        events: 'Events',
        patterns: 'Patterns',
        preferences: 'Preferences',
        profile: 'Profile',
        skills: 'Skills',
        tools: 'Tools',
      },
      subtitle: 'Memory category distribution',
      title: 'Memory Stats',
    },
    recentTasks: {
      empty: 'No tasks',
      subtitle: 'Background tasks',
      title: 'Recent Tasks',
    },
    requestFailed: 'Request failed',
    sessions: {
      empty: 'No sessions',
      subtitle: 'Session list',
      title: 'Sessions',
    },
    statCard: {
      memoryTotal: 'Memory Total',
      tokenUsage: 'Token Usage',
      vectorCount: 'Vector Count',
      vectorCountSub: 'indexed embeddings',
      memoryTotalSub: 'across all categories',
      tokenUsageSub: 'lifetime total',
    },
    systemHealth: {
      allOperational: 'All systems operational',
      close: 'Close',
      dialogDescription: 'Detailed error information for the selected component.',
      dialogTitle: 'Error Details',
      issuesDetected: 'Issues detected',
      noDetails: 'No detailed error message was returned.',
      title: 'System Health',
      viewErrorAria: 'View error details for {{component}}',
      viewDetails: 'View Details',
      nIssues: '{{count}} issue(s) detected',
      copyError: 'Copy Error',
      copied: 'Copied',
      statusDetail: 'Status Detail',
      rawJson: 'Raw JSON',
      operational: 'operational',
      degraded: 'degraded',
      error: 'error',
      healthy: 'Healthy',
      unhealthy: 'Unhealthy',
      errors: 'errors',
    },
  },
  operations: {
    page: {
      placeholder: 'Operations dashboard is under construction.',
    },
  },
  addResource: {
    title: 'Add Resource',
    description: 'Upload a local file to the server. File type is auto-detected via magic bytes.',
    dropzone: {
      title: 'Drag & drop a file here, or click to select',
      hint: 'Only one file at a time.',
      supportedFormats: 'Supports PDF, Word, PPTX, Excel, Markdown, code files, images, and more',
    },
    fileInfo: {
      name: 'File',
      size: 'Size',
      type: 'Type',
      unknown: 'Unknown type',
      remove: 'Remove',
    },
    targetUri: 'Target URI',
    'targetUri.hint': 'Choose where to store this resource. Defaults to viking://resources/.',
    'targetUri.browse': 'Browse',
    advancedOptions: 'Advanced Options',
    upload: 'Upload File',
    'upload.progress': 'Uploading... {{progress}}%',
    'upload.processing': 'File uploaded, processing...',
    uploading: 'Uploading\u2026',
    result: {
      success: 'Upload complete!',
      skippedFiles: '{{count}} file(s) skipped (unsupported format)',
    },
    continueUpload: 'Continue Uploading',
    cancelUpload: 'Cancel',
    success: 'Resource added successfully',
    fileBlocked: '"{{name}}" is not a supported file type.',
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
    'remoteUrl.hint': 'HTTP(S) URL, Git repository, or other remote resource address.',
    submit: 'Add Resource',
    strict: 'Strict Mode',
    'strict.hint': 'When enabled, the server will reject files with unsupported or unrecognized types instead of skipping them silently.',
    directlyUploadMedia: 'Directly Upload Media',
    'directlyUploadMedia.hint': 'When enabled, media files (images, audio, video) are stored as-is. When disabled, media files are processed through AI vision/audio pipeline for content extraction first.',
    reason: 'Reason',
    'reason.placeholder': 'Why are you adding this resource?',
    instruction: 'Instruction',
    'instruction.placeholder': 'Special processing instructions for this resource.',
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
    searchPalette: {
      placeholder: 'Search... Enter / to browse directories',
      scope: {
        global: 'Search scope: Global',
        current: 'Search scope: {{name}}',
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
        title: 'Semantic knowledge search',
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
        title: 'No matching content found',
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
      empty: {
        title: 'Empty directory',
        subtitle: 'There are currently no subdirectories to expand at this level',
      },
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
      placeholder: 'Type a message...',
      emptyState: 'Select or create a session to start chatting.',
      thinking: 'Thinking...',
      toolCall: 'Tool call',
      send: 'Send',
      cancel: 'Stop',
    },
  },
} as const

export default en
