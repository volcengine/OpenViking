import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'

export type PlaygroundPanel = 'agent' | 'terminal'

export type PlaygroundSearch = {
  uri?: string
  file?: string
  panel?: PlaygroundPanel
  session?: string
  upload?: boolean
}

export type ResourceRef = {
  uri: string
  label?: string
  meta?: string
}

export type TerminalEntry = {
  id: string
  kind: 'command' | 'error' | 'info' | 'success'
  title: string
  body?: string
  refs?: ResourceRef[]
}

export type TerminalCommandKey =
  | 'status'
  | 'ls'
  | 'search'
  | 'find'
  | 'read'
  | 'addResource'

export type TerminalCommandSuggestion = {
  command: string
  /** i18n subkey under `playground.terminal.commands`. */
  key: TerminalCommandKey
  insertText: string
}

/** A {@link TerminalCommandSuggestion} with its label/usage resolved via i18n. */
export type TerminalCommandView = TerminalCommandSuggestion & {
  description: string
  usage: string
}

export type ResourceOpenHandler = (uri: string) => Promise<void> | void
export type VikingEntryHandler = (entry: VikingFsEntry) => void
