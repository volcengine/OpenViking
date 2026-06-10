import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowRightIcon,
  CheckCircle2Icon,
  HistoryIcon,
  Loader2Icon,
  SendIcon,
  SparklesIcon,
  TerminalIcon,
  TrashIcon,
  XCircleIcon,
} from 'lucide-react'

import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  getHealth,
  getOvResult,
  getSystemStatus,
  postSystemWait,
} from '#/lib/ov-client'
import { cn } from '#/lib/utils'
import {
  fetchDirectoryLevelContent,
  fetchFileContent,
  fetchFsList,
  fetchFsStat,
  fetchFsTree,
  fetchSearch,
} from '#/routes/resources/-lib/api'
import { normalizeDirUri } from '#/routes/resources/-lib/normalize'
import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'

import { ROOT_URI, TERMINAL_COMMANDS } from '../-lib/constants'
import type {
  ResourceRef,
  ResourceOpenHandler,
  TerminalCommandGroup,
  TerminalCommandView,
  TerminalEntry,
} from '../-lib/types'
import {
  cleanVikingUri,
  entryToRef,
  getErrorMessage,
  readStoredJsonArray,
  removeStoredValue,
  searchResultToRefs,
  visibleContextEntries,
  writeStoredJson,
} from '../-lib/utils'
import { ResourceRefList } from './resource-ref-list'

const TERMINAL_COMMAND_HISTORY_STORAGE_KEY =
  'openviking.playground.terminalCommandHistory'
const TERMINAL_ENTRY_HISTORY_STORAGE_KEY =
  'openviking.playground.terminalEntryHistory'
const TERMINAL_COMMAND_HISTORY_LIMIT = 50
const TERMINAL_ENTRY_HISTORY_LIMIT = 100
const URI_ARGUMENT_COMMANDS = new Set([
  '/abstract',
  '/ls',
  '/overview',
  '/read',
  '/stat',
  '/tree',
])

type TerminalSuggestionGroup = TerminalCommandGroup | 'history' | 'resource'

type TerminalSuggestion = Omit<TerminalCommandView, 'group'> & {
  group: TerminalSuggestionGroup
  id: string
}

type TerminalQuickStartExample = {
  action?: () => void
  command: string
  code: string
  key: string
  title: string
}

function loadCommandHistory(): string[] {
  return readStoredJsonArray(
    TERMINAL_COMMAND_HISTORY_STORAGE_KEY,
    (item) => {
      if (typeof item !== 'string') return undefined
      const trimmed = item.trim()
      return trimmed || undefined
    },
    TERMINAL_COMMAND_HISTORY_LIMIT,
  )
}

function persistCommandHistory(history: string[]): void {
  writeStoredJson(
    TERMINAL_COMMAND_HISTORY_STORAGE_KEY,
    history.slice(0, TERMINAL_COMMAND_HISTORY_LIMIT),
  )
}

function normalizeRefs(value: unknown): ResourceRef[] | undefined {
  if (!Array.isArray(value)) return undefined
  const refs = value
    .filter(
      (item): item is ResourceRef =>
        typeof item === 'object' &&
        item !== null &&
        typeof (item as ResourceRef).uri === 'string',
    )
    .map((item) => ({
      label: typeof item.label === 'string' ? item.label : undefined,
      meta: typeof item.meta === 'string' ? item.meta : undefined,
      uri: item.uri,
    }))
  return refs.length > 0 ? refs : undefined
}

function loadTerminalHistory(): TerminalEntry[] {
  return readStoredJsonArray(
    TERMINAL_ENTRY_HISTORY_STORAGE_KEY,
    (item): TerminalEntry | undefined => {
      if (typeof item !== 'object' || item === null) return undefined
      const record = item as Record<string, unknown>
      if (
        typeof record.id !== 'string' ||
        typeof record.title !== 'string' ||
        !['command', 'error', 'info', 'success'].includes(
          String(record.kind),
        ) ||
        (record.body !== undefined && typeof record.body !== 'string')
      ) {
        return undefined
      }
      return {
        body: typeof record.body === 'string' ? record.body : undefined,
        id: record.id,
        kind: record.kind as TerminalEntry['kind'],
        refs: normalizeRefs(record.refs),
        title: record.title,
      }
    },
    TERMINAL_ENTRY_HISTORY_LIMIT,
    true,
  )
}

