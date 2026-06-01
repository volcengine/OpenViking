import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'

export type StudioPanel = 'agent' | 'terminal'

export type StudioSearch = {
  uri?: string
  file?: string
  panel?: StudioPanel
  session?: string
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

export type TerminalCommandSuggestion = {
  command: string
  description: string
  insertText: string
  usage: string
}

export type ResourceOpenHandler = (uri: string) => Promise<void> | void
export type VikingEntryHandler = (entry: VikingFsEntry) => void
