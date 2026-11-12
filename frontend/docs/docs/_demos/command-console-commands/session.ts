import { URI_PARAM } from './shared';
import type { CommandConsoleCommandEntry } from './shared';

/** 状态、会话与交互命令。Status, session and interactive commands. */
export const SESSION_COMMANDS: readonly CommandConsoleCommandEntry[] = [
  {
    name: 'task',
    group: 'status',
    description: 'Track async resource processing tasks',
    usage: '/task <list|status|cancel> [id]',
    skeleton: 'status',
    params: [{ label: '<subcommand>', description: 'list, status or cancel' }],
    examples: ['/task list'],
  },
  {
    name: 'status',
    group: 'status',
    description: 'All OpenViking server components status',
    usage: '/status [--verbose]',
    skeleton: 'status',
    options: [
      { token: '--verbose', description: 'Show full component tables' },
    ],
    examples: ['/status'],
  },
  {
    name: 'health',
    group: 'status',
    description: 'Quick health check',
    usage: '/health',
    skeleton: 'status',
    examples: ['/health'],
  },
  {
    name: 'session',
    group: 'session',
    description: 'Session management commands',
    usage: '/session <subcommand>',
    skeleton: 'status',
    params: [{ label: '<subcommand>', description: 'Session operation' }],
    examples: ['/session list'],
  },
  {
    name: 'import',
    group: 'session',
    description: 'Import .ovpack into target URI',
    usage: '/import <file.ovpack> <target-uri>',
    skeleton: 'write',
    params: [
      { label: '<file.ovpack>', description: 'Input .ovpack file path' },
      { label: '<target-uri>', description: 'Target parent URI' },
    ],
    options: [
      {
        token: '--on-conflict <policy>',
        description: 'fail, overwrite or skip',
      },
    ],
    examples: ['/import ./acme.ovpack viking://projects'],
  },
  {
    name: 'export',
    group: 'session',
    description: 'Export context as .ovpack',
    usage: '/export <uri> <output.ovpack>',
    skeleton: 'write',
    params: [
      URI_PARAM,
      { label: '<output.ovpack>', description: 'Output file path' },
    ],
    examples: ['/export viking://projects/acme ./acme.ovpack'],
  },
];
