import type { CommandSpec } from './command-specs.js';

export const parameterExamples: Partial<
  Record<CommandSpec['name'], readonly [string, ...string[]]>
> = {
  tree: [
    'ov tree viking://resources --node-limit 200',
    'ov tree viking://resources --level-limit 2',
  ],
  search: [
    'ov search OpenViking --mode list --events-time-decay-protection 7d',
    'ov search OpenViking --mode context --session-id <session-id>',
  ],
  'task list': [
    'ov task list --status failed',
    'ov task list --task-type add_resource',
  ],
};
