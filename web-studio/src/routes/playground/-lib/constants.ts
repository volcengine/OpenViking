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
    command: '/status',
    executable: true,
    key: 'status',
    insertText: '/status',
  },
  {
    command: '/health',
    executable: true,
    key: 'health',
    insertText: '/health',
  },
  {
    command: '/version',
    executable: true,
    key: 'version',
    insertText: '/version',
  },
  {
    adminOnly: true,
    command: '/wait',
    executable: true,
    key: 'wait',
    insertText: '/wait ',
  },
  { command: '/ls', executable: true, key: 'ls', insertText: '/ls ' },
  { command: '/tree', executable: true, key: 'tree', insertText: '/tree ' },
  { command: '/stat', executable: true, key: 'stat', insertText: '/stat ' },
  { command: '/read', executable: true, key: 'read', insertText: '/read ' },
  {
    command: '/abstract',
    executable: true,
    key: 'abstract',
    insertText: '/abstract ',
  },
  {
    command: '/overview',
    executable: true,
    key: 'overview',
    insertText: '/overview ',
  },
  { command: '/find', executable: true, key: 'find', insertText: '/find ' },
  {
    command: '/search',
    executable: true,
    key: 'search',
    insertText: '/search ',
  },
  {
    command: '/add-resource',
    executable: true,
    key: 'addResource',
    insertText: '/add-resource',
  },
]
