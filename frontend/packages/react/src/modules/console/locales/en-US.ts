import type { ConsoleLabels } from '../types.js';

const messages = {
  labels: {
    conversation: 'VikingBot',
    command: 'Terminal',
    mode: 'Panel mode',
    description:
      'Connect your resources, memories and context to turn your data into usable knowledge.',
    clear: 'New session',
    history: 'Command history',
    emptyHistory: 'No command history yet',
  } satisfies Required<
    Omit<
      ConsoleLabels,
      'tui' | 'vikingBot' | 'commandTitle' | 'commandDescription' | 'quickStart'
    >
  >,
  commands: {
    'add-resource': 'Add a resource from a URL',
    'add-skill': 'Install a Skill',
    'add-memory': 'Add memory directly',
    compile: 'Compile material with a Skill',
    'compile create': 'Create a compile configuration',

    grep: 'Match text in resources',
    glob: 'Match resource paths',
    help: 'Show available commands',
    clear: 'Clear local records',
    health: 'Check server health',
    status: 'Show system status',
    ls: 'List directory entries',
    tree: 'List a resource tree',
    stat: 'Inspect a resource',
    read: 'Read file content',
    abstract: 'Read an L0 abstract',
    overview: 'Read an L1 overview',
    find: 'Find relevant context',
    search: 'Search with query planning',
    mkdir: 'Create a directory',
    cp: 'Copy a resource',
    mv: 'Move a resource',
    rm: 'Remove a resource',
    write: 'Write text content',
    'task list': 'List background tasks',
    'task status': 'Inspect a background task',
    'task cancel': 'Request task cancellation',
  },
  parameters: {
    path: 'Remote path or URL to import',
    to: 'Destination URI',
    parent: 'Existing parent directory URI',
    'parent-auto-create': 'Create the parent directory if missing',
    reason: 'Reason for importing',
    args: 'Parser options as a JSON object',
    wait: 'Wait for processing to complete',
    'target-uri': 'Destination skills root',
    content: 'Plain text to remember',
    'session-id': 'Session to append and commit',
    query: 'Semantic search query',
    target_uri: 'Limit retrieval to this URI',
    limit: 'Maximum number of results',
    level: 'Filter content levels, e.g. 0,1,2',
    from: 'Source URI to compile',
    skill: 'Compiler Skill URI',
    instruction: 'Additional compilation instructions',
  },
  commandGroups: {
    core: 'Core workflow',
    filesystem: 'Filesystem',
    search: 'Search & context',
    status: 'Config & status',
  },
  messages: {
    cancelled: () =>
      'Request cancelled; the server may still process an accepted operation.',
    unknownCommand: () =>
      'Unknown command. Enter ov help for available commands.',
    shellSyntax: () => 'Shell syntax is not supported.',
    unclosedQuote: () => 'Unclosed quote or escape.',
    unexpectedArgument: (value: string) => `Unexpected argument: ${value}`,
    duplicateArgument: (value: string) => `Duplicate argument: ${value}`,
    missingValue: (value: string) => `Missing value: ${value}`,
    expectedUri: (value: string) => `Expected a Viking URI: ${value}`,
    invalidArgument: (value: string) => `Invalid ${value}`,
    missingArgument: (value: string) => `Missing ${value}`,
  },
};
export default messages;
