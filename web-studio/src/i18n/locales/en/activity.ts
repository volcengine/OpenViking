const activity = {
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
      deleteConfirmTitle: 'Delete session?',
      deleteConfirmDescription:
        '“{{title}}” and its conversation history will be permanently deleted. This action cannot be undone.',
      cancel: 'Cancel',
      confirmDelete: 'Delete',
      deleting: 'Deleting...',
      deleteSuccess: 'Session deleted',
      deleteFailed: 'Could not delete session: {{error}}',
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
      allTypes: 'All',
      filterByType: 'Filter by memory type',
      before: 'Before',
      after: 'After',
      addedContent: 'Added content',
      deletedContent: 'Deleted content',
      emptyContent: 'No content to display',
      loading: 'Loading memory changes...',
      loadFailed: 'Could not load memory changes',
      retry: 'Retry',
      empty: 'This session commit did not produce any memory changes.',
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
      header: 'Terminal',
      history: 'Command history',
      historyTitle: 'Command history',
      historyDescription: 'Review commands run in this browser.',
      clearHistory: 'Clear command history',
      noHistory: 'No command history',
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

export default activity
