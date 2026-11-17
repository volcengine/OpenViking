import { OV_PATHS } from '../../client/paths.js';
import { ConsoleCommandError } from './command-messages.js';
import type { Argument } from './command-parser.js';
import type { CommandSpec } from './command-specs.js';

export const uri: Argument = { name: 'uri', kind: 'uri' };
export const uriOrRecordId: Argument = {
  name: 'uri',
  pattern: /^(?:viking:\/\/.*|[a-f\d]{32})$/i,
};
export const offset: Argument = { name: 'offset', kind: 'integer' };
export const acl: Argument = {
  name: 'acl',
  kind: 'json-object',
  placeholder: 'json',
};
export const wait: Argument = {
  name: 'wait',
  kind: 'boolean',
  defaultValue: false,
};

const content: Argument = {
  name: 'content',
  descriptionKey: 'write-content',
  placeholder: 'text',
};
const append: Argument = {
  name: 'append',
  kind: 'boolean',
  defaultValue: false,
};
const mode: Argument = {
  name: 'mode',
  descriptionKey: 'write-mode',
  choices: ['replace', 'append', 'create'],
  placeholder: 'replace|append|create',
};

export const CONTENT_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'read',
    positional: [uriOrRecordId],
    flags: [offset, { name: 'limit', kind: 'integer', min: -1 }],
    preferredFlags: [],
    skeleton: 'resource',
    request: ({ uri, offset, limit }) => ({
      method: 'GET',
      path: OV_PATHS.contentRead,
      query: {
        uri: uri as string,
        offset: offset as number | undefined,
        limit: limit as number | undefined,
      },
    }),
  },
  ...(
    [
      ['abstract', OV_PATHS.contentAbstract],
      ['overview', OV_PATHS.contentOverview],
    ] as const
  ).map(([name, path]): CommandSpec => ({
    name,
    positional: [{ ...uri, descriptionKey: 'summary-uri' }],
    skeleton: 'resource',
    request: ({ uri }) => ({
      method: 'GET',
      path,
      query: { uri: uri as string },
    }),
  })),
  {
    name: 'write',
    positional: [uri, { name: 'legacy-content', optional: true }],
    preferredPositional: [uri],
    flags: [content, append, mode, acl, wait],
    preferredFlags: [
      { ...content, required: true },
      append,
      { ...mode, defaultValue: 'replace' },
      acl,
      wait,
    ],
    skeleton: 'write',
    request: ({
      'legacy-content': legacy,
      content,
      append,
      mode,
      wait,
      ...body
    }) => {
      if (legacy !== undefined && content !== undefined)
        throw new ConsoleCommandError('duplicateArgument', '--content');
      const text = content ?? legacy;
      if (text === undefined)
        throw new ConsoleCommandError('missingArgument', '--content');
      if (append === true && mode !== undefined && mode !== 'append')
        throw new ConsoleCommandError('invalidArgument', '--append / --mode');
      return {
        method: 'POST',
        path: OV_PATHS.contentWrite,
        body: {
          ...body,
          content: text,
          mode: append === true ? 'append' : (mode ?? 'replace'),
          ...(wait === true ? { wait } : {}),
        },
      };
    },
  },
];
