export type FilesystemPanel = 'agent' | 'terminal'

export type FilesystemSearch = {
  uri?: string
  file?: string
  panel?: FilesystemPanel
  session?: string
  upload?: boolean
}

export const validateFilesystemSearch = (
  search: Record<string, unknown>,
): FilesystemSearch => ({
  file: typeof search.file === 'string' ? search.file : undefined,
  panel:
    search.panel === 'agent' || search.panel === 'terminal'
      ? search.panel
      : undefined,
  session: typeof search.session === 'string' ? search.session : undefined,
  upload: search.upload === true || search.upload === 'true',
  uri: typeof search.uri === 'string' ? search.uri : undefined,
})

export type ResourceRef = {
  uri: string
  label?: string
  meta?: string
}

export type TerminalEntry = {
  commandId?: string
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
