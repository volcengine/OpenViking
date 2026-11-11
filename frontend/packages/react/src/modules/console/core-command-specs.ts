import type { CommandSpec } from './command-specs.js';
import { ConsoleCommandError } from './command-messages.js';

const wikiSkill = 'viking://system/compile/example-skills/llm-wiki/SKILL.md';
const skillUrl =
  'https://github.com/volcengine/OpenViking/tree/skills/llm-wiki';
const repository = 'https://github.com/volcengine/OpenViking/blob/main/';

function jsonOptions(
  input: string | number | boolean,
): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(String(input));
    if (value && typeof value === 'object' && !Array.isArray(value))
      return value as Record<string, unknown>;
  } catch {
    // Parser errors and non-object input use the same command error.
  }
  throw new ConsoleCommandError('invalidArgument', '--args');
}

export const CORE_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'add-resource',
    positional: [{ name: 'path' }],
    flags: [
      { name: 'to', kind: 'uri' },
      { name: 'parent', kind: 'uri' },
      { name: 'parent-auto-create', kind: 'uri', alias: '-p' },
      { name: 'reason' },
      { name: 'args' },
      { name: 'wait', kind: 'boolean' },
    ],
    skeleton: 'write',
    request: ({ 'parent-auto-create': autoParent, args, ...body }) => {
      if ([body.to, body.parent, autoParent].filter(Boolean).length > 1)
        throw new ConsoleCommandError(
          'invalidArgument',
          '--to / --parent / -p',
        );
      return {
        method: 'POST',
        path: '/api/v1/resources',
        body: {
          ...body,
          ...(autoParent ? { parent: autoParent, create_parent: true } : {}),
          ...(args === undefined ? {} : { args: jsonOptions(args) }),
        },
      };
    },
  },
  {
    name: 'add-skill',
    positional: [{ name: 'path' }],
    flags: [
      { name: 'target-uri', kind: 'uri', alias: '-p' },
      { name: 'wait', kind: 'boolean' },
    ],
    skeleton: 'write',
    request: ({ path, 'target-uri': target, ...rest }) => ({
      method: 'POST',
      path: '/api/v1/skills',
      body: { data: path, target_uri: target, ...rest },
    }),
  },
  {
    name: 'add-memory',
    positional: [{ name: 'content' }],
    flags: [{ name: 'session-id', required: true }],
    skeleton: 'write',
    execute: async ({ content, 'session-id': sessionId }, request) => {
      const session =
        '/api/v1/sessions/' + encodeURIComponent(String(sessionId));
      const message = await request({
        method: 'POST',
        path: session + '/messages',
        body: { role: 'user', content },
      });
      const commit = await request({
        method: 'POST',
        path: session + '/commit',
        body: {},
      });
      return {
        session_id: sessionId,
        task_id: commit.task_id,
        message_count: message.message_count,
        archive_uri: commit.archive_uri,
        status: commit.status,
      };
    },
  },
  ...(['compile', 'compile create'] as const).map((name): CommandSpec => ({
    name,
    flags: [
      { name: 'from', kind: 'uri', required: true },
      { name: 'to', kind: 'uri', required: true },
      { name: 'skill', kind: 'uri', defaultValue: wikiSkill },
      { name: 'instruction' },
    ],
    skeleton: 'write',
    request: ({ from, skill, ...body }) => ({
      method: 'POST',
      path: '/api/v1/compile',
      body: {
        ...body,
        from: [from],
        skill: skill ?? wikiSkill,
        // Explicit scheduling settings save the config without queuing a compile.
        ...(name === 'compile create' ? { args: { auto_update: false } } : {}),
      },
    }),
  })),
];

export const CORE_COMMAND_GUIDES = {
  'zh-CN': {
    'add-resource': {
      description:
        '把网页 URL 或仓库地址导入 Resources，供后续检索、阅读和引用。浏览器终端不接收本地文件路径。',
      examples: [
        'ov add-resource ' + repository + 'docs/repository/README_CN.md',
      ],
    },
    'add-skill': {
      description: '从远程地址安装 Skill，可通过 -p 选择目标技能目录。',
      examples: ['ov add-skill ' + skillUrl + ' -p viking://agent/skills'],
    },
    'add-memory': {
      description: '将事实、偏好或协作约定写入指定会话，再提交会话以提取记忆。',
      examples: [
        'ov add-memory "评审文档优先用表格对比方案" --session-id ov-tui-demo',
      ],
    },
    find: {
      description:
        '按搜索词在当前上下文中做语义检索，返回匹配的资源、记忆或技能。',
      examples: ['ov find "OpenViking 有什么价值？"'],
    },
    'compile create': {
      description: `保存来源、目标和 Skill 的编译配置，不立即启动编译。`,
      examples: [
        'ov compile create --from viking://resources/source --to viking://resources/wiki',
      ],
    },
    compile: {
      description:
        '默认使用内置 llm-wiki Skill 整理资料，将 Wiki 写入目标目录。可通过 --skill 指定其他 Skill。',
      examples: [
        'ov compile --from viking://resources/source --to viking://resources/wiki',
      ],
    },
  },
  'en-US': {
    'add-resource': {
      description:
        'Import a web or repository URL into Resources. The browser terminal does not accept local file paths.',
      examples: ['ov add-resource ' + repository + 'README.md'],
    },
    'add-skill': {
      description:
        'Install a Skill from a remote source. Use -p to select the destination skills directory.',
      examples: ['ov add-skill ' + skillUrl + ' -p viking://agent/skills'],
    },
    'add-memory': {
      description:
        'Add a fact, preference, or convention to a session, then commit the session to extract memory.',
      examples: [
        'ov add-memory "Prefer tables for comparing options in reviews" --session-id ov-tui-demo',
      ],
    },
    find: {
      description:
        'Search for relevant resources, memories, and skills in the current context.',
      examples: ['ov find "What is the value of OpenViking?"'],
    },
    'compile create': {
      description: `Save sources, destination, and Skill without starting compilation.`,
      examples: [
        'ov compile create --from viking://resources/source --to viking://resources/wiki',
      ],
    },
    compile: {
      description:
        'Compile source material into a Wiki using the built-in llm-wiki Skill. Use --skill to choose another Skill.',
      examples: [
        'ov compile --from viking://resources/source --to viking://resources/wiki',
      ],
    },
  },
};
