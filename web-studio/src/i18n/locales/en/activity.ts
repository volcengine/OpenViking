const activity = {
  sessions: {
    page: {
      placeholder: 'Sessions and Bot workspace is under construction.',
    },
    threadList: {
      title: 'Sessions',
      backToList: 'Back to sessions',
      newSession: 'New Session',
      count: '{{count}} session',
      count_other: '{{count}} sessions',
      loading: 'Loading sessions...',
      loadFailed: 'Could not load sessions',
      retry: 'Retry',
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
      createFailed: 'Could not create session: {{error}}',
      deleteFailed: 'Could not delete session: {{error}}',
      shortcut: '⌘ N to create a new session',
    },
    chat: {
      emptyReadDescription: 'This session has no messages yet.',
      historyLoadFailed: 'Could not load conversation history: {{error}}',
      sendFailed: 'Could not send message: {{error}}',
      copy: 'Copy',
      emptyDescription: 'Explore your knowledge base and start a conversation.',
      placeholder: 'Type a message...',
      emptyState: 'Select or create a session to start chatting.',
      thinking: 'Thinking...',
      generating: 'Generating',
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
      viewExperienceImpact: 'View impact for this experience',
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
  filesystem: {
    copyUri: 'Copy current URI',
    copied: 'URI copied',
    copyFailed: 'Copy failed',
    resizeContext: 'Resize filesystem width',
    resizeAction: 'Resize Terminal and Agent width',
    readFailed: 'Failed to read {{uri}}',
    tabs: {
      terminal: 'Terminal',
      agent: 'Agent',
    },
    actionPanel: {
      collapse: 'Collapse panel',
      expand: 'Open panel',
    },
    addResource: {
      title: 'Add resource',
      description:
        'After it finishes, the filesystem on the left refreshes and the Terminal on the right can locate the new resource.',
      submitted: 'Resource add task submitted',
    },
    explorer: {
      title: 'Filesystem',
      addResource: 'Add resource',
      abstractLevel: 'L0',
      collapseDirectory: 'Collapse {{name}}',
      empty: 'empty',
      expandDirectory: 'Expand {{name}}',
      loading: 'loading',
      overviewLevel: 'L1',
      search: 'Search context',
      refresh: 'Refresh tree',
      namespaces: {
        agent: 'Agent capabilities, tools, and experience',
        user: 'Personalized user memories',
        resources: 'External resources the Agent can reference',
        system: 'Built-in system resources',
      },
    },
    agent: {
      history: 'Session history',
      newSession: 'New session',
      creating: 'Creating Filesystem session...',
      detectingBot: 'Detecting bot mode...',
      createFailed: 'Failed to create session: {{error}}',
      retry: 'Retry',
      botDisabledFooter: 'Enable bot mode to chat with the Agent',
      historyTitle: 'Agent session history',
      historyDescription:
        'Conversations are shared with VikingBot, including legacy Agent sessions saved in this browser.',
      loadingSessions: 'Loading sessions...',
      noSessions: 'No session history yet',
      createTimeout:
        'Creating the Filesystem session timed out. Check your connection settings and try again.',
      newSessionTitle: 'New Filesystem session',
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
      welcomeTitle: 'Terminal connected to the filesystem',
      welcomeBody:
        'Run /status, /ls, /find, /search, /read, /write, or /add-resource. Use --uri viking://resources/... to limit a search to a directory.',
      scopeLabel: 'cwd: {{uri}}',
      globalScope: 'global',
      opened: 'Resource opened',
      onlineTitle: 'Service online',
      onlineBody:
        'OpenViking API responded normally; found {{count}} nodes under the root.',
      lsBody: 'Showing {{count}} nodes under {{uri}}.',
      fileEmpty: 'File is empty; opened in the middle preview.',
      searchUsage: 'Usage: {{name}} <query> [--uri viking://resources/...]',
      searchScopeLine: 'Search scope: {{scope}}',
      patternUsage:
        'Usage: {{name}} <pattern> [--uri viking://...] [--node-limit n]',
      patternHits: '{{count}} matches under {{uri}}.',
      helpParameters: 'Parameters',
      parameterName: 'Parameter',
      parameterRequired: 'Required',
      parameterDescription: 'Description',
      parameterDefault: 'Default',
      parameterTypes: {
        Text: 'Text',
        URI: 'URI',
        Number: 'Number',
        Boolean: 'Boolean',
      },
      yes: 'Yes',
      no: 'No',
      helpExamples: 'Examples',
      helpSubcommands: 'Subcommands',
      noParameters: 'No parameters',
      currentScopeAction: 'Use current directory',
      readUsage: 'Usage: /read viking://resources/...',
      enterUri: 'Please enter a viking:// URI',
      hits: 'Hit {{resources}} resources, {{memories}} memories, {{skills}} skills.',
      resourceProcessing: 'Task submitted and processing. Task ID: {{taskId}}',
      resourceCompleted: 'Import completed. Opened {{uri}}.',
      resourceFailed: 'Resource import was not successful.',
      resourceTaskFailed: 'Import failed: {{reason}}\nTask ID: {{taskId}}',
      backgroundTaskFailed: 'Task failed: {{reason}}\nTask ID: {{taskId}}',
      resourceStillProcessing:
        'Task is still processing. Check Task Center. Task ID: {{taskId}}',
      viewResourceTask: 'View task status',
      taskCompleted: 'Task completed',
      taskCompletedHint: 'The task is complete. View the result.',
      viewResult: 'View result',
      taskDetails: {
        taskId: 'Task ID:',
        rootUri: 'Root URI:',
        uri: 'URI:',
        name: 'Name:',
        auxiliaryFiles: 'Auxiliary files:',
      },
      sessionUsage:
        'Usage: /session [current|list|create|switch|get|context|messages|archive|commit|message|tool-results|tool-result|tool-search|delete] ...',
      sessionDeleteUsage: 'Usage: /session delete <session_id>',
      sessionMissing:
        'No active session. Open the Agent panel to create one, or pass a session_id.',
      sessionCurrentBody: 'Current active session: {{id}}',
      sessionListBody: '{{count}} sessions.',
      sessionCreatedBody: 'Created and switched to session: {{id}}',
      sessionSwitchedBody: 'Switched to session: {{id}}',
      sessionDeletedBody: 'Deleted session: {{id}}',
      sessionMessageAddedBody: 'Added a message to session {{id}}.',
      unknownCommand: 'Unknown command. Type / to browse available commands.',
      addMemoryUsage:
        'Usage: /add-memory "memory content" [--session-id <session ID>]',
      addMemorySubmitted:
        'Session committed; memory extraction is running in the background.',
      memoryCompleted: 'Background processing completed for session {{id}}.',
      skillCompleted: 'Skill processing completed; view it on the Skills page.',
      addSkillUsage:
        'Usage: /add-skill <Git URL> [-p viking://agent/skills]. Upload local files on the Skills page.',
      addResourceUsage:
        'Usage: /add-resource <URL> [--to <URI>|--parent <URI>] [--args <JSON>] [--reason <text>]. Use Add Resource above the file tree for visual import.',
      mkdirUsage: 'Usage: /mkdir <viking://URI> [--description "text"]',
      mvUsage: 'Usage: /mv <source viking://URI> <target viking://URI>',
      cpUsage:
        'Usage: /cp <source viking://URI> <target viking://URI> [-r|--recursive]',
      mkdirCreated: 'Created directory: {{uri}}',
      mvCompleted: 'Moved: {{from}} → {{to}}',
      cpCompleted: 'Copied: {{from}} → {{to}}',
      rmUsage:
        'Usage: /rm <viking://URI> [-r|--recursive]. An explicit target is required.',
      rmConfirm: 'Delete {{uri}}? This cannot be undone.',
      rmConfirmRecursive:
        'Delete {{uri}} and all files and subdirectories inside it? This cannot be undone.',
      rmCancelled: 'Deletion cancelled',
      rmDeleted: 'Deleted: {{uri}}',
      writeUsage:
        'Usage: /write <URI> --content "text" [--mode replace|append|create] [--wait]',
      waitUsage: 'Usage: /wait (the current open-source API takes no timeout)',
      addSkillSubmitted:
        'Skill submitted for processing; check the Skills page.',
      commandFailed: 'Command failed',
      running: 'Running command...',
      placeholder: 'Enter a CLI command, e.g. /status',
      inputHint: 'Type / for commands · ↑↓ select · Enter complete',
      suggestionsTitle: 'Command suggestions',
      suggestionsHint: '↑↓ select · Tab complete · Enter run',
      paletteGroups: {
        core: 'Core workflows',
        filesystem: 'Filesystem',
        search: 'Search and context',
        status: 'Configuration and status',
        history: 'Recent commands',
        resource: 'Resource paths',
        subcommand: 'Session commands',
      },
      quickStart: {
        title: 'Quick start',
        heading: 'OpenViking CLI',
        description: 'Work with context directly from the CLI',
        addResource: {
          title: 'Add a resource',
          command: '/add-resource',
          code: 'Import docs or files into viking://resources',
        },
        addSkill: {
          title: 'Add a Skill',
          command: '/add-skill',
          code: 'Enter a Git URL to add a personal or shared Skill',
        },
        addMemory: {
          title: 'Add memory',
          command: '/add-memory',
          code: 'Enter memory content; optionally target an existing session',
        },
        find: {
          title: 'Find related context',
          command: 'ov find "What is OpenViking useful for?"',
          code: 'Search resources, memories, and skills from the current scope',
        },
        compile: {
          title: 'Compile sources',
          command: '/compile',
          code: 'Choose source and output directories plus a Skill',
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
        compileFrom: {
          name: '--from <source directory>',
          description:
            'Required. A viking:// source path; repeat for multiple sources.',
        },
        compileTo: {
          name: '--to <output directory>',
          description: 'Required. The viking:// directory for compiled output.',
        },
        compileSkill: {
          name: '--skill <Skill URI>',
          description:
            'Required. An installed Skill used to organize the sources.',
        },
        skillSource: {
          name: '<Git URL>',
          description:
            'Required. A Git repository or GitHub directory containing SKILL.md; upload local files on the Skills page.',
        },
        skillScope: {
          name: '--scope user|agent',
          description:
            'Optional. Defaults to a personal Skill; agent shares it with the account.',
        },
        skillParent: {
          name: '-p <URI>',
          description: 'Optional Skill root; defaults to viking://~/skills.',
        },
        resourceSource: {
          name: '<URL>',
          description:
            'Optional remote URL; omit to open the local upload form.',
        },
        resourceTo: {
          name: '--to <URI>',
          description:
            'Optional exact destination URI; cannot be combined with --parent.',
        },
        resourceParent: {
          name: '--parent <URI>',
          description:
            'Optional parent directory; cannot be combined with --to.',
        },
        resourceReason: {
          name: '--reason <text>',
          description: 'Optional reason for importing this resource.',
        },
        resourceArgs: {
          name: '--args <JSON>',
          description:
            'Optional parser arguments, such as {"branch":"main"} for Git.',
        },
        writeContent: {
          name: '--content <text>',
          description: 'Required text to write. Quote text with spaces.',
        },
        writeMode: {
          name: '--mode <mode>',
          description:
            'Optional replace, append, or create; defaults to replace.',
        },
        sourceUri: {
          name: '<source URI>',
          description:
            'Required explicit Viking URI of the source file or directory.',
        },
        targetUri: {
          name: '<target URI>',
          description: 'Required explicit destination Viking URI.',
        },
        directoryDescription: {
          name: '--description <text>',
          description:
            'Optional initial directory description. Quote text containing spaces.',
        },
        recursive: {
          name: '-r, --recursive',
          description:
            'Process a directory and all of its contents when copying or deleting.',
        },
        writeWait: {
          name: '--wait',
          description: 'Optional. Wait for server processing.',
        },
        nodeLimit: {
          name: '--node-limit <n>',
          description:
            'Optional maximum result count; defaults to 10, or 256 for pattern search.',
        },
        threshold: {
          name: '--threshold <score>',
          description: 'Optional minimum result score.',
        },
        searchSessionId: {
          name: '--session-id <id>',
          description: 'Optional session to associate with the search.',
        },
        pattern: {
          name: '<pattern>',
          description:
            'Required content pattern for grep or path pattern for glob.',
        },
        ignoreCase: {
          name: '-i',
          description: 'Optional case-insensitive grep matching.',
        },
        taskAction: {
          name: 'list|status|cancel',
          description:
            'Required action to list compile tasks or inspect/cancel a task.',
        },
        taskId: {
          name: '<task_id>',
          description: 'Required for status and cancel.',
        },
        memoryContent: {
          name: '<memory content>',
          description: 'Required. Quote text containing spaces.',
        },
        memorySessionId: {
          name: '--session-id <session ID>',
          description:
            'Optional existing session; a new session is created when omitted.',
        },
        query: {
          name: 'query',
          description: 'Keywords or a semantic question to search for.',
        },
        scope: {
          name: '--uri <.|uri>',
          description:
            'Optional. Omit for global search; pass . for the current directory; pass uri for a specific directory.',
        },
        sessionAction: {
          name: 'subcommand',
          description:
            'current, list, create, switch, get, context, messages, archive, commit, message, tool-results, tool-result, tool-search, delete.',
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
        compile: {
          default: {
            code: '/compile --from viking://resources/source --to viking://resources/output --skill viking://~/skills/daily-report',
            description: 'Compile a source directory with an installed Skill',
          },
        },
        addSkill: {
          git: {
            code: '/add-skill https://github.com/volcengine/OpenViking/tree/main/examples/compile/ov-compile-skills/daily-report',
            description: 'Add a personal Skill from a GitHub directory',
          },
        },
        addMemory: {
          default: {
            code: '/add-memory "a useful fact"',
            description: 'Create a session and extract memory',
          },
        },
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
            code: '/search agent --uri .',
            description: 'Use the highlighted directory',
          },
          scoped: {
            code: '/search agent --uri viking://resources/',
            description: 'Search only within a directory',
          },
        },
        find: {
          global: {
            code: '/find agent',
            description: 'Find related resources globally',
          },
          current: {
            code: '/find agent --uri .',
            description: 'Use the highlighted directory',
          },
          scoped: {
            code: '/find agent --uri viking://resources/',
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
          remote: {
            code: `ov add-resource https://github.com/volcengine/OpenViking --to viking://resources/openviking-repo --args '{"branch":"main"}'`,
            description: 'Import the main branch of the OpenViking repository',
          },
        },
        mkdir: {
          default: {
            code: '/mkdir viking://resources/new-docs --description "Project docs"',
            description: 'Create a directory with a description',
          },
        },
        mv: {
          default: {
            code: '/mv viking://resources/old.md viking://resources/new.md',
            description: 'Move or rename a file or directory',
          },
        },
        cp: {
          file: {
            code: '/cp viking://resources/notes.md viking://resources/notes-copy.md',
            description: 'Copy one file',
          },
          directory: {
            code: '/cp viking://resources/docs viking://resources/docs-copy --recursive',
            description: 'Copy a directory recursively',
          },
        },
        rm: {
          file: {
            code: '/rm viking://resources/notes.md',
            description: 'Delete a file after confirmation',
          },
          directory: {
            code: '/rm viking://resources/old-docs --recursive',
            description:
              'Delete a directory and its contents after confirmation',
          },
        },
        write: {
          default: {
            code: '/write viking://resources/notes.md --content "New content"',
            description: 'Replace file content',
          },
          append: {
            code: '/write viking://resources/notes.md --content "More content" --append',
            description: 'Append to an existing file',
          },
        },
        grep: {
          default: {
            code: '/grep "OpenViking"',
            description: 'Find text in resources',
          },
          scoped: {
            code: '/grep "OpenViking" --uri viking://resources/',
            description: 'Find text under one directory',
          },
        },
        glob: {
          default: {
            code: '/glob "**/*.md"',
            description: 'Match Markdown file paths',
          },
          scoped: {
            code: '/glob "**/*.md" --uri viking://resources/',
            description: 'Match paths under one directory',
          },
        },
        task: {
          list: {
            code: '/task list --task-type compile',
            description: 'List compile tasks',
          },
          status: {
            code: '/task status <task_id>',
            description: 'Inspect a task',
          },
          cancel: {
            code: '/task cancel <task_id>',
            description: 'Cancel a task',
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
          message: {
            code: '/session message [session_id] user hello',
            description: 'Append a message to a session',
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
          usage: '/ls [<URI>]',
        },
        search: {
          description: 'Semantic search',
          usage: '/search <query> [--uri <URI>] [--node-limit <n>]',
        },
        find: {
          description: 'Find related resources',
          usage: '/find <query> [--uri <URI>] [--node-limit <n>]',
        },
        read: {
          description: 'Read a resource file',
          usage: '/read <URI>',
        },
        compile: {
          description: 'Compile source materials with a Skill',
          usage:
            '/compile --from <source directory> --to <output directory> --skill <Skill URI>',
        },
        addSkill: {
          description: 'Add a Skill from a Git URL',
          usage: '/add-skill <Git URL> [-p <target root>]',
        },
        addMemory: {
          description: 'Add a message to a session and extract memory',
          usage: '/add-memory "memory content" [--session-id <session ID>]',
        },
        addResource: {
          description: 'Add external resources',
          usage:
            '/add-resource <URL> [--to <URI>|--parent <URI>] [--args <JSON>]',
        },
        mkdir: {
          description: 'Create a directory',
          usage: '/mkdir <URI> [--description <text>]',
        },
        mv: {
          description: 'Move or rename a file or directory',
          usage: '/mv <source URI> <target URI>',
        },
        cp: {
          description: 'Copy a file or directory',
          usage: '/cp <source URI> <target URI> [-r|--recursive]',
        },
        rm: {
          description: 'Delete a file or directory after confirmation',
          usage: '/rm <URI> [-r|--recursive]',
        },
        write: {
          description: 'Write or append resource content',
          usage: '/write <URI> --content <text> [--mode replace|append|create]',
        },
        grep: {
          description: 'Search resource text patterns',
          usage: '/grep <pattern> [--uri <URI>]',
        },
        glob: {
          description: 'Search resource paths',
          usage: '/glob <pattern> [--uri <URI>]',
        },
        task: {
          description: 'Inspect and manage compile tasks',
          usage: '/task <list|status|cancel> [<task ID>]',
        },
        session: {
          description: 'Manage Agent sessions',
          usage: '/session <subcommand>',
        },
        tree: {
          description: 'Show directory tree',
          usage: '/tree [<URI>]',
        },
        stat: {
          description: 'Show resource metadata',
          usage: '/stat <URI>',
        },
        abstract: {
          description: 'Read directory abstract',
          usage: '/abstract <URI>',
        },
        overview: {
          description: 'Read directory overview',
          usage: '/overview <URI>',
        },
        health: {
          description: 'Show backend health',
          usage: '/health',
        },
        wait: {
          description: 'Wait for service readiness',
          usage: '/wait',
        },
      },
    },
  },
} as const

export default activity
