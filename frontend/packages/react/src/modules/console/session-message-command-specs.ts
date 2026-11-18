import { OV_PATH_BUILDERS } from '../../client/paths.js';
import { ConsoleCommandError } from './command-messages.js';
import type { Argument } from './command-parser.js';
import type { CommandSpec } from './command-specs.js';

export const sessionIdArgument: Argument = {
  name: 'session-id',
  valueKey: 'session_id',
};
export const sessionTelemetryArgument: Argument = {
  name: 'telemetry',
  kind: 'boolean-or-json',
  hidden: true,
};
const role: Argument = { name: 'role', required: true };
const content: Argument = { name: 'content', placeholder: 'text' };
const peerId: Argument = { name: 'peer-id', valueKey: 'peer_id' };

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
      (!('content' in message && typeof message.content === 'string') &&
        !('parts' in message && Array.isArray(message.parts)))
    )
      throw new ConsoleCommandError('invalidArgument', '<messages-json>');
    return message;
  });
}

export const SESSION_MESSAGE_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'session add-message',
    positional: [sessionIdArgument],
    flags: [
      role,
      content,
      peerId,
      { name: 'parts', kind: 'json-array', hidden: true },
      { name: 'created-at', valueKey: 'created_at', hidden: true },
      sessionTelemetryArgument,
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
    positional: [sessionIdArgument, { name: 'messages-json', optional: true }],
    preferredPositional: [sessionIdArgument, { name: 'messages-json' }],
    flags: [{ name: 'messages', hidden: true }, sessionTelemetryArgument],
    skeleton: 'write',
    request: ({
      session_id,
      'messages-json': input,
      messages: legacy,
      telemetry,
    }) => {
      if (input !== undefined && legacy !== undefined)
        throw new ConsoleCommandError('duplicateArgument', '<messages-json>');
      if (input === undefined && legacy === undefined)
        throw new ConsoleCommandError('missingArgument', '<messages-json>');
      return {
        method: 'POST',
        path: OV_PATH_BUILDERS.sessionMessagesBatch(session_id as string),
        body: {
          messages: parseMessages((input ?? legacy) as string),
          telemetry,
        },
      };
    },
  },
];
