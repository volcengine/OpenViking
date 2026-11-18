import { OV_PATH_BUILDERS, OV_PATHS } from '../../client/paths.js';
import type { CommandSpec } from './command-specs.js';
import type { Argument } from './command-parser.js';
import { ConsoleCommandError } from './command-messages.js';
import { COMPILE_COMMAND_SPECS } from './compile-command-specs.js';

const tempFileId: Argument = {
  name: 'temp-file-id',
  valueKey: 'temp_file_id',
  placeholder: 'id',
  pattern: /\S/,
};
const telemetry: Argument = {
  name: 'telemetry',
  kind: 'boolean-or-json',
  placeholder: 'true|json',
};

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
  {
    name: 'add-resource',
    positional: [
      {
        name: 'path',
        descriptionKey: 'resource-source',
        placeholder: 'path-or-url',
        pattern: /\S/,
        optional: true,
      },
    ],
    flags: [
      tempFileId,
      { name: 'to', kind: 'uri' },
      { name: 'parent', kind: 'uri' },
      { name: 'parent-auto-create', kind: 'uri', alias: '-p' },
      {
        name: 'create-parent',
        valueKey: 'create_parent',
        kind: 'boolean',
        hidden: true,
      },
      { name: 'reason', placeholder: 'text' },
      {
        name: 'instruction',
        placeholder: 'text',
        descriptionKey: 'resource-instruction',
      },
      { name: 'ignore-dirs', valueKey: 'ignore_dirs', placeholder: 'names' },
      { name: 'include', placeholder: 'patterns' },
      { name: 'exclude', placeholder: 'patterns' },
      { name: 'strict', kind: 'boolean', hidden: true },
      {
        name: 'directly-upload-media',
        valueKey: 'directly_upload_media',
        kind: 'boolean',
        hidden: true,
      },
      {
        name: 'preserve-structure',
        valueKey: 'preserve_structure',
        kind: 'boolean',
        hidden: true,
      },
      { ...telemetry, hidden: true },
      {
        name: 'watch-interval',
        valueKey: 'watch_interval',
        kind: 'number',
        min: 0,
        placeholder: 'minutes',
        helpDefault: 0,
      },
      { name: 'args', kind: 'json-object', placeholder: 'json-object' },
      {
        name: 'acl',
        descriptionKey: 'resource-acl',
        kind: 'json-object',
        placeholder: 'json-object',
      },
      { name: 'wait', kind: 'boolean' },
    ],
    skeleton: 'write',
    request: ({ 'parent-auto-create': autoParent, ...body }) => {
      validateSource(body.path, body.temp_file_id, '<path-or-url>');
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
        },
      };
    },
  },
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
      { name: 'target-uri', valueKey: 'target_uri', kind: 'uri', alias: '-p' },
      tempFileId,
      { name: 'wait', kind: 'boolean' },
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
