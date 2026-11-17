import { coreParameters } from './core-parameters.js';
import filesystemParameters from './filesystem-parameters.js';
import { SEARCH_PARAMETERS_EN_US } from './search-parameters.js';
import type { ConsoleCommandGuide, ConsoleLabels } from '../types.js';

const commands = {
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
      | 'commandTitle'
      | 'commandDescription'
      | 'quickStart'
    >
  >,
  taskCompletion: {
    title: 'Task complete',
    description: 'Background task completed.',
    resource: 'The resource import is complete. Click to view the result.',
    viewResult: 'View result',
  },
  commands,
  commandGuides: {
    'add-resource': {
      description:
        'Import a web or repository URL into Resources. The browser terminal does not accept local file paths.',
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
    'compile create': {
      description:
        'Save sources, destination, and Skill without starting compilation.',
      examples: [
        'ov compile create --compile-name demo-wiki --from viking://resources/source --to viking://resources/wiki --skill viking://system/compile/example-skills/llm-wiki',
      ],
    },
    compile: {
      description: 'Run a saved compilation configuration by name.',
      examples: ['ov compile --compile-name demo-wiki'],
      quickStart: {
        documentation: {
          label: 'Compilation guide',
          href: 'https://docs.openviking.ai/en/context-compilation/01-overview',
        },
        steps: [
          {
            description:
              'Verify that OpenViking is configured to run compilation.',
          },
          { description: 'Import the source material with add-resource.' },
          {
            description:
              'Save the sources, destination, and Skill as a named configuration.',
            command:
              'ov compile create --compile-name <name> --from <source-uri> --to <target-uri> --skill <skill-uri>',
          },
          {
            description:
              'Run the saved configuration with its sources, destination, Skill, instructions, model, and schedule.',
            command: 'ov compile --compile-name <name>',
          },
          {
            description: 'Check the run status with the returned task ID.',
            command: 'ov task status <task_id>',
          },
        ],
      },
    },
  } satisfies Partial<Record<keyof typeof commands, ConsoleCommandGuide>>,
  parameters: {
    path: 'Remote path or URL to import',
    to: 'Destination URI',
    parent: 'Existing parent directory URI',
    'parent-auto-create': 'Create the parent directory if missing',
    reason: 'Reason for importing',
    args: 'Parser options as a JSON object',
    'compile-args':
      'JSON object for model_name, auto_update, and update_schedule',
    'target-uri': 'Destination skills root',
    content: 'Plain text to remember',
    'session-id': 'Session to append and commit',
    limit: 'Maximum number of results',
    'compile-name':
      'Case-sensitive name using Han characters, ASCII letters, digits, underscores or hyphens, with no leading hyphen. Up to 64 characters for ASCII-only names or 32 if the name contains Han characters.',
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
