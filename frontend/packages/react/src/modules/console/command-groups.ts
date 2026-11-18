import type { CommandSpec } from './command-specs.js';

// Command palette order, restricted to the commands this Console can execute.
export const COMMAND_GROUPS = {
  core: [
    'add-resource',
    'add-skill',
    'compile',
    'compile create',
    'find',
    'read',
    'write',
    'add-memory',
  ],
  filesystem: ['ls', 'tree', 'mkdir', 'rm', 'mv', 'stat', 'get', 'cp'],
  search: ['find', 'search', 'grep', 'glob', 'abstract', 'overview', 'read'],
  status: [
    'help',
    'health',
    'status',
    'task list',
    'task status',
    'task cancel',
    'clear',
  ],
  transfers: [
    'export',
    'backup',
    'session new',
    'session list',
    'session get',
    'session get-session-context',
    'session get-session-archive',
    'session delete',
    'session add-message',
    'session add-messages',
    'session commit',
  ],
} satisfies Record<string, readonly CommandSpec['name'][]>;
