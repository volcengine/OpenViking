import type {
  CommandConsoleCommandDefinition,
  CommandConsoleSkeletonKind,
} from '@openviking/react/components/command-console';

type Entry = readonly [
  string,
  string,
  string,
  CommandConsoleSkeletonKind,
  readonly string[],
  readonly string[],
];
const ENTRIES: readonly Entry[] = [
  ['help', 'Show available commands', '查看可用命令', 'generic', [], []],
  [
    'backup',
    'Back up context',
    '备份上下文',
    'write',
    ['<output.ovpack>'],
    ['--include-vectors'],
  ],
  [
    'restore',
    'Restore a backup',
    '恢复备份',
    'write',
    ['<backup.ovpack>'],
    [
      '--on-conflict <fail|overwrite|skip>',
      '--vector-mode <auto|recompute|require>',
    ],
  ],
  [
    'task list',
    'List async tasks',
    '列出异步任务',
    'status',
    [],
    ['--task-type <type>', '--status <status>'],
  ],
  [
    'task status',
    'Read task status',
    '查看任务状态',
    'status',
    ['<task-id>'],
    [],
  ],
  ['task cancel', 'Cancel a task', '取消任务', 'write', ['<task-id>'], []],
  [
    'session new',
    'Create a session',
    '创建会话',
    'write',
    [],
    ['--session-id <id>'],
  ],
  ['session list', 'List sessions', '列出会话', 'status', [], []],
  ['session get', 'Read a session', '查看会话', 'status', ['<session-id>'], []],
  [
    'session get-session-context',
    'Read session context',
    '查看会话上下文',
    'status',
    ['<session-id>'],
    ['--token-budget <n>'],
  ],
  [
    'session get-session-archive',
    'Read a session archive',
    '查看会话归档',
    'status',
    ['<session-id>', '<archive-id>'],
    [],
  ],
  [
    'session delete',
    'Delete a session',
    '删除会话',
    'write',
    ['<session-id>'],
    [],
  ],
  [
    'session add-message',
    'Add a session message',
    '添加会话消息',
    'write',
    ['<session-id>'],
    ['--role <role>!', '--content <content>!', '--peer-id <id>'],
  ],
  [
    'session add-messages',
    'Add session messages',
    '批量添加会话消息',
    'write',
    ['<session-id>', '<messages-json>'],
    [],
  ],
  [
    'session commit',
    'Commit session memory',
    '提交会话记忆',
    'write',
    ['<session-id>'],
    [],
  ],
];

export function createAdditionalCommandConsoleCommands(
  locale: 'en-US' | 'zh-CN',
): CommandConsoleCommandDefinition[] {
  return ENTRIES.map(([name, en, zh, skeleton, args, flags]) => ({
    name,
    group: name.startsWith('task ')
      ? 'status'
      : name === 'help'
        ? 'interactive'
        : 'session',
    description: locale === 'zh-CN' ? zh : en,
    inputText: `ov ${name}`,
    usage:
      `ov ${name} ${[...args, ...flags.map((flag) => (flag.endsWith('!') ? flag.slice(0, -1) : `[${flag}]`))].join(' ')}`.trim(),
    skeleton,
    params: args.map((label) => ({
      label,
      required: true,
      description: label,
    })),
    options: flags.map((flag) => ({
      token: flag.replace(/!$/, ''),
      required: flag.endsWith('!'),
      description: flag.replace(/!$/, ''),
    })),
  }));
}
