const arranger = {
  title: 'Arrange',
  description:
    'Organize business knowledge and live data into context that agents can read directly, with less tool and argument guesswork.',
  shortDescription: 'Organize static knowledge and live data',
  pathLabel: 'Root Viking URI',
  loading: 'Loading context',
  searchPlaceholder: 'Filter loaded nodes',
  emptySummary: 'No directory summary',
  start: {
    liveDescription:
      'Choose an MCP tool and preset known arguments so agents can fetch current data when reading the node.',
    liveTitle: 'Live context',
    pathHint:
      'For example, viking://resources/arranger. The path is preserved in the URL.',
    pathTitle: 'Choose a directory to arrange',
    staticDescription:
      'Organize directories, Markdown, and existing resources, then save changes to OpenViking manually.',
    staticTitle: 'Static content',
  },
  actions: {
    cancel: 'Cancel',
    confirm: 'Confirm',
    connectionSettings: 'Connection settings',
    addResource: 'Add resource',
    copy: 'Copy',
    createRoot: 'Create this directory',
    delete: 'Delete',
    editMount: 'Edit mount',
    fullscreen: 'Toggle fullscreen',
    load: 'Load',
    loadMore: 'Load more',
    mount: 'Mount context',
    move: 'Move',
    newDirectory: 'New directory',
    newFile: 'New document',
    refresh: 'Refresh',
    save: 'Save now',
  },
  views: {
    graph: 'Graph view',
    list: 'List view',
  },
  fields: {
    arguments: 'Preset arguments',
    description: 'Description',
    mountNamePlaceholder: 'status.md',
    name: 'Name',
    optional: 'Optional',
    required: 'Required',
    server: 'MCP server',
    summary: 'Directory summary',
    tags: 'Tags',
    tagsHint: 'Separate multiple tags with commas.',
    tagsPlaceholder: 'production, observability',
    targetUri: 'Destination URI',
    timeout: 'Timeout (seconds)',
    title: 'Title',
    tool: 'Tool',
    toolUri: 'Tool URI',
  },
  live: {
    badge: 'Live',
    boundTool: 'Bound tool',
    destructiveHint: 'May modify data',
    incomplete: 'Live context inventory unavailable',
    loadResult: 'Get content',
    mutationsBlocked: 'Mutating operations are blocked until mounts load.',
    noDescription: 'This tool does not provide a description.',
    nodeInfo: 'Node information',
    overview: 'Node details',
    rawConfig: 'Raw config',
    readOnly: 'Live context is read-only',
    readOnlyHint: 'Read-only tool',
    refreshResult: 'Refresh',
    result: 'Live content',
    resultDescription: 'Reading this node invokes its bound MCP tool.',
    resultEmpty: 'Select “Get content” to view the current result.',
    runtime: 'Runtime policy',
    timeoutValue: '{{value}} second attempt timeout',
    updatedAt: 'Updated at {{value}}',
  },
  mount: {
    addItem: 'Add item',
    argumentsDescription:
      'Generated from the selected tool input schema. Required values must be provided.',
    argumentsTitle: 'Tool arguments',
    catalogIncomplete: 'Some tool catalogs failed to load',
    chooseToolFirst:
      'Select a tool to generate its argument form automatically.',
    editAsJson: 'Edit as JSON',
    expectedJsonObject: 'Tool arguments must be a JSON object.',
    itemLabel: 'Item {{index}}',
    loadingTools: 'Loading servers',
    noArguments: 'This tool does not require preset arguments.',
    notSet: 'Not set',
    removeItem: 'Remove item',
    selectServer: 'Select an MCP server',
    selectTool: 'Select a tool',
    selectValue: 'Select a value',
    toolCount: '{{count}} tools',
  },
  save: {
    conflict: 'Conflict detected. Your input is preserved.',
    continueEditing: 'Continue editing',
    discard: 'Discard changes',
    dirty: 'Unsaved changes',
    error: 'Save failed',
    leaveDescription: 'Save your changes before leaving, or discard this edit.',
    leaveTitle: 'Save changes to “{{name}}”?',
    saved: 'Saved',
    saveAndLeave: 'Save and leave',
    saving: 'Saving',
  },
  dialogs: {
    addResource: {
      description:
        'Import a local file or public source into the selected directory.',
    },
    copy: {
      title: 'Copy resource',
      description: 'Choose the new URI inside the loaded root.',
    },
    delete: {
      confirm: 'Delete',
      deleting: 'Deleting',
      directoryDescription:
        'This directory and all of its contents will be permanently deleted. This action cannot be undone.',
      fileDescription:
        'This file will be permanently deleted from OpenViking. This action cannot be undone.',
      liveDescription:
        'This live context node and its mount will be permanently deleted. This action cannot be undone.',
      title: 'Delete “{{name}}”?',
    },
    file: {
      title: 'New document',
      description:
        'Create a text or Markdown document in the selected directory.',
    },
    mkdir: {
      title: 'New directory',
      description: 'Create a directory in the selected location.',
    },
    mount: {
      title: 'Mount live context',
      description: 'Bind an MCP tool to a Markdown context node.',
    },
    move: {
      title: 'Move resource',
      description: 'Choose the new URI inside the loaded root.',
    },
  },
  apiResources: {
    filesystemStat: 'filesystem stat',
    mcpMountList: 'MCP mount list',
    mcpServerList: 'MCP server list',
    mcpToolCatalog: 'MCP tool catalog',
  },
  errors: {
    destinationLiveContext: 'The destination is a live context node',
    directoryContainsLiveContext: 'This directory contains live context nodes',
    invalidApiResponse: 'Invalid {{resource}} response from OpenViking',
    liveContextMutationOnly: 'Live context nodes require mount/unmount',
    liveContextMutationRequired:
      'Live context node requires mount/unmount: {{uri}}',
    loadRoot: 'Could not load root',
    mountInventoryIncomplete:
      'Live context mounts are incomplete; mutation is blocked',
    preflightFailed: 'Unable to complete live context preflight',
    preflightFailedWithDetail:
      'Unable to complete live context preflight: {{detail}}',
    read: 'Could not read this file',
    rootNotDirectory: 'The selected root is not a directory',
    studioHtmlResponse:
      'Received the Studio HTML page instead of OpenViking API data. Configure the OpenViking server URL in Connection Settings.',
  },
  messages: {
    deleted: 'Resource deleted',
  },
  validation: {
    invalidArguments: 'Check required tool arguments and their data formats.',
    invalidTimeout: 'Timeout must be a number greater than 0.',
    mountRequired: 'Name, tool, and title are required.',
    destinationInsideSource: 'A directory cannot be moved into itself.',
    destinationOutsideRoot: 'The destination must stay inside the loaded root.',
    noRelativePathSegments: 'Relative path segments are not allowed.',
    required: 'This argument is required.',
    rootImmutable: 'The loaded root cannot be changed.',
    selectTool: 'Select a valid MCP tool.',
    singlePathSegment: 'Name must be one path segment.',
    validVikingUri: 'A valid viking:// URI is required.',
  },
  values: {
    false: 'No',
    true: 'Yes',
  },
} as const

export default arranger
