import type enUS from './locales/en-US.js';
import { OV_PATH_BUILDERS, OV_PATHS } from '../../client/paths.js';
import type { OvRequestSpec } from '../../client/types.js';
import type { CommandConsoleSkeletonKind } from '../../components/command-console/index.js';
import { CORE_COMMAND_SPECS } from './core-command-specs.js';
import type { Argument } from './command-parser.js';

type Values = Record<string, string | number | boolean>;
export interface CommandSpec {
  name: keyof typeof enUS.commands;
  positional?: readonly Argument[];
  flags?: readonly Argument[];
  skeleton?: CommandConsoleSkeletonKind;
  request?: (values: Values) => OvRequestSpec;
  execute?: (
    values: Values,
    request: (spec: OvRequestSpec) => Promise<Record<string, unknown>>,
  ) => Promise<unknown>;
}

const uri: Argument = { name: 'uri', kind: 'uri' };
const limit: Argument = { name: 'limit', kind: 'integer', min: 1 };
const recursive: Argument = { name: 'recursive', kind: 'boolean' };
const wait: Argument = { name: 'wait', kind: 'boolean' };
const taskId: Argument = { name: 'id' };

/** One catalogue drives validation, execution, completion and help. */
export const COMMAND_SPECS: readonly CommandSpec[] = [
  { name: 'help' },
  { name: 'clear' },
  ...CORE_COMMAND_SPECS,
  {
    name: 'health',

    skeleton: 'status',
    request: () => ({ method: 'GET', path: OV_PATHS.health }),
  },
  {
    name: 'status',

    skeleton: 'status',
    request: () => ({ method: 'GET', path: OV_PATHS.systemStatus }),
  },
  ...(['ls', 'tree'] as const).map((name): CommandSpec => ({
    name,

    positional: [{ ...uri, optional: true }],
    flags: [
      limit,
      { name: 'offset', kind: 'integer' },
      ...(name === 'tree'
        ? [{ name: 'level_limit', kind: 'integer' as const, min: 1 }]
        : [recursive]),
    ],
    skeleton: 'tree',
    request: (values) => ({
      method: 'GET',
      path: name === 'ls' ? OV_PATHS.fsLs : OV_PATHS.fsTree,
      query: { uri: 'viking://', limit: 100, ...values, output: 'original' },
    }),
  })),
  {
    name: 'stat',

    positional: [uri],
    skeleton: 'status',
    request: (query) => ({ method: 'GET', path: OV_PATHS.fsStat, query }),
  },
  ...(
    [
      ['read', OV_PATHS.contentRead],
      ['abstract', OV_PATHS.contentAbstract],
      ['overview', OV_PATHS.contentOverview],
    ] as const
  ).map(([name, path]): CommandSpec => ({
    name,

    positional: [uri],
    flags:
      name === 'read'
        ? [
            { name: 'offset', kind: 'integer' },
            { ...limit, min: -1 },
          ]
        : [],
    skeleton: 'resource',
    request: (query) => ({
      method: 'GET',
      path,
      query,
    }),
  })),
  ...(['find', 'search'] as const).map((name): CommandSpec => ({
    name,

    positional: [{ name: 'query' }],
    flags: [
      { name: 'target_uri', kind: 'uri', alias: '-u' },
      { ...limit, alias: '-n' },
      { name: 'level', alias: '-L' },
    ],
    skeleton: 'search',
    request: (body) => ({
      method: 'POST',
      path: name === 'find' ? OV_PATHS.searchFind : OV_PATHS.searchQuery,
      body,
    }),
  })),
  ...(['grep', 'glob'] as const).map((name): CommandSpec => ({
    name,

    positional: [{ name: 'pattern' }],
    flags: [
      uri,
      { name: 'node_limit', kind: 'integer', min: 1 },
      ...(name === 'grep'
        ? [{ name: 'case_insensitive', kind: 'boolean' as const }]
        : []),
    ],
    skeleton: 'search',
    request: (values) => ({
      method: 'POST',
      path: name === 'grep' ? OV_PATHS.searchGrep : OV_PATHS.searchGlob,
      body: { uri: 'viking://', ...values },
    }),
  })),
  {
    name: 'mkdir',

    positional: [uri],
    flags: [{ name: 'description' }],
    skeleton: 'write',
    request: (body) => ({ method: 'POST', path: OV_PATHS.fsMkdir, body }),
  },
  ...(['cp', 'mv'] as const).map((name): CommandSpec => ({
    name,

    positional: [
      { name: 'from_uri', kind: 'uri' },
      { name: 'to_uri', kind: 'uri' },
    ],
    flags: name === 'cp' ? [recursive] : [],
    skeleton: 'write',
    request: (body) => ({
      method: 'POST',
      path: name === 'cp' ? OV_PATHS.fsCp : OV_PATHS.fsMv,
      body,
    }),
  })),
  {
    name: 'rm',

    positional: [uri],
    flags: [recursive, wait],
    skeleton: 'write',
    request: (query) => ({ method: 'DELETE', path: OV_PATHS.fsRm, query }),
  },
  {
    name: 'write',

    positional: [uri, { name: 'content' }],
    flags: [{ name: 'mode', choices: ['create', 'replace', 'append'] }, wait],
    skeleton: 'write',
    request: (body) => ({
      method: 'POST',
      path: OV_PATHS.contentWrite,
      body,
    }),
  },
  {
    name: 'task list',

    flags: [limit, { name: 'status' }, { name: 'task_type' }],
    skeleton: 'status',
    request: (query) => ({ method: 'GET', path: OV_PATHS.tasks, query }),
  },
  {
    name: 'task status',

    positional: [taskId],
    skeleton: 'status',
    request: ({ id }) => ({
      method: 'GET',
      path: OV_PATH_BUILDERS.task(String(id)),
    }),
  },
  {
    name: 'task cancel',

    positional: [taskId],
    skeleton: 'write',
    request: ({ id }) => ({
      method: 'POST',
      path: OV_PATH_BUILDERS.taskCancel(String(id)),
    }),
  },
];
