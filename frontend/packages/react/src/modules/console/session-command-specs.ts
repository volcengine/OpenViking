import { OV_PATH_BUILDERS, OV_PATHS } from '../../client/paths.js';
import { ConsoleCommandError } from './command-messages.js';
import type { Argument, ArgumentValues } from './command-parser.js';
import type { CommandSpec } from './command-specs.js';

const session: Argument = { name: 'session-id', valueKey: 'session_id' };
const eventTags: Argument = {
  name: 'event-tags',
  valueKey: 'event_tags',
  placeholder: 'key=value',
};
const noEventTags: Argument = {
  name: 'no-event-tags',
  valueKey: 'no_event_tags',
  kind: 'boolean',
};
const autoCommitPolicy: Argument = {
  name: 'auto-commit-policy-json',
  valueKey: 'auto_commit_policy',
  kind: 'json-object',
};
const noAutoCommit: Argument = {
  name: 'no-auto-commit',
  valueKey: 'no_auto_commit',
  kind: 'boolean',
};

function sessionTags({ event_tags, no_event_tags }: ArgumentValues) {
  if (event_tags !== undefined && no_event_tags === true)
    throw new ConsoleCommandError(
      'invalidArgument',
      '--event-tags / --no-event-tags',
    );
  if (no_event_tags === true) return [];
  return typeof event_tags === 'string' ? event_tags.split(',') : undefined;
}

function sessionConfiguration(values: ArgumentValues, required?: boolean) {
  const { auto_commit_policy, no_auto_commit } = values;
  if (no_auto_commit === true && auto_commit_policy !== undefined)
    throw new ConsoleCommandError(
      'invalidArgument',
      '--auto-commit-policy-json / --no-auto-commit',
    );
  const tags = sessionTags(values);
  const policy = no_auto_commit === true ? null : auto_commit_policy;
  if (required && tags === undefined && policy === undefined)
    throw new ConsoleCommandError(
      'missingArgument',
      '--event-tags / --no-event-tags / --auto-commit-policy-json / --no-auto-commit',
    );
  return {
    memory_extraction_config:
      tags === undefined ? undefined : { events: { tags } },
    auto_commit_policy: policy,
  };
}

function parseMessages(input: string) {
  let parsed: unknown;
  try {
    parsed = JSON.parse(input);
  } catch {
    return [{ role: 'user', content: input }];
  }
  const messages = Array.isArray(parsed)
    ? parsed
    : parsed &&
        typeof parsed === 'object' &&
        ('role' in parsed || 'content' in parsed)
      ? [parsed]
      : [{ role: 'user', content: input }];
  return messages.map((message: unknown) => {
    if (
      !message ||
      typeof message !== 'object' ||
      !('role' in message) ||
      typeof message.role !== 'string' ||
      !('content' in message) ||
      typeof message.content !== 'string'
    )
      throw new ConsoleCommandError('invalidArgument', '<messages-json>');
    return { role: message.role, content: message.content };
  });
}

export const SESSION_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'session new',
    flags: [session, eventTags, autoCommitPolicy, noAutoCommit],
    skeleton: 'write',
    request: (values) => ({
      method: 'POST',
      path: OV_PATHS.sessions,
      body: { session_id: values.session_id, ...sessionConfiguration(values) },
    }),
  },
  {
    name: 'session list',
    skeleton: 'status',
    request: () => ({ method: 'GET', path: OV_PATHS.sessions }),
  },
  ...(['get', 'delete'] as const).map((action): CommandSpec => ({
    name: `session ${action}`,
    positional: [session],
    skeleton: action === 'get' ? 'status' : 'write',
    request: ({ session_id }) => ({
      method: action === 'get' ? 'GET' : 'DELETE',
      path: OV_PATH_BUILDERS.session(session_id as string),
    }),
  })),
  {
    name: 'session get-session-context',
    positional: [session],
    flags: [
      {
        name: 'token-budget',
        valueKey: 'token_budget',
        kind: 'integer',
        defaultValue: 128000,
        placeholder: 'tokens',
      },
    ],
    skeleton: 'status',
    request: ({ session_id, token_budget }) => ({
      method: 'GET',
      path: OV_PATH_BUILDERS.sessionContext(session_id as string),
      query: { token_budget: Number(token_budget) },
    }),
  },
  {
    name: 'session get-session-archive',
    positional: [session, { name: 'archive-id' }],
    skeleton: 'status',
    request: ({ session_id, 'archive-id': archiveId }) => ({
      method: 'GET',
      path: OV_PATH_BUILDERS.sessionArchive(
        session_id as string,
        archiveId as string,
      ),
    }),
  },
  {
    name: 'session add-message',
    positional: [session],
    flags: [
      { name: 'role', required: true },
      { name: 'content', required: true, placeholder: 'text' },
      { name: 'peer-id', valueKey: 'peer_id' },
    ],
    skeleton: 'write',
    request: ({ session_id, ...body }) => ({
      method: 'POST',
      path: OV_PATH_BUILDERS.sessionMessages(session_id as string),
      body,
    }),
  },
  {
    name: 'session add-messages',
    positional: [session, { name: 'messages-json', optional: true }],
    preferredPositional: [session, { name: 'messages-json' }],
    flags: [{ name: 'messages', hidden: true }],
    skeleton: 'write',
    request: ({ session_id, 'messages-json': input, messages: legacy }) => {
      if (input !== undefined && legacy !== undefined)
        throw new ConsoleCommandError('duplicateArgument', '<messages-json>');
      if (input === undefined && legacy === undefined)
        throw new ConsoleCommandError('missingArgument', '<messages-json>');
      return {
        method: 'POST',
        path: `${OV_PATH_BUILDERS.sessionMessages(session_id as string)}/batch`,
        body: { messages: parseMessages((input ?? legacy) as string) },
      };
    },
  },
  {
    name: 'session commit',
    positional: [session],
    flags: [eventTags, noEventTags],
    skeleton: 'write',
    request: (values) => {
      const tags = sessionTags(values);
      return {
        method: 'POST',
        path: OV_PATH_BUILDERS.sessionCommit(values.session_id as string),
        body:
          tags === undefined
            ? {}
            : { extraction_metadata: { event: { tags } } },
      };
    },
  },
  {
    name: 'session config set',
    positional: [session],
    flags: [eventTags, noEventTags, autoCommitPolicy, noAutoCommit],
    skeleton: 'write',
    request: (values) => ({
      method: 'PATCH',
      path: `${OV_PATH_BUILDERS.session(values.session_id as string)}/config`,
      body: sessionConfiguration(values, true),
    }),
  },
];
