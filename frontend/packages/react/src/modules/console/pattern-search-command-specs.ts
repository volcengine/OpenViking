import { OV_PATHS } from '../../client/paths.js';
import type { CommandSpec } from './command-specs.js';
import type { Argument, ArgumentValues } from './command-parser.js';

const root: Argument = {
  name: 'uri',
  alias: '-u',
  kind: 'uri',
  defaultValue: 'viking://',
  descriptionKey: 'search-root-uri',
};

const fileLimit: Argument = {
  name: 'node-limit',
  alias: '-n',
  aliases: ['--node_limit', '--limit'],
  kind: 'integer',
  min: 0,
  placeholder: 'n',
  valueKey: 'node_limit',
  defaultValue: 256,
};

const tags: Argument = { name: 'tags', placeholder: 'k=v' };
const fields: Argument = { name: 'fields', alias: '-f', placeholder: 'fields' };

function patternSearchRequest(name: 'grep' | 'glob', values: ArgumentValues) {
  const { tags, fields, ...body } = values;
  const tagList = typeof tags === 'string' ? tags.split(',') : undefined;
  const fieldList = typeof fields === 'string' ? fields.split(',') : undefined;
  return {
    method: 'POST' as const,
    path: name === 'grep' ? OV_PATHS.searchGrep : OV_PATHS.searchGlob,
    body: {
      ...body,
      tags: tagList,
      include_tags:
        tagList?.length || fieldList?.includes('tags') ? true : undefined,
      ...(name === 'glob' ? { extra_fields: fieldList } : {}),
    },
  };
}

export const PATTERN_SEARCH_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'grep',
    positional: [{ name: 'pattern', descriptionKey: 'grep-pattern' }],
    flags: [
      root,
      {
        name: 'ignore-case',
        kind: 'boolean',
        alias: '-i',
        aliases: ['--case-insensitive', '--case_insensitive'],
        defaultValue: false,
        valueKey: 'case_insensitive',
      },
      fileLimit,
      tags,
      fields,
      {
        name: 'before-context',
        alias: '-b',
        kind: 'integer',
        min: 0,
        placeholder: 'n',
        valueKey: 'before_context',
      },
      {
        name: 'after-context',
        alias: '-a',
        kind: 'integer',
        min: 0,
        placeholder: 'n',
        valueKey: 'after_context',
      },
      {
        name: 'exclude-uri',
        kind: 'uri',
        alias: '-x',
        aliases: ['--exclude_uri'],
        valueKey: 'exclude_uri',
      },
      {
        name: 'level-limit',
        kind: 'integer',
        alias: '-L',
        aliases: ['--level_limit'],
        placeholder: 'n',
        defaultValue: 10,
        valueKey: 'level_limit',
      },
    ],
    skeleton: 'search',
    request: (values) => patternSearchRequest('grep', values),
  },
  {
    name: 'glob',
    positional: [{ name: 'pattern', descriptionKey: 'glob-pattern' }],
    flags: [root, fileLimit, tags, fields],
    skeleton: 'search',
    request: (values) => patternSearchRequest('glob', values),
  },
];
