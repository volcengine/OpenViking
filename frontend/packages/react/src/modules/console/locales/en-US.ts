import { compileHelp } from './compile-help.js';
import { transferErrors, transferSession } from './transfer-session.en-US.js';
import { coreParameters } from './core-parameters.js';
import filesystemParameters from './filesystem-parameters.js';
import { SEARCH_PARAMETERS_EN_US } from './search-parameters.js';
import type { ConsoleCommandGuide, ConsoleLabels } from '../types.js';

const commands = {
  ...transferSession,
  'add-resource': 'Import a web URL, repository, or local file',
  'add-skill': 'Install a Skill',
  'add-memory': 'Add memory directly',
  compile: 'Run compilation',
  'compile create': 'Create a configuration to run with compile',

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
};

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
      | 'commandConsole'
      | 'vikingBot'
      | 'activationTitle'
      | 'activationDescription'
      | 'commandTitle'
      | 'commandDescription'
      | 'quickStart'
    >
  >,
  noTasks: 'No tasks',
  grepLimitReached: 'The search reached its limit. More matches may exist.',
  telemetryResult: (value: string) => `Telemetry: ${value}`,
  taskResult: 'Task result',
  memoryCommit: {
    idle: 'Commit session',
    pending: 'Committing session',
    submitted: 'Committing session',
    success: 'Session committed',
    empty: 'There are no new messages to extract memories from.',
    skipped: 'No new memories were generated.',
    failed: 'Could not submit the session',
    completed: 'Memory extraction completed.',
    saved: 'Memories were generated and saved.',
    view: 'View memory',
  },
  compileQuickInputs: {
    createWiki: {
      label: 'Create Wiki',
      prompt:
        'Organize the files in the selected directory into a Wiki, preserving sources and the relationships between documents',
    },
    dailyReport: {
      label: 'Schedule daily report',
      prompt:
        'Generate a daily report from the selected directory and schedule updates',
    },
  },
  commands,
  localResourceImportHelp:
    'Enter a web or repository URL to import it directly. Run the command by itself or with only a local path to open the Local upload page and choose files or folders.',
  commandGuides: {
    'add-resource': {
      description:
        'Enter a web or repository URL to import it directly. For local uploads, choose a file in the browser.',
      examples: [
        'ov add-resource https://github.com/volcengine/OpenViking/blob/main/README.md',
      ],
    },
    'add-skill': {
      description:
        'Install a Skill from a remote source. Use -p to select the destination skills directory.',
      examples: [
        'ov add-skill https://github.com/volcengine/OpenViking/tree/skills/llm-wiki -p viking://agent/skills',
      ],
    },
    'add-memory': {
      description:
        'Add a fact, preference, or convention to a session, then commit the session to extract memory.',
      examples: [
        'ov add-memory "Prefer tables for comparing options in reviews" --session-id ov-command-console-demo',
      ],
    },
    find: {
      description:
        'Search for relevant resources, memories, and skills in the current context.',
      examples: ['ov find "What is the value of OpenViking?"'],
    },
    'compile create': compileHelp['en-US'].createGuide,
    compile: compileHelp['en-US'].runGuide,
  } satisfies Partial<Record<keyof typeof commands, ConsoleCommandGuide>>,
  compileArgsHelp: compileHelp['en-US'].args,
  parameters: {
    path: 'Remote path or URL to import',
    to: 'Destination URI',
    parent: 'Existing parent directory URI',
    'parent-auto-create': 'Create the parent directory if missing',
    reason: 'Reason for importing',
    args: 'Parser options as a JSON object',
    'compile-args':
      'Valid JSON object for model and scheduling settings. Field names are case-sensitive.',
    'target-uri': 'Destination skills root',
    content: 'Plain text to remember',
    'session-id': 'Session to append and commit',
    limit: 'Maximum number of results',
    'compile-name':
      'Case-sensitive name using Han characters, ASCII letters, digits, underscores or hyphens, with no leading hyphen. Up to 64 characters for ASCII-only names or 32 if the name contains Han characters. Names must be unique per user and cannot be renamed.',
    'compile-from':
      'At least one existing readable source directory. Individual files are unsupported. Repeat --from or separate URIs with commas. Duplicate URIs are removed.',
    'compile-to':
      'Writable output directory. The server can create it if needed.',
    from: 'Existing source directory URIs. Repeat --from or separate URIs with commas',
    skill: 'Compiler Skill URI',
    instruction: 'Additional compilation instructions',
    'task-type': 'Filter by task type, for example add_resource or add_skill',
    'task-status':
      'Filter by task status, for example pending, running, completed, or failed',
    'task-id': 'ID of the background task',
    ...coreParameters['en-US'],
    ...filesystemParameters['en-US'],
    ...SEARCH_PARAMETERS_EN_US,
  },
  commandGroups: {
    transfer: 'File transfer',
    session: 'Sessions',
    core: 'Core workflow',
    filesystem: 'Filesystem',
    search: 'Search & context',
    status: 'Config & status',
  },
  messages: {
    localResourceImportOpened: () =>
      'Choose files or folders on the Local upload page.',
    localResourceImportFlags: () =>
      'Use Local upload to configure the import, or use --temp-file-id to apply command options.',
    cancelled: () =>
      'Request cancelled; the server may still process an accepted operation.',
    ...transferErrors,
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
    compileNamedRunInputs: () =>
      'Use compile create to save inputs, then run compile with --compile-name alone.',
  },
};
export default messages;

export const renderErrors = {
  console: 'Console could not be displayed.',
  toolbar: 'Toolbar could not be displayed.',
  command: 'Command panel could not be displayed.',
};
