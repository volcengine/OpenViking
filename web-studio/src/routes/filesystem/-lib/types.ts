export type FilesystemPanel = 'agent' | 'terminal'

export type FilesystemSearch = {
  uri?: string
  file?: string
  panel?: FilesystemPanel
  session?: string
  upload?: boolean
}

export type ResourceRef = {
  uri: string
  label?: string
  meta?: string
}

export type TerminalEntry = {
  compileTaskId?: string
  compileForm?: boolean
  id: string
  kind: 'command' | 'error' | 'info' | 'success'
  title: string
  body?: string
  refs?: ResourceRef[]
}

export type TerminalCommandGroup = 'core' | 'filesystem' | 'search' | 'status'

export type TerminalCommandParameterKey =
  | 'archiveId'
  | 'contextChars'
  | 'keepRecent'
  | 'limit'
  | 'messageContent'
  | 'messageRole'
  | 'offset'
  | 'query'
  | 'scope'
  | 'sessionAction'
  | 'sessionId'
  | 'tokenBudget'
  | 'toolName'
  | 'toolResultId'
  | 'timeout'
  | 'uri'

export type TerminalCommandSuggestion = {
  adminOnly?: boolean
  command: string
  examples?: string[]
  group: TerminalCommandGroup
  /** i18n subkey under `filesystem.terminal.commands`. */
  key: string
  insertText: string
  parameters?: TerminalCommandParameterKey[]
  executable?: boolean
}

/** A {@link TerminalCommandSuggestion} with its label/usage resolved via i18n. */
export type TerminalCommandView = TerminalCommandSuggestion & {
  description: string
  usage: string
}

export type ResourceOpenHandler = (uri: string) => Promise<void> | void
