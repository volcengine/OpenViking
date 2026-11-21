import { OV_PATH_BUILDERS, OV_PATHS } from '../../client/paths.js';
import { ConsoleCommandError } from './command-messages.js';
import type { Argument, ArgumentValues } from './command-parser.js';
import type { CommandSpec } from './command-specs.js';

import {
  SESSION_MESSAGE_COMMAND_SPECS,
  sessionIdArgument as session,
  sessionTelemetryArgument as telemetry,
} from './session-message-command-specs.js';

const autoCreate: Argument = {
  name: 'auto-create',
  valueKey: 'auto_create',
  kind: 'boolean',
  hidden: true,
};
const memoryPolicy: Argument = {
  name: 'memory-policy',
  valueKey: 'memory_policy',
  kind: 'json-object',
  hidden: true,
};
const keepRecentCount: Argument = {
  name: 'keep-recent-count',
  valueKey: 'keep_recent_count',
  kind: 'integer',
  min: 0,
  max: 10_000,
  hidden: true,
};
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

export const SESSION_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'session new',
    flags: [
      { ...session, aliases: ['--sessionid', '--id'] },
      eventTags,
      autoCommitPolicy,
      noAutoCommit,
      memoryPolicy,
      telemetry,
    ],
    skeleton: 'write',
    request: (values) => ({
      method: 'POST',
      path: OV_PATHS.sessions,
      body: {
        session_id: values.session_id,
        ...sessionConfiguration(values),
        memory_policy: values.memory_policy,
        telemetry: values.telemetry,
      },
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
    flags: action === 'get' ? [autoCreate] : undefined,
    skeleton: action === 'get' ? 'status' : 'write',
    request: ({ session_id, auto_create }) => ({
      method: action === 'get' ? 'GET' : 'DELETE',
      path: OV_PATH_BUILDERS.session(session_id as string),
      query:
        action === 'get'
          ? { auto_create: auto_create as boolean | undefined }
          : undefined,
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
  ...SESSION_MESSAGE_COMMAND_SPECS,
  {
    name: 'session commit',
    positional: [session],
    flags: [eventTags, noEventTags, keepRecentCount, telemetry],
    skeleton: 'write',
    request: (values) => {
      const tags = sessionTags(values);
      return {
        method: 'POST',
        path: OV_PATH_BUILDERS.sessionCommit(values.session_id as string),
        body: {
          extraction_metadata:
            tags === undefined ? undefined : { event: { tags } },
          keep_recent_count: values.keep_recent_count,
          telemetry: values.telemetry,
        },
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
