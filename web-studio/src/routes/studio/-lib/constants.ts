import type { TerminalCommandSuggestion } from './types'

export const ROOT_URI = 'viking://'
export const STUDIO_LEFT_WIDTH_STORAGE_KEY = 'web-studio-playground-left-width'
export const STUDIO_RIGHT_WIDTH_STORAGE_KEY =
  'web-studio-playground-right-width'
export const STUDIO_AGENT_SESSIONS_STORAGE_KEY = 'web-studio-agent-sessions'
export const STUDIO_LEFT_WIDTH = {
  default: 330,
  max: 620,
  min: 240,
}
export const STUDIO_RIGHT_WIDTH = {
  default: 430,
  max: 680,
  min: 320,
}
export const STUDIO_MAIN_MIN_WIDTH = 420

export const TERMINAL_COMMANDS: TerminalCommandSuggestion[] = [
  { command: '/status', key: 'status', insertText: '/status' },
  { command: '/ls', key: 'ls', insertText: '/ls ' },
  { command: '/search', key: 'search', insertText: '/search ' },
  { command: '/find', key: 'find', insertText: '/find ' },
  { command: '/read', key: 'read', insertText: '/read ' },
  { command: '/add-resource', key: 'addResource', insertText: '/add-resource' },
]
