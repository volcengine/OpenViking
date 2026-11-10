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
    find: 'Find matching context',
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
  availableCommands: 'Available commands',
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
