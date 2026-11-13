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
  backgroundTaskCompletedBody?: string
  backgroundTaskNotify?: boolean
  backgroundTaskFallbackUri?: string
  backgroundTaskSource?: string
  createdAt?: number
  compileTaskId?: string
  resourceTaskId?: string
  compileForm?: boolean
  id: string
  kind: 'command' | 'error' | 'info' | 'success'
  title: string
  body?: string
  details?: Array<{ label: string; value: string; uri?: string }>
  refs?: ResourceRef[]
}

export type TerminalCommandGroup = 'core' | 'filesystem' | 'search' | 'status'

export type TerminalCommandParameterKey =
  | 'compileFrom'
  | 'compileTo'
  | 'compileSkill'
  | 'skillSource'
  | 'skillScope'
  | 'skillParent'
  | 'resourceSource'
  | 'resourceTo'
  | 'resourceParent'
  | 'resourceReason'
  | 'resourceArgs'
  | 'writeContent'
  | 'writeMode'
  | 'writeWait'
  | 'recursive'
  | 'sourceUri'
  | 'targetUri'
  | 'directoryDescription'
  | 'nodeLimit'
  | 'threshold'
  | 'searchSessionId'
  | 'pattern'
  | 'ignoreCase'
  | 'taskAction'
  | 'taskId'
  | 'memoryContent'
  | 'memorySessionId'
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