function persistTerminalHistory(history: TerminalEntry[]): void {
  writeStoredJson(
    TERMINAL_ENTRY_HISTORY_STORAGE_KEY,
    history.slice(-TERMINAL_ENTRY_HISTORY_LIMIT),
  )
}

function clearPersistedTerminalHistory(): void {
  removeStoredValue(TERMINAL_ENTRY_HISTORY_STORAGE_KEY)
}

function extractVikingUris(text: string): string[] {
  return text.match(/viking:\/\/[^\s,，)）\]}】'"`]+/g) ?? []
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

function parseWaitTimeout(body: string): number | undefined {
  const trimmed = body.trim()
  if (!trimmed) return undefined
  const match = trimmed.match(/^(?:--timeout\s+)?(\d+(?:\.\d+)?)$/)
  if (!match) {
    throw new Error('Usage: /wait [--timeout seconds]')
  }
  return Number(match[1])
}

export function TerminalPanel({
  currentUri,
  entries,
  onOpenAddResource,
  onOpenResource,
  openingUri,
}: {
  currentUri: string
  entries: VikingFsEntry[]
  onOpenAddResource: () => void
  onOpenResource: ResourceOpenHandler
  openingUri: string | null
}) {
  const { t } = useTranslation('playground')
  const { connectionRole } = useAppConnection()
  const [command, setCommand] = useState('')
  const [running, setRunning] = useState(false)
  const [suggestionsOpen, setSuggestionsOpen] = useState(false)
  const [activeSuggestionIndex, setActiveSuggestionIndex] = useState(0)
  const [commandHistory, setCommandHistory] = useState(loadCommandHistory)
  const [history, setHistory] = useState(loadTerminalHistory)
  const [historyOpen, setHistoryOpen] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const commands = useMemo<TerminalCommandView[]>(
    () =>
      TERMINAL_COMMANDS.filter(
        (item) =>
          !item.adminOnly ||
          connectionRole === 'admin' ||
          connectionRole === 'root',
      ).map((item) => ({
        ...item,
        description: t(`terminal.commands.${item.key}.description`, {
          defaultValue: item.command,
        }),
        usage: t(`terminal.commands.${item.key}.usage`, {
          defaultValue: item.insertText.trim(),
        }),
      })),
    [connectionRole, t],
  )

  const groupLabels = useMemo(
    () => ({
      memories: t('terminal.groupLabels.memories'),
      resources: t('terminal.groupLabels.resources'),
      skills: t('terminal.groupLabels.skills'),
    }),
    [t],
  )

  const resourceCandidates = useMemo(() => {
    const candidates = new Set<string>([currentUri, ROOT_URI])
    for (const entry of visibleContextEntries(entries)) {
      const ref = entryToRef(entry)
      candidates.add(ref.uri)
    }
    for (const item of history) {
      for (const ref of item.refs ?? []) {
        candidates.add(ref.uri)
      }
      if (item.body) {
        for (const uri of extractVikingUris(item.body)) candidates.add(uri)
      }
      for (const uri of extractVikingUris(item.title)) candidates.add(uri)
    }
    for (const item of commandHistory) {
      for (const uri of extractVikingUris(item)) candidates.add(uri)
    }
    return Array.from(candidates).filter(Boolean).sort()
  }, [commandHistory, currentUri, entries, history])

  const suggestions = useMemo<TerminalSuggestion[]>(() => {
    const rawQuery = command.trimStart()
    const query = rawQuery.toLowerCase()
    const commandMatches =
      !query || query === '/'
        ? commands.map((item) => ({
            ...item,
            id: `command:${item.command}`,
          }))
        : query.startsWith('/')
          ? commands
              .filter(
                (item) =>
                  item.command.toLowerCase().startsWith(query) ||
                  item.description.toLowerCase().includes(query.slice(1)),
              )
              .map((item) => ({
                ...item,
                id: `command:${item.command}`,
              }))
          : []

    const activeCommand = [...commands]
      .sort((a, b) => b.command.length - a.command.length)
      .find(
        (item) =>
          rawQuery === item.command || rawQuery.startsWith(`${item.command} `),
      )
    const resourceMatches =
      activeCommand && URI_ARGUMENT_COMMANDS.has(activeCommand.command)
        ? resourceCandidates
            .filter((uri) => {
              const argQuery = rawQuery
                .slice(activeCommand.command.length)
                .trimStart()
                .toLowerCase()
              if (!argQuery) return true
              return (
                uri.toLowerCase().startsWith(argQuery) ||
                uri.toLowerCase().includes(argQuery)
              )
            })
            .slice(0, 12)
            .map((uri) => ({
              ...activeCommand,
              command: uri,
              description: t('terminal.resourceSuggestion'),
              group: 'resource' as const,
              id: `resource:${activeCommand.command}:${uri}`,
              insertText: `${activeCommand.command} ${uri}`,
              usage: `${activeCommand.command} ${uri}`,
            }))
        : []

    const historyMatches = commandHistory
      .filter((item) => {
        const lower = item.toLowerCase()
        return lower !== query && lower.startsWith(query)
      })
      .slice(0, 8)
      .map((item) => ({
        adminOnly: false,
        command: item,
        description: t('terminal.historySuggestion'),
        executable: false,
        group: 'history' as const,
        id: `history:${item}`,
        insertText: item,
        key: 'history',
        usage: item,
      }))

    const seen = new Set<string>()
    return [...commandMatches, ...resourceMatches, ...historyMatches].filter(
      (item) => {
        if (seen.has(item.insertText)) return false
        seen.add(item.insertText)
        return true
      },
    )
  }, [command, commandHistory, commands, resourceCandidates, t])

  const groupedSuggestions = useMemo(() => {
    const order: TerminalSuggestionGroup[] = [
      'core',
      'filesystem',
      'search',
      'status',
      'resource',
      'history',
    ]
    return order
      .map((group) => ({
        group,
        items: suggestions.filter((item) => item.group === group),
        label: t(`terminal.commandGroups.${group}`),
      }))
      .filter((group) => group.items.length > 0)
  }, [suggestions, t])

  useEffect(() => {
    setActiveSuggestionIndex(0)
  }, [suggestions.length])

  useEffect(() => {
    scrollRef.current?.scrollTo({
      behavior: 'smooth',
      top: scrollRef.current.scrollHeight,
    })
  }, [history.length, running])

  const append = useCallback((entry: Omit<TerminalEntry, 'id'>) => {
    setHistory((prev) => {
      const next = [
        ...prev,
        {
          ...entry,
          id: `${Date.now()}-${prev.length}`,
        },
      ].slice(-TERMINAL_ENTRY_HISTORY_LIMIT)
      persistTerminalHistory(next)
      return next
    })
  }, [])

  const clearHistory = useCallback(() => {
    setHistory([])
    clearPersistedTerminalHistory()
  }, [])

  const rememberCommand = useCallback((raw: string) => {
    const trimmed = raw.trim()
    if (!trimmed) return
    setCommandHistory((prev) => {
      const next = [
        trimmed,
        ...prev.filter((item) => item.toLowerCase() !== trimmed.toLowerCase()),
      ].slice(0, TERMINAL_COMMAND_HISTORY_LIMIT)
      persistCommandHistory(next)
      return next
    })
  }, [])

  const runCommand = useCallback(
    async (raw: string) => {
      const trimmed = raw.trim()
      if (!trimmed || running) return

      append({ kind: 'command', title: trimmed })
      rememberCommand(trimmed)
      setCommand('')
      setRunning(true)

      try {
        const [name = '', ...args] = trimmed.split(/\s+/)
        const body = args.join(' ').trim()

        if (trimmed.startsWith('viking://')) {
          await onOpenResource(trimmed)
          append({
            kind: 'success',
            refs: [{ uri: trimmed }],
            title: t('terminal.opened'),
          })
          return
        }

        switch (name) {
          case '/status': {
            const root = await fetchFsList(ROOT_URI, { nodeLimit: 12 })
            const status =
              await getOvResult<Record<string, unknown>>(getSystemStatus())
            append({
              body: `${t('terminal.onlineBody', { count: root.entries.length })}\n\n${formatJson(status)}`,
              kind: 'success',
              refs: root.entries.slice(0, 6).map(entryToRef),
              title: t('terminal.onlineTitle'),
            })
            return
          }
          case '/health': {
            const health =
              await getOvResult<Record<string, unknown>>(getHealth())
            append({
              body: formatJson(health),
              kind: 'success',
              title: 'health',
            })
            return
          }
          case '/wait': {
            const timeout = parseWaitTimeout(body)
            const result = await getOvResult<unknown>(
              postSystemWait({
                body: {
                  timeout,
                },
              }),
            )
            append({
              body: formatJson(result),
              kind: 'success',
              title: 'wait',
            })
            return
          }
          case '/ls': {
            const target = body ? normalizeDirUri(body) : currentUri
            const result = body
              ? await fetchFsList(target, {
                  nodeLimit: 60,
                  output: 'agent',
                  showAllHidden: true,
                })
              : { entries, uri: currentUri }
            const visibleEntries = visibleContextEntries(result.entries)
            append({
              body: t('terminal.lsBody', {
                count: visibleEntries.length,
                uri: target,
              }),
              kind: 'success',
              refs: visibleEntries.map(entryToRef),
              title: `ls ${target}`,
            })
            return
          }
          case '/tree': {
            const target = body ? normalizeDirUri(body) : currentUri
            const result = await fetchFsTree(target, {
              nodeLimit: 80,
              output: 'agent',
              showAllHidden: true,
            })
            const visibleEntries = visibleContextEntries(result.nodes)
            append({
              body: t('terminal.lsBody', {
                count: visibleEntries.length,
                uri: target,
              }),
              kind: 'success',
              refs: visibleEntries.map(entryToRef),
              title: `tree ${target}`,
            })
            return
          }
          case '/stat': {
            if (!body) throw new Error(t('terminal.enterUri'))
            const uri = cleanVikingUri(body)
            if (!uri) throw new Error(t('terminal.enterUri'))
            const entry = await fetchFsStat(uri, { throwOnError: true })
            append({
              body: formatJson(entry),
              kind: 'success',
              refs: [entryToRef(entry)],
              title: `stat ${uri}`,
            })
            return
          }
          case '/read': {
            if (!body) throw new Error(t('terminal.readUsage'))
            const uri = cleanVikingUri(body)
            if (!uri) throw new Error(t('terminal.enterUri'))
            const content = await fetchFileContent(uri, {
              limit: 1200,
              raw: true,
            })
            await onOpenResource(uri)
            append({
              body: content.content.slice(0, 1200) || t('terminal.fileEmpty'),
              kind: 'success',
              refs: [{ uri }],
              title: `read ${uri}`,
            })
            return
          }
          case '/abstract':
          case '/overview': {
            if (!body) throw new Error(t('terminal.enterUri'))
            const uri = cleanVikingUri(body)
            if (!uri) throw new Error(t('terminal.enterUri'))
            const level = name === '/abstract' ? 'abstract' : 'overview'
            const content = await fetchDirectoryLevelContent(uri, level)
            await onOpenResource(uri)
            append({
              body: content || t('terminal.fileEmpty'),
              kind: 'success',
              refs: [{ uri }],
              title: `${name.slice(1)} ${uri}`,
            })
            return
          }
          case '/find':
          case '/search': {
            if (!body) throw new Error(t('terminal.searchUsage', { name }))
            const result = await fetchSearch(body, {
              limit: 8,
              targetUri: currentUri,
            })
            append({
              body:
                result.query_plan?.reasoning ||
                t('terminal.hits', {
                  memories: result.memories.length,
                  resources: result.resources.length,
                  skills: result.skills.length,
                }),
              kind: 'success',
              refs: searchResultToRefs(result, groupLabels),
              title: `${name} ${body}`,
            })
            return
          }
          case '/add-resource': {
            onOpenAddResource()
            append({
              body: t('terminal.addResourceBody'),
              kind: 'info',
              title: t('terminal.addResourceTitle'),
            })
            return
          }
          default:
            throw new Error(t('terminal.unknownCommand'))
        }
      } catch (error) {
        append({
          body: getErrorMessage(error),
          kind: 'error',
          title: t('terminal.commandFailed'),
        })
      } finally {
        setRunning(false)
      }
    },
    [
      append,
      currentUri,
      entries,
      groupLabels,
      onOpenAddResource,
      onOpenResource,
      running,
      rememberCommand,
      t,
    ],
  )

  const acceptSuggestion = useCallback((suggestion: { insertText: string }) => {
    setCommand(suggestion.insertText)
    setSuggestionsOpen(false)
    window.requestAnimationFrame(() => {
      const input = inputRef.current
      input?.focus()
      input?.setSelectionRange(
        suggestion.insertText.length,
        suggestion.insertText.length,
      )
    })
  }, [])

  const runSuggestion = useCallback(
    (suggestion: TerminalSuggestion) => {
      setSuggestionsOpen(false)
      if (suggestion.executable) {
        void runCommand(suggestion.insertText)
        return
      }
      setCommand(suggestion.insertText)
    },
    [runCommand],
  )

  const quickCommands = commands.filter((item) =>
    ['/status', '/find', '/search', '/add-resource'].includes(item.command),
  )

  const quickStartExamples = useMemo<TerminalQuickStartExample[]>(
    () => [
      {
        action: () => void runCommand('/add-resource'),
        code: t('terminal.quickStart.addResource.code'),
        command: t('terminal.quickStart.addResource.command'),
        key: 'add-resource',
        title: t('terminal.quickStart.addResource.title'),
      },
      {
        code: t('terminal.quickStart.addMemory.code'),
        command: t('terminal.quickStart.addMemory.command'),
        key: 'add-memory',
        title: t('terminal.quickStart.addMemory.title'),
      },
      {
        action: () => void runCommand('/find openviking 有什么价值？'),
        code: t('terminal.quickStart.find.code'),
        command: t('terminal.quickStart.find.command'),
        key: 'find',
        title: t('terminal.quickStart.find.title'),
      },
    ],
    [runCommand, t],
  )

  return (
    <>
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="border-b bg-background/70 px-4 py-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <TerminalIcon className="size-3.5 shrink-0" />
            <span className="min-w-0 flex-1 truncate">
              {t('terminal.header')}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="size-7 shrink-0"
              title={t('terminal.history')}
              onClick={() => setHistoryOpen(true)}
            >
              <HistoryIcon className="size-3.5" />
            </Button>
          </div>
        </div>
        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
        >
          <div className="space-y-3">
            {history.length === 0 && !running ? (
              <TerminalQuickStart
                examples={quickStartExamples}
                title={t('terminal.quickStart.title')}
              />
            ) : null}
            {history.map((entry) => (
              <TerminalHistoryItem
                key={entry.id}
                entry={entry}
                onOpenResource={onOpenResource}
                openingUri={openingUri}
              />
            ))}
            {running ? (
              <div className="flex items-center gap-2 rounded-lg border bg-background px-3 py-2 text-xs text-muted-foreground">
                <Loader2Icon className="size-3.5 animate-spin" />
                {t('terminal.running')}
              </div>
            ) : null}
          </div>
        </div>
        <div className="border-t bg-background/80 p-3">
          <div className="mb-2 flex flex-wrap gap-1.5">
            {quickCommands.map((item) => (
              <button
                key={item.command}
                type="button"
                className="rounded-md border bg-muted/40 px-2 py-1 font-mono text-[11px] text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
                onClick={() => acceptSuggestion(item)}
              >
                {item.command}
              </button>
            ))}
          </div>
          <form
            className="relative flex items-center gap-2 rounded-lg border bg-muted/30 px-2 py-2"
            onSubmit={(event) => {
              event.preventDefault()
              void runCommand(command)
            }}
          >
            <input
              ref={inputRef}
              value={command}
              onBlur={() => {
                window.setTimeout(() => setSuggestionsOpen(false), 120)
              }}
              onChange={(event) => {
                setCommand(event.target.value)
                setSuggestionsOpen(true)
              }}
              onFocus={() => setSuggestionsOpen(true)}
              onKeyDown={(event) => {
                if (!suggestionsOpen || suggestions.length === 0) return
                if (event.key === 'ArrowDown') {
                  event.preventDefault()
                  setActiveSuggestionIndex((current) =>
                    Math.min(current + 1, suggestions.length - 1),
                  )
                  return
                }
                if (event.key === 'ArrowUp') {
                  event.preventDefault()
                  setActiveSuggestionIndex((current) =>
                    Math.max(current - 1, 0),
                  )
                  return
                }
                if (event.key === 'Tab') {
                  event.preventDefault()
                  acceptSuggestion(suggestions[activeSuggestionIndex])
                  return
                }
                if (
                  event.key === 'ArrowRight' &&
                  event.currentTarget.selectionStart === command.length &&
                  event.currentTarget.selectionEnd === command.length
                ) {
                  event.preventDefault()
                  acceptSuggestion(suggestions[activeSuggestionIndex])
                  return
                }
                if (event.key === 'Enter') {
                  event.preventDefault()
                  if (command.trim() === '/') {
                    acceptSuggestion(suggestions[activeSuggestionIndex])
                    return
                  }
                  runSuggestion(suggestions[activeSuggestionIndex])
                  return
                }
                if (event.key === 'Escape') {
                  setSuggestionsOpen(false)
                }
              }}
              placeholder={t('terminal.placeholder')}
              className="h-8 min-w-0 flex-1 bg-transparent font-mono text-sm outline-none placeholder:text-muted-foreground/60"
            />
            {suggestionsOpen && suggestions.length > 0 ? (
              <div className="absolute bottom-[calc(100%+0.5rem)] left-0 right-0 z-20 overflow-hidden rounded-lg border bg-popover shadow-xl">
                <div className="border-b px-3 py-2 text-[11px] font-medium text-muted-foreground">
                  {t('terminal.suggestionsTitle')}
                </div>
                <div className="max-h-64 overflow-y-auto p-1">
                  {(() => {
                    let suggestionIndex = 0
                    return groupedSuggestions.map((group) => (
                      <div key={group.group} className="py-1 first:pt-0">
                        <div className="px-2 pb-1 pt-1 text-[10px] font-semibold text-muted-foreground">
                          {group.label}
                        </div>
                        {group.items.map((suggestion) => {
                          const index = suggestionIndex
                          suggestionIndex += 1
                          return (
                            <button
                              key={suggestion.id}
                              type="button"
                              className={cn(
                                'grid w-full min-w-0 grid-cols-[minmax(5rem,12rem)_minmax(0,1fr)] items-start gap-3 rounded-md px-2 py-2 text-left transition-colors',
                                index === activeSuggestionIndex
                                  ? 'bg-primary/10 text-foreground'
                                  : 'hover:bg-muted/60',
                              )}
                              onClick={() => acceptSuggestion(suggestion)}
                              onMouseDown={(event) => event.preventDefault()}
                              onMouseEnter={() =>
                                setActiveSuggestionIndex(index)
                              }
                            >
                              <span
                                className="min-w-0 truncate font-mono text-xs font-semibold text-primary"
                                title={suggestion.command}
                              >
                                {suggestion.command}
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="block text-xs text-foreground">
                                  {suggestion.description}
                                </span>
                                <span className="mt-0.5 block truncate font-mono text-[11px] text-muted-foreground">
                                  {suggestion.usage}
                                </span>
                              </span>
                            </button>
                          )
                        })}
                      </div>
                    ))
                  })()}
                </div>
                <div className="border-t px-3 py-1.5 text-[10px] text-muted-foreground">
                  {t('terminal.suggestionsHint')}
                </div>
              </div>
            ) : null}
            <Button
              type="submit"
              size="icon-sm"
              disabled={running || !command.trim()}
            >
              <SendIcon className="size-4" />
            </Button>
          </form>
        </div>
      </div>
      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="gap-4 sm:max-w-2xl">
          <DialogHeader>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <DialogTitle>{t('terminal.historyTitle')}</DialogTitle>
                <DialogDescription>
                  {t('terminal.historyDescription')}
                </DialogDescription>
              </div>
              {history.length > 0 ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="size-7 shrink-0"
                  title={t('terminal.clearHistory')}
                  onClick={clearHistory}
                >
                  <TrashIcon className="size-3.5" />
                </Button>
              ) : null}
            </div>
          </DialogHeader>
          <div className="max-h-[460px] overflow-y-auto pr-1">
            {history.length === 0 ? (
              <div className="rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">
                {t('terminal.noHistory')}
              </div>
            ) : (
              <div className="space-y-3">
                {history.map((entry) => (
                  <TerminalHistoryItem
                    key={`dialog-${entry.id}`}
                    entry={entry}
                    onOpenResource={onOpenResource}
                    openingUri={openingUri}
                  />
                ))}
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

function TerminalQuickStart({
  examples,
  title,
}: {
  examples: TerminalQuickStartExample[]
  title: string
}) {
  return (
    <section className="space-y-3 py-3">
      <div className="text-xs font-semibold text-muted-foreground">{title}</div>
      <div className="space-y-2">
        {examples.map((example) => {
          const content = (
            <>
              <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-background text-muted-foreground">
                <ArrowRightIcon className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-foreground">
                    {example.title}
                  </span>
                  <span className="rounded-md border bg-background px-2 py-0.5 font-mono text-xs font-semibold">
                    {example.command}
                  </span>
                </span>
                <span className="mt-1 block truncate font-mono text-xs text-muted-foreground">
                  {example.code}
                </span>
              </span>
            </>
          )

          if (!example.action) {
            return (
              <div
                key={example.key}
                className="flex min-w-0 items-center gap-3 rounded-lg bg-muted/40 px-3 py-3"
              >
                {content}
              </div>
            )
          }

          return (
            <button
              key={example.key}
              type="button"
              className="flex w-full min-w-0 items-center gap-3 rounded-lg bg-muted/40 px-3 py-3 text-left transition-colors hover:bg-muted/70"
              onClick={example.action}
            >
              {content}
            </button>
          )
        })}
      </div>
    </section>
  )
}

export function TerminalHistoryItem({
  entry,
  onOpenResource,
  openingUri,
}: {
  entry: TerminalEntry
  onOpenResource: ResourceOpenHandler
  openingUri: string | null
}) {
  const Icon =
    entry.kind === 'command'
      ? TerminalIcon
      : entry.kind === 'success'
        ? CheckCircle2Icon
        : entry.kind === 'error'
          ? XCircleIcon
          : SparklesIcon

  return (
    <section
      className={cn(
        'rounded-lg border bg-background px-3 py-3 text-sm',
        entry.kind === 'command' && 'border-muted bg-muted/40 font-mono',
        entry.kind === 'error' && 'border-destructive/30 bg-destructive/5',
      )}
    >
      <div className="mb-2 flex items-center gap-2">
        <Icon
          className={cn(
            'size-3.5 shrink-0 text-muted-foreground',
            entry.kind === 'success' && 'text-primary',
            entry.kind === 'error' && 'text-destructive',
          )}
        />
        <span className="min-w-0 truncate text-xs font-semibold">
          {entry.title}
        </span>
      </div>
      {entry.body ? (
        <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 text-xs leading-5 text-muted-foreground">
          {entry.body}
        </pre>
      ) : null}
      {entry.refs?.length ? (
        <ResourceRefList
          className="mt-2"
          refs={entry.refs}
          onOpenResource={onOpenResource}
          openingUri={openingUri}
        />
      ) : null}
    </section>
  )
}
