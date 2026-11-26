import { OV_PATH_BUILDERS, OV_PATHS } from '../../client/paths.js';
import type { CommandSpec } from './command-specs.js';
import { ConsoleCommandError } from './command-messages.js';
import {
  RESOURCE_COMMAND_SPEC,
  tempFileId,
  telemetry,
} from './resource-command-spec.js';
import { COMPILE_COMMAND_SPECS } from './compile-command-specs.js';

function validateSource(source: unknown, uploadId: unknown, label: string) {
  const options = `${label} / --temp-file-id`;
  if (!source && !uploadId)
    throw new ConsoleCommandError('missingArgument', options);
  if (source && uploadId)
    throw new ConsoleCommandError('invalidArgument', options);
}

/** The native skill endpoint accepts Markdown, a URL or a structured Skill. */
function skillSource(source: unknown) {
  if (typeof source !== 'string' || !source.trimStart().startsWith('{'))
    return source;
  try {
    return JSON.parse(source) as unknown;
  } catch {
    throw new ConsoleCommandError('invalidArgument', '<skill-content-or-url>');
  }
}

export const CORE_COMMAND_SPECS: readonly CommandSpec[] = [
  RESOURCE_COMMAND_SPEC,
  {
    name: 'add-skill',
    positional: [
      {
        name: 'data',
        placeholder: 'skill-content-or-url',
        pattern: /\S/,
        optional: true,
      },
    ],
    flags: [
      {
        name: 'parent-auto-create',
        valueKey: 'target_uri',
        kind: 'uri',
        alias: '-p',
        aliases: ['--target-uri'],
      },
      tempFileId,
      { name: 'wait', kind: 'boolean', alias: '-w' },
      telemetry,
    ],
    skeleton: 'write',
    request: ({ data, ...body }) => {
      validateSource(data, body.temp_file_id, '<skill-content-or-url>');
      return {
        method: 'POST',
        path: OV_PATHS.skills,
        body: data === undefined ? body : { ...body, data: skillSource(data) },
      };
    },
  },
  {
    name: 'add-memory',
    positional: [{ name: 'content', rest: true, pattern: /\S/ }],
    flags: [
      {
        name: 'session-id',
        valueKey: 'session_id',
        placeholder: 'id',
        pattern: /\S/,
        required: true,
      },
      {
        name: 'role',
        choices: ['user', 'assistant'],
        defaultValue: 'user',
        hidden: true,
      },
    ],
    skeleton: 'write',
    execute: async ({ content, session_id: sessionId, role }, request) => {
      if (typeof sessionId !== 'string')
        throw new ConsoleCommandError('invalidArgument', '--session-id');
      const message = await request({
        method: 'POST',
        path: OV_PATH_BUILDERS.sessionMessages(sessionId),
        body: { role, content },
      });
      const commit = await request({
        method: 'POST',
        path: OV_PATH_BUILDERS.sessionCommit(sessionId),
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
  ...COMPILE_COMMAND_SPECS,
];
