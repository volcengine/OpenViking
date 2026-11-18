import type enUS from './locales/en-US.js';
import { OV_PATH_BUILDERS, OV_PATHS } from '../../client/paths.js';
import type { OvRequestSpec } from '../../client/types.js';
import type { CommandConsoleSkeletonKind } from '../../components/command-console/index.js';
import { CORE_COMMAND_SPECS } from './core-command-specs.js';
import { FILESYSTEM_COMMAND_SPECS } from './filesystem-command-specs.js';
import { SEARCH_COMMAND_SPECS } from './search-command-specs.js';
import { SESSION_COMMAND_SPECS } from './session-command-specs.js';
import { TRANSFER_COMMAND_SPECS } from './transfer-command-specs.js';
import type { CommandExecution } from './command-files.js';
import type { Argument, ArgumentValues } from './command-parser.js';

export interface CommandSpec {
  name: keyof typeof enUS.commands;
  admin?: boolean;
  positional?: readonly Argument[];
  flags?: readonly Argument[];
  /** Parameter help may prefer the current workflow over legacy signatures. */
  preferredPositional?: readonly Argument[];
  preferredFlags?: readonly Argument[];
  skeleton?: CommandConsoleSkeletonKind;
  request?: (values: ArgumentValues) => OvRequestSpec;
  execute?: (
    values: ArgumentValues,
    request: CommandExecution['request'],
    execution: CommandExecution,
  ) => Promise<unknown>;
}

const taskId: Argument = { name: 'task-id', valueKey: 'id' };

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
  ...FILESYSTEM_COMMAND_SPECS,
  ...SEARCH_COMMAND_SPECS,
  ...SESSION_COMMAND_SPECS,
  ...TRANSFER_COMMAND_SPECS,
  {
    name: 'task list',
    flags: [
      {
        name: 'task-type',
        valueKey: 'task_type',
        aliases: ['--task_type'],
        placeholder: 'type',
      },
      { name: 'status', placeholder: 'status', descriptionKey: 'task-status' },
      {
        name: 'resource-id',
        valueKey: 'resource_id',
        aliases: ['--resource_id'],
        hidden: true,
      },
      { name: 'limit', kind: 'integer', min: 1, max: 200, hidden: true },
    ],
    skeleton: 'status',
    request: ({ task_type, status, resource_id, limit }) => ({
      method: 'GET',
      path: OV_PATHS.tasks,
      query: {
        task_type: task_type as string | undefined,
        status: status as string | undefined,
        resource_id: resource_id as string | undefined,
        limit: limit as number | undefined,
      },
    }),
  },
  {
    name: 'task status',

    positional: [taskId],
    skeleton: 'status',
    request: ({ id }) => ({
      method: 'GET',
      path: OV_PATH_BUILDERS.task(id as string),
    }),
  },
  {
    name: 'task cancel',

    positional: [taskId],
    skeleton: 'write',
    request: ({ id }) => ({
      method: 'POST',
      path: OV_PATH_BUILDERS.taskCancel(id as string),
    }),
  },
];
