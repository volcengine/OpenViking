import { OV_PATH_BUILDERS, OV_PATHS } from '../../client/paths.js';
import type { Argument } from './command-parser.js';
import { ConsoleCommandError } from './command-messages.js';
import type { CommandSpec } from './command-specs.js';

const sessionId: Argument = { name: 'session-id', valueKey: 'session_id' };
const telemetry: Argument = {
  name: 'telemetry',
  kind: 'boolean',
  hidden: true,
};
const content: Argument = { name: 'content', placeholder: 'content' };
const role: Argument = { name: 'role', required: true };
const peerId: Argument = {
  name: 'peer-id',
  valueKey: 'peer_id',
  placeholder: 'id',
};

export const SESSION_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'session new',
    flags: [
      { ...sessionId, aliases: ['--sessionid', '--id'], placeholder: 'id' },
      {
        name: 'memory-policy',
        valueKey: 'memory_policy',
        kind: 'json-object',
        hidden: true,
      },
      telemetry,
    ],
    skeleton: 'write',
    request: (body) => ({ method: 'POST', path: OV_PATHS.sessions, body }),
  },
  {
    name: 'session list',
    skeleton: 'status',
    request: () => ({ method: 'GET', path: OV_PATHS.sessions }),
  },
  {
    name: 'session get',
    positional: [sessionId],
    flags: [
      {
        name: 'auto-create',
        valueKey: 'auto_create',
        kind: 'boolean',
        hidden: true,
      },
    ],
    skeleton: 'status',
    request: ({ session_id, auto_create }) => ({
      method: 'GET',
      path: OV_PATH_BUILDERS.session(session_id as string),
      query: { auto_create: auto_create as boolean | undefined },
    }),
  },
  {
    name: 'session get-session-context',
    positional: [sessionId],
    flags: [
      {
        name: 'token-budget',
        valueKey: 'token_budget',
        kind: 'integer',
        defaultValue: 128_000,
        placeholder: 'n',
      },
    ],
    skeleton: 'status',
    request: ({ session_id, token_budget }) => ({
      method: 'GET',
      path: OV_PATH_BUILDERS.sessionContext(session_id as string),
      query: { token_budget: token_budget as number },
    }),
  },
  {
    name: 'session get-session-archive',
    positional: [sessionId, { name: 'archive-id', valueKey: 'archive_id' }],
    skeleton: 'status',
    request: ({ session_id, archive_id }) => ({
      method: 'GET',
      path: OV_PATH_BUILDERS.sessionArchive(
        session_id as string,
        archive_id as string,
      ),
    }),
  },
  {
    name: 'session delete',
    positional: [sessionId],
    skeleton: 'write',
    request: ({ session_id }) => ({
      method: 'DELETE',
      path: OV_PATH_BUILDERS.session(session_id as string),
    }),
  },
  {
    name: 'session add-message',
    positional: [sessionId],
    flags: [
      role,
      content,
      peerId,
      { name: 'parts', kind: 'json-array', hidden: true },
      { name: 'created-at', valueKey: 'created_at', hidden: true },
      telemetry,
    ],
    preferredFlags: [role, { ...content, required: true }, peerId],
    skeleton: 'write',
    request: ({ session_id, ...body }) => {
      if (body.content === undefined && body.parts === undefined)
        throw new ConsoleCommandError('missingArgument', '--content');
      return {
        method: 'POST',
        path: OV_PATH_BUILDERS.sessionMessages(session_id as string),
        body,
      };
    },
  },
  {
    name: 'session add-messages',
    positional: [sessionId],
    flags: [
      {
        name: 'messages',
        kind: 'json-array',
        required: true,
        placeholder: 'json-array',
      },
      telemetry,
    ],
    skeleton: 'write',
    request: ({ session_id, ...body }) => ({
      method: 'POST',
      path: OV_PATH_BUILDERS.sessionMessagesBatch(session_id as string),
      body,
    }),
  },
  {
    name: 'session commit',
    positional: [sessionId],
    flags: [
      {
        name: 'keep-recent-count',
        valueKey: 'keep_recent_count',
        kind: 'integer',
        max: 10_000,
        hidden: true,
      },
      telemetry,
    ],
    skeleton: 'write',
    request: ({ session_id, ...body }) => ({
      method: 'POST',
      path: OV_PATH_BUILDERS.sessionCommit(session_id as string),
      body,
    }),
  },
];
