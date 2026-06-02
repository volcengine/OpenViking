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
  { command: '/status', key: 'status', insertText: '/status' },
  { command: '/ls', key: 'ls', insertText: '/ls ' },
  { command: '/search', key: 'search', insertText: '/search ' },
  { command: '/find', key: 'find', insertText: '/find ' },
  { command: '/read', key: 'read', insertText: '/read ' },
  { command: '/add-resource', key: 'addResource', insertText: '/add-resource' },
]
