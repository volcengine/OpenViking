import { OV_PATH_BUILDERS, OV_PATHS } from '../../client/paths.js';
import type { CommandSpec } from './command-specs.js';
import { ConsoleCommandError } from './command-messages.js';

const wikiSkill = 'viking://system/compile/example-skills/llm-wiki/SKILL.md';

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
        path: OV_PATHS.resources,
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
      path: OV_PATHS.skills,
      body: { data: path, target_uri: target, ...rest },
    }),
  },
  {
    name: 'add-memory',
    positional: [{ name: 'content' }],
    flags: [{ name: 'session-id', required: true }],
    skeleton: 'write',
    execute: async ({ content, 'session-id': sessionId }, request) => {
      const id = String(sessionId);
      const message = await request({
        method: 'POST',
        path: OV_PATH_BUILDERS.sessionMessages(id),
        body: { role: 'user', content },
      });
      const commit = await request({
        method: 'POST',
        path: OV_PATH_BUILDERS.sessionCommit(id),
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
      path: OV_PATHS.compile,
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
