const studio = {
  duration: { milliseconds: '{{value}}ms', seconds: '{{value}}s' },
  errors: {
    botHealth: 'Bot health check failed ({{status}})',
    botSteer: 'Bot steer failed ({{status}})',
    botInterrupt: 'Bot interrupt failed ({{status}})',
    expectedEventStream: 'Expected {{contentType}} response',
    sseFailed: 'SSE request failed ({{status}}): {{detail}}',
    imageLoadFailed: 'Image failed to load',
    invalidMessage: '{{location}}: invalid message',
    sessionMessages: 'Session messages',
    messageLocation: '{{location}}, message {{index}}',
    invalidJsonl: '{{archiveId}}: expected JSONL content',
    archiveLine: '{{archiveId}}: line {{index}}',
    invalidJson: '{{location}}: invalid JSON',
    clipboardUnavailable: 'Clipboard is unavailable',
    clipboardFailed: 'Clipboard copy failed',
    missingServerUrl: 'Missing server URL',
    invalidServerUrl: 'Invalid server URL',
    connectionChanged: 'Active request belongs to a different connection',
    noActiveRequest: 'Session has no active request to steer',
    inputRejected: 'Bot did not accept the input',
    restartTimeout: 'Restart timed out',
    modelResponseUnavailable: 'Model configuration response is unavailable',
    invalidTaskResponse: 'Invalid task response',
    invalidDirectoryResponse: 'Invalid fs/ls response',
    emptyBlob: 'empty blob',
    invalidSvg: 'Mermaid returned invalid SVG markup.',
    invalidConfigObject: 'ov.conf must contain a JSON object',
    configUpdateFailed: 'Could not update ov.conf',
    gatewayUnavailable: 'Gateway is unavailable for this connection',
    botAccountRequired: 'Select an account before managing bots',
    waitUsage: 'Usage: /wait [--timeout seconds]',
    unknown: 'Unknown error',
    requestFailed: 'OpenViking request failed',
    sessionUriMissing: 'Session URI is missing',
    invalidFlag: 'Invalid --{{key}}: {{value}}',
    missingApiKeyHint: 'Please go to Settings and set X-API-Key.',
    httpStatus: 'Request failed with status {{status}}',
    truncated: '... (truncated, {{count}} chars total)',
  },
  codeEditor: {
    completions: 'Completions',
    foldedLines: 'Folded lines',
    unfoldedLines: 'Unfolded lines',
    to: 'to',
    foldedCode: 'folded code',
    unfold: 'unfold',
    foldLine: 'Fold line',
    unfoldLine: 'Unfold line',
    selectionDeleted: 'Selection deleted',
    controlCharacter: 'Control character',
    find: 'Find',
    replaceInput: 'Replace',
    next: 'next',
    previous: 'previous',
    selectAll: 'all',
    matchCase: 'match case',
    regexp: 'regexp',
    wholeWord: 'by word',
    replace: 'replace',
    replaceAll: 'replace all',
    close: 'close',
    goToLine: 'Go to line',
    go: 'go',
    currentMatch: 'current match',
    onLine: 'on line',
    replacedOnLine: 'replaced match on line $',
    replacedMatches: 'replaced $ matches',
  },
  oauthClient: 'MCP client',
  localConnection: 'Local OpenViking',
  profiles: {
    connectionChanged:
      'The connection changed. Select the identity again on the active connection.',
    identityPending: 'Identity unconfirmed',
    typeHint:
      'Select the service type for this profile. Save to apply changes to the active connection.',
    title: 'Connection profiles',
    hint: 'Enable one profile to use its service and credentials. To delete the active profile, enable another first.',
    add: 'Add profile',
    edit: 'Edit profile',
    delete: 'Delete',
    activate: 'Enable',
    active: 'Active',
    name: 'Profile name',
    save: 'Save',
    cancel: 'Cancel',
    saveHint:
      'Saving a new profile does not enable it. Changes to the active profile reconnect immediately.',
    invalidUrl:
      'Enter a valid HTTP or HTTPS service address without embedded credentials.',
    deleteConfirm: 'Delete “{{name}}”? Its saved credentials will be removed.',
    fields: {
      baseUrl: 'Service address',
      apiKey: 'User API Key',
      adminApiKey: 'Management API Key (optional)',
      accountId: 'Account ID',
      userId: 'User ID',
    },
  },

  selectSessionRecord:
    'Select a session from the list to view its conversation history.',
  noSessionRecords: 'No session records are accessible to this identity.',
  sessionRecords: 'Session records',
  processingTasks: 'Processing tasks',
  backToFiles: 'Back to files',
  showOverview: 'Show statistics and queues',
  hideOverview: 'Hide statistics and queues',
  connectionSummary: {
    opensource:
      'The workspace is configured for an open-source service. Extensions and management require their own configuration and permissions.',
    volcengine:
      'The workspace is configured for a hosted service. Only verified extensions are shown; a user API key does not grant management access.',
    unknown:
      'Available features appear after connection verification. Select a service type for a custom address.',
  },

  watchesReadDescription:
    'View configured synchronization tasks and their status.',
  watchesReadEmpty:
    'There are no synchronization tasks accessible to this identity.',
  statistics: 'Statistics',
  listingLimit: 'This view shows up to 500 entries.',
  directory: 'My directory',
  memories: 'Memories',
  domainsLabel: 'Studio areas',
  domains: {
    workspace: 'Workspace',
    extensions: 'Advanced features',
    management: 'Management',
  },
  providers: {
    opensource: 'Open source',
    volcengine: 'Volcengine hosted',
    unknown: 'Service type unconfirmed',
    checking: 'Checking connection…',
  },
  serviceType: 'Service type',
  auto: 'Detect automatically',
  serviceHint:
    'Automatic detection uses Volcengine for official endpoints and open source for other addresses. For a hosted proxy, select Volcengine hosted. Features appear after connection verification.',
  cloudManagement:
    'Hosted management requires a separate management service. A data key does not enable management.',
  connectionSettings: 'Connection settings',
  retry: 'Retry',
  loading: 'Loading…',
  empty: 'This directory is empty.',
  selectFile: 'Select a file to preview its content.',
  spaces: 'Directory scope',
  personal: 'My space',
  shared: 'Shared resources',
  back: 'Back',
  readOnly:
    'This workspace is read-only. Shared resources remain separate from your personal space.',
  directoryDescription:
    'Browse your personal context and resources shared with you.',
  directoryError:
    'Unable to open this directory. It may be missing or inaccessible to your identity.',
  states: {
    gatewayAuthMode: {
      title: 'Gateway requires API key authentication',
      description:
        'Connect to an open-source service in API key mode using a verified management key. Trusted mode is not supported by Gateway user binding.',
    },
    checking: {
      title: 'Checking availability',
      description: 'Verifying this connection and its read capabilities.',
    },
    connectionFailed: {
      title: 'Connection could not be verified',
      description:
        'Check the service URL and API key, then retry in connection settings.',
    },
    managementRequired: {
      title: 'Independent management authorization required',
      description:
        'Native management is available for authorized open-source management connections. Hosted management requires a separate backend.',
    },
    unknownProvider: {
      title: 'Confirm the service type',
      description:
        'This custom address has no confirmed provider. Select the service type in connection settings to continue.',
    },
    unavailable: {
      title: 'This service does not expose this feature',
      description:
        'You can continue browsing your directory and other available features.',
    },
    forbidden: {
      title: 'Your identity cannot access this feature',
      description:
        'This connection does not grant the required read permission.',
    },
    unknown: {
      title: 'Availability is not confirmed',
      description:
        'The extension contract has not been verified for this connection. Your workspace remains available.',
    },
  },
} as const
export default studio
