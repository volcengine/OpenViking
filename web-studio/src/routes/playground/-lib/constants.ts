import type { TerminalCommandSuggestion } from './types'

export const ROOT_URI = 'viking://'
export const PLAYGROUND_LEFT_WIDTH_STORAGE_KEY =
  'openviking.playground.leftWidth'
export const PLAYGROUND_RIGHT_WIDTH_STORAGE_KEY =
  'openviking.playground.rightWidth'
export const PLAYGROUND_AGENT_SESSIONS_STORAGE_KEY =
  'openviking.playground.agentSessions'
export const PLAYGROUND_LEFT_WIDTH = {
  default: 330,
  max: 620,
  min: 240,
}
export const PLAYGROUND_RIGHT_WIDTH = {
  default: 430,
  max: 680,
  min: 320,
}
export const PLAYGROUND_MAIN_MIN_WIDTH = 420

export const TERMINAL_COMMANDS: TerminalCommandSuggestion[] = [
  {
    command: '/add-resource',
    executable: true,
    group: 'core',
    key: 'addResource',
    insertText: '/add-resource',
  },
  {
    command: '/find',
    executable: true,
    group: 'core',
    key: 'find',
    insertText: '/find ',
  },
  {
    command: '/read',
    executable: true,
    group: 'core',
    key: 'read',
    insertText: '/read ',
  },
  {
    command: '/ls',
    executable: true,
    group: 'filesystem',
    key: 'ls',
    insertText: '/ls ',
  },
  {
    command: '/tree',
    executable: true,
    group: 'filesystem',
    key: 'tree',
    insertText: '/tree ',
  },
  {
    command: '/stat',
    executable: true,
    group: 'filesystem',
    key: 'stat',
    insertText: '/stat ',
  },
  {
    command: '/search',
    executable: true,
    group: 'search',
    key: 'search',
    insertText: '/search ',
  },
  {
    command: '/abstract',
    executable: true,
    group: 'search',
    key: 'abstract',
    insertText: '/abstract ',
  },
  {
    command: '/overview',
    executable: true,
    group: 'search',
    key: 'overview',
    insertText: '/overview ',
  },
  {
    command: '/health',
    executable: true,
    group: 'status',
    key: 'health',
    insertText: '/health',
  },
  {
    command: '/status',
    executable: true,
    group: 'status',
    key: 'status',
    insertText: '/status',
  },
  {
    adminOnly: true,
    command: '/wait',
    executable: true,
    group: 'status',
    key: 'wait',
    insertText: '/wait ',
  },
]
