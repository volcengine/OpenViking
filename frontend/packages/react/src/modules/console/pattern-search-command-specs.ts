import { OV_PATHS } from '../../client/paths.js';
import type { CommandSpec } from './command-specs.js';
import type { Argument } from './command-parser.js';

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
  aliases: ['--node_limit'],
  kind: 'integer',
  min: 1,
  placeholder: 'n',
  valueKey: 'node_limit',
  defaultValue: 256,
};

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
    request: (body) => ({ method: 'POST', path: OV_PATHS.searchGrep, body }),
  },
  {
    name: 'glob',
    positional: [{ name: 'pattern', descriptionKey: 'glob-pattern' }],
    flags: [root, fileLimit],
    skeleton: 'search',
    request: (body) => ({ method: 'POST', path: OV_PATHS.searchGlob, body }),
  },
];
