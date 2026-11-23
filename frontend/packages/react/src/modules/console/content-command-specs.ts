import { OV_PATHS } from '../../client/paths.js';
import { selectCommandFile } from './command-files.js';
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

export const processingMode: Argument = {
  name: 'processing-mode',
  valueKey: 'processing_mode',
  choices: ['semantic_and_vectors', 'vectors_only'],
  helpDefault: 'semantic_and_vectors',
};
export const tags: Argument = { name: 'tags', placeholder: 'k=v' };
export const tagMode: Argument = {
  name: 'tag-mode',
  valueKey: 'tag_mode',
  choices: ['replace', 'append', 'clear'],
  helpDefault: 'replace',
};
const fromFile: Argument = { name: 'from-file', placeholder: 'path' };
const writeFlags: readonly Argument[] = [
  content,
  fromFile,
  append,
  { ...mode, helpDefault: 'replace' },
  wait,
  { name: 'use-snapshot', valueKey: 'use_snapshot', kind: 'boolean' },
  processingMode,
  { name: 'defer-processing', valueKey: 'defer_processing', kind: 'boolean' },
  tags,
  tagMode,
  acl,
];

export function tagFields(raw: unknown, mode: unknown) {
  if (typeof raw !== 'string' && mode !== 'clear') return {};
  return {
    tags: typeof raw === 'string' ? raw.split(',') : [],
    tag_mode: mode ?? 'replace',
  };
}

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
    flags: writeFlags,
    skeleton: 'write',
    execute: async (
      {
        'legacy-content': legacy,
        'from-file': source,
        content,
        append,
        mode,
        wait,
        tags,
        tag_mode,
        ...body
      },
      request,
      execution,
    ) => {
      if (
        [legacy, content, source].filter((value) => value !== undefined)
          .length > 1
      )
        throw new ConsoleCommandError(
          'duplicateArgument',
          '--content / --from-file',
        );
      if (append === true && mode !== undefined)
        throw new ConsoleCommandError('invalidArgument', '--append / --mode');
      const text =
        typeof source === 'string'
          ? await (await selectCommandFile(execution, source)).text()
          : (content ?? legacy);
      if (text === undefined)
        throw new ConsoleCommandError(
          'missingArgument',
          '--content / --from-file',
        );
      return request({
        method: 'POST',
        path: OV_PATHS.contentWrite,
        body: {
          ...body,
          ...tagFields(tags, tag_mode),
          content: text,
          mode: append === true ? 'append' : (mode ?? 'replace'),
          ...(wait === true ? { wait } : {}),
        },
      });
    },
  },
];
