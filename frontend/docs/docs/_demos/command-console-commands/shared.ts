import type { CommandConsoleCommandDefinition } from '@openviking/react/components/command-console';

export const URI_PARAM = { label: '<uri>', description: 'Viking URI' };
export const LIMIT_OPTION = {
  token: '-n, --node-limit <n>',
  description: 'Maximum number of results',
};

export type CommandConsoleCommandEntry = CommandConsoleCommandDefinition;
