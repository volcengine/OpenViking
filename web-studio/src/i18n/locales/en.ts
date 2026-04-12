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
  },
  sessions: {
    page: {
      placeholder: 'Sessions and Bot workspace is under construction.',
    },
  },
} as const

export default en
