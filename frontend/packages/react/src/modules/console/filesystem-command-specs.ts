import { OV_PATHS } from '../../client/paths.js';
import type { Argument, ArgumentValues } from './command-parser.js';
import type { CommandSpec } from './command-specs.js';
import {
  CONTENT_COMMAND_SPECS,
  acl,
  offset,
  uri,
  uriOrRecordId,
  wait,
} from './content-command-specs.js';

const moveUris: readonly Argument[] = [
  { name: 'from-uri', kind: 'uri', valueKey: 'from_uri' },
  { name: 'to-uri', kind: 'uri', valueKey: 'to_uri' },
];
const recursive: Argument = {
  name: 'recursive',
  kind: 'boolean',
  alias: '-r',
  defaultValue: false,
};
const all: Argument = {
  name: 'all',
  kind: 'boolean',
  alias: '-a',
  aliases: ['--show-all-hidden', '--show_all_hidden'],
  valueKey: 'show_all_hidden',
  defaultValue: false,
};
const nodeLimit: Argument = {
  name: 'node-limit',
  descriptionKey: 'fs-node-limit',
  kind: 'integer',
  alias: '-n',
  aliases: ['--node_limit', '--limit'],
  valueKey: 'node_limit',
  placeholder: 'n',
  defaultValue: 256,
  min: 1,
};
const abstractLimit: Argument = {
  name: 'abs-limit',
  kind: 'integer',
  alias: '-l',
  aliases: ['--abs_limit'],
  valueKey: 'abs_limit',
  placeholder: 'n',
};
const lsOptions: readonly Argument[] = [
  recursive,
  { name: 'simple', kind: 'boolean', alias: '-s', defaultValue: false },
  all,
  nodeLimit,
  { ...abstractLimit, defaultValue: 256 },
];
const treeOptions: readonly Argument[] = [
  {
    name: 'level-limit',
    descriptionKey: 'tree-level-limit',
    kind: 'integer',
    alias: '-L',
    aliases: ['--level_limit'],
    valueKey: 'level_limit',
    placeholder: 'n',
    defaultValue: 3,
    min: 1,
  },
  nodeLimit,
  all,
  { ...abstractLimit, defaultValue: 128 },
];

function listingQuery(values: ArgumentValues) {
  return {
    uri: values.uri as string,
    output: 'agent',
    abs_limit: values.abs_limit as number,
    show_all_hidden: values.show_all_hidden === true,
    node_limit: values.node_limit as number,
    offset: values.offset as number | undefined,
  };
}

export const FILESYSTEM_COMMAND_SPECS: readonly CommandSpec[] = [
  {
    name: 'ls',
    positional: [{ ...uri, optional: true, defaultValue: 'viking://' }],
    flags: [...lsOptions, offset],
    preferredFlags: lsOptions,
    skeleton: 'tree',
    request: (values) => ({
      method: 'GET',
      path: OV_PATHS.fsLs,
      query: {
        ...listingQuery(values),
        recursive: values.recursive === true,
        simple: values.simple === true,
      },
    }),
  },
  {
    name: 'tree',
    positional: [uri],
    flags: [...treeOptions, offset],
    preferredFlags: treeOptions,
    skeleton: 'tree',
    request: (values) => ({
      method: 'GET',
      path: OV_PATHS.fsTree,
      query: {
        ...listingQuery(values),
        level_limit: values.level_limit as number,
      },
    }),
  },
  {
    name: 'stat',
    positional: [uriOrRecordId],
    skeleton: 'status',
    request: ({ uri }) => ({
      method: 'GET',
      path: OV_PATHS.fsStat,
      query: { uri: uri as string },
    }),
  },
  {
    name: 'mkdir',
    positional: [uri],
    flags: [{ name: 'description', placeholder: 'text' }, acl],
    skeleton: 'write',
    request: (body) => ({ method: 'POST', path: OV_PATHS.fsMkdir, body }),
  },
  {
    name: 'rm',
    positional: [uri],
    flags: [recursive, wait],
    preferredFlags: [recursive],
    skeleton: 'write',
    request: ({ uri, recursive, wait }) => ({
      method: 'DELETE',
      path: OV_PATHS.fsRm,
      query: {
        uri: uri as string,
        recursive: recursive === true,
        wait: wait === true,
      },
    }),
  },
  {
    name: 'mv',
    positional: moveUris,
    skeleton: 'write',
    request: (body) => ({ method: 'POST', path: OV_PATHS.fsMv, body }),
  },
  {
    name: 'cp',
    positional: moveUris,
    flags: [recursive],
    skeleton: 'write',
    request: (body) => ({ method: 'POST', path: OV_PATHS.fsCp, body }),
  },
  ...CONTENT_COMMAND_SPECS,
];
