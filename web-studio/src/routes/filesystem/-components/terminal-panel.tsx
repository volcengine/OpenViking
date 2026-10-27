import i18n from '#/i18n/core'
import { useQuery } from '@tanstack/react-query'
import {
  active as activeCompileTask,
  fetchCompileSkills,
  fetchCompileTask,
} from '#/routes/compile/-lib/api'
import { compileSuggestions } from '#/routes/compile/-lib/suggestions'
import { Link } from '@tanstack/react-router'
import {
  CompileCommandError,
  isCompileCommand,
  compileHistory,
  tokenize,
} from '#/routes/compile/-lib/commands'
import { runCompileSubmission } from '#/routes/compile/-lib/terminal'
import { addNewSkill } from '#/routes/skills/-lib/add-skill'
import { useInvalidateVikingFs } from '#/routes/resources/-hooks/viking-fm'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  ArrowRightIcon,
  CheckCircle2Icon,
  HistoryIcon,
  Loader2Icon,
  SendIcon,
  SparklesIcon,
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
  getTaskByTaskId,
  postResources,
  postContentWrite,
  postSystemWait,
} from '#/lib/ov-client'
import {
  addMessage,
  commitSession,
  createSession,
  deleteSession,
  fetchSession,
  fetchSessionArchive,
  fetchSessionContext,
  fetchSessionMessages,
  fetchSessions,
  fetchSessionToolResult,
  fetchSessionToolResults,
  searchSessionToolResult,
} from '#/lib/sessions/api'
import { cn } from '#/lib/utils'
import { fetchGlob, fetchGrep } from '#/lib/retrieval'
import {
  fetchDirectoryLevelContent,
  fetchFileContent,
  fetchFind,
  fetchFsList,
  fetchFsStat,
  fetchFsTree,
  fetchSearch,
} from '#/routes/resources/-lib/api'
import { normalizeDirUri } from '#/routes/resources/-lib/normalize'
import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'
import type { SessionListItem } from '@ov-server/api/v1/sessions'

import { ROOT_URI, TERMINAL_COMMANDS } from '../-lib/constants'
import { getCommandInputHint } from '../-lib/terminal-input-hint'
import {
  parseAddMemory,
  parseAddResource,
  parseAddSkill,
  parseWrite,
  submitMemoryCommand,
  taskResultDetails,
  taskResultResourceUri,
  TerminalActionError,
  waitForResourceTask,
} from '../-lib/terminal-actions'
import type { ResourceTaskState } from '../-lib/terminal-actions'
import type {
  ResourceRef,
  ResourceOpenHandler,
  TerminalCommandGroup,
  TerminalCommandParameterKey,
  TerminalCommandView,
  TerminalEntry,
} from '../-lib/types'
import {
  cleanVikingUri,
  createIdentityStorageKey,
  entryToRef,
  getErrorMessage,
  registerFilesystemAgentSessionId,
  removeStoredValue,
  searchResultToRefs,
  visibleContextEntries,
} from '../-lib/utils'
import { ResourceRefList } from './resource-ref-list'
import type { ResourceImportResult } from '#/routes/resources/-lib/resource-import-types'

const TERMINAL_COMMAND_HISTORY_STORAGE_KEY =
  'openviking.playground.terminalCommandHistory'
const TERMINAL_ENTRY_HISTORY_STORAGE_KEY =
  'openviking.playground.terminalEntryHistory'
const TERMINAL_COMMAND_HISTORY_LIMIT = 50
const TERMINAL_ENTRY_HISTORY_LIMIT = 100
// Keep terminal history across SPA route changes, but discard it on page reload.
let terminalPageCache: {
  identityScopeKey: string
  history: TerminalEntry[]
  commandHistory: string[]
} | null = null
const URI_ARGUMENT_COMMANDS = new Set([
  '/abstract',
  '/ls',
  '/overview',
  '/read',
  '/stat',
  '/tree',
])

type SessionSubcommandHelp = {
  examples: string[]
  insertText: string
  key: string
  parameters: TerminalCommandParameterKey[]
  usage: string
}

const SESSION_SUBCOMMANDS: SessionSubcommandHelp[] = [
  {
    examples: ['session.current'],
    insertText: '/session current',
    key: 'current',
    parameters: [],
    usage: '/session current',
  },
  {
    examples: ['session.list'],
    insertText: '/session list',
    key: 'list',
    parameters: [],
    usage: '/session list',
  },
  {
    examples: ['session.create'],
    insertText: '/session create ',
    key: 'create',
    parameters: ['sessionId'],
    usage: '/session create [session_id]',
  },
  {
    examples: ['session.switch'],
    insertText: '/session switch ',
    key: 'switch',
    parameters: ['sessionId'],
    usage: '/session switch <session_id>',
  },
  {
    examples: ['session.get'],
    insertText: '/session get ',
    key: 'get',
    parameters: ['sessionId'],
    usage: '/session get [session_id]',
  },
  {
    examples: ['session.context'],
    insertText: '/session context ',
    key: 'context',
    parameters: ['sessionId', 'tokenBudget'],
    usage: '/session context [<session_id>] [--token-budget <tokens>]',
  },
  {
    examples: ['session.messages'],
    insertText: '/session messages ',
    key: 'messages',
    parameters: ['sessionId'],
    usage: '/session messages [session_id]',
  },
  {
    examples: ['session.archive'],
    insertText: '/session archive ',
    key: 'archive',
    parameters: ['sessionId', 'archiveId'],
    usage: '/session archive [session_id] <archive_id>',
  },
  {
    examples: ['session.commit'],
    insertText: '/session commit ',
    key: 'commit',
    parameters: ['sessionId', 'keepRecent'],
    usage: '/session commit [<session_id>] [--keep-recent <count>]',
  },
  {
    examples: ['session.message'],
    insertText: '/session message ',
    key: 'message',
    parameters: ['sessionId', 'messageRole', 'messageContent'],
    usage: '/session message [<session_id>] <role> <content>',
  },
  {
    examples: ['session.toolResults'],
    insertText: '/session tool-results ',
    key: 'tool-results',
    parameters: ['sessionId', 'toolName', 'limit'],
    usage: '/session tool-results [<session_id>] [--limit <count>]',
  },
  {
    examples: ['session.toolResult'],
    insertText: '/session tool-result ',
    key: 'tool-result',
    parameters: ['sessionId', 'toolResultId', 'limit', 'offset'],
    usage: '/session tool-result [session_id] <tool_result_id>',
  },
  {
    examples: ['session.toolSearch'],
    insertText: '/session tool-search ',
    key: 'tool-search',
    parameters: ['sessionId', 'toolResultId', 'query', 'limit', 'contextChars'],
    usage: '/session tool-search [<session_id>] <tool_result_id> <query>',
  },
  {
    examples: ['session.delete'],
    insertText: '/session delete ',
    key: 'delete',
    parameters: ['sessionId'],
    usage: '/session delete <session_id>',
  },
]

const SESSION_SUBCOMMANDS_BY_KEY = new Map(
  SESSION_SUBCOMMANDS.map((item) => [item.key, item]),
)

type TerminalSuggestionGroup =
  | TerminalCommandGroup
  | 'history'
  | 'resource'
  | 'subcommand'

type TerminalSuggestion = Omit<TerminalCommandView, 'group'> & {
  group: TerminalSuggestionGroup
  id: string
}

type TerminalQuickStartExample = {
  action?: () => void
  command: string
  code: string
  key: string
}

type ScopedSearchInput = {
  query: string
  scopeUri?: string
  limit?: number
  scoreThreshold?: number
  sessionId?: string
}

type ParsedOptions = {
  flags: Map<string, string[]>
  positional: string[]
}

const PARAMETER_META: Partial<
  Record<
    TerminalCommandParameterKey,
    {
      type: 'Text' | 'URI' | 'Number' | 'Boolean'
      required?: boolean
      defaultValue?: string
    }
  >
> = {
  archiveId: { type: 'Text', required: true },
  compileFrom: { type: 'URI', required: true },
  compileSkill: { type: 'URI', required: true },
  compileTo: { type: 'URI', required: true },
  contextChars: { type: 'Number' },
  ignoreCase: { type: 'Boolean', defaultValue: 'false' },
  keepRecent: { type: 'Number' },
  limit: { type: 'Number' },
  memoryContent: { type: 'Text', required: true },
  memorySessionId: { type: 'Text' },
  messageContent: { type: 'Text', required: true },
  messageRole: { type: 'Text', required: true },
  nodeLimit: { type: 'Number', defaultValue: '10' },
  offset: { type: 'Number', defaultValue: '0' },
  pattern: { type: 'Text', required: true },
  query: { type: 'Text', required: true },
  resourceParent: { type: 'URI' },
  resourceReason: { type: 'Text' },
  resourceArgs: { type: 'Text' },
  resourceSource: { type: 'Text' },
  resourceTo: { type: 'URI' },
  scope: { type: 'URI' },
  searchSessionId: { type: 'Text' },
  sessionAction: { type: 'Text' },
  sessionId: { type: 'Text' },
  skillParent: { type: 'URI', defaultValue: 'viking://~/skills' },
  skillSource: { type: 'Text', required: true },
  taskAction: { type: 'Text', required: true },
  taskId: { type: 'Text', required: true },
  threshold: { type: 'Number' },
  timeout: { type: 'Number' },
  tokenBudget: { type: 'Number' },
  toolName: { type: 'Text' },
  toolResultId: { type: 'Text', required: true },
  uri: { type: 'URI' },
  writeContent: { type: 'Text', required: true },
  writeMode: { type: 'Text', defaultValue: 'replace' },
  writeWait: { type: 'Boolean', defaultValue: 'false' },
}

function extractVikingUris(text: string): string[] {
  return text.match(/viking:\/\/[^\s,，)）\]}】'"`]+/g) ?? []
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

function joinBodyLines(lines: Array<string | undefined>): string {
  return lines.filter((line): line is string => Boolean(line)).join('\n')
}

function parseOptions(body: string): ParsedOptions {
  const tokens = body.trim().split(/\s+/).filter(Boolean)
  const flags = new Map<string, string[]>()
  const positional: string[] = []

  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index]
    if (!token.startsWith('--')) {
      positional.push(token)
      continue
    }

    const eqIndex = token.indexOf('=')
    const key = token.slice(2, eqIndex > -1 ? eqIndex : undefined)
    const inlineValue = eqIndex > -1 ? token.slice(eqIndex + 1) : undefined
    let value = inlineValue ?? 'true'

    if (
      inlineValue === undefined &&
      tokens[index + 1] &&
      !tokens[index + 1].startsWith('--')
    ) {
      value = tokens[index + 1]
      index += 1
    }

    flags.set(key, [...(flags.get(key) ?? []), value])
  }

  return { flags, positional }
}

function getLastFlag(
  flags: Map<string, string[]>,
  key: string,
): string | undefined {
  return flags.get(key)?.at(-1)
}

function getNumberFlag(
  flags: Map<string, string[]>,
  key: string,
): number | undefined {
  const value = getLastFlag(flags, key)
  if (value === undefined || value === 'true') return undefined
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) {
    throw new Error(i18n.t('studio:errors.invalidFlag', { key, value }))
  }
  return parsed
}

function getBooleanFlag(flags: Map<string, string[]>, key: string): boolean {
  return flags.has(key) && getLastFlag(flags, key) !== 'false'
}

function parseScopedSearchInput(
  body: string,
  currentUri: string,
  missingScopeMessage: string,
): ScopedSearchInput {
  const tokens = tokenize(`/find ${body}`).slice(1)
  const queryParts: string[] = []
  let scopeUri: string | undefined
  let limit: number | undefined
  let scoreThreshold: number | undefined
  let sessionId: string | undefined

  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index]
    if (token === '--scope' || token === '--uri' || token === '-u') {
      const value = tokens[index + 1]
      if (!value) throw new Error(missingScopeMessage)
      scopeUri = value === '.' ? currentUri : normalizeDirUri(value)
      index += 1
      continue
    }
    if (token.startsWith('--scope=') || token.startsWith('--uri=')) {
      const value = token.slice(token.indexOf('=') + 1)
      if (!value) throw new Error(missingScopeMessage)
      scopeUri = value === '.' ? currentUri : normalizeDirUri(value)
      continue
    }
    if (
      [
        '--node-limit',
        '--limit',
        '-n',
        '--threshold',
        '-t',
        '--session-id',
      ].includes(token)
    ) {
      const value = tokens[index + 1]
      if (!value) throw new Error(missingScopeMessage)
      if (token === '--session-id') sessionId = value
      else {
        const parsed = Number(value)
        if (
          !Number.isFinite(parsed) ||
          (token !== '--threshold' &&
            token !== '-t' &&
            (!Number.isInteger(parsed) || parsed < 0))
        )
          throw new Error(missingScopeMessage)
        if (token === '--threshold' || token === '-t') scoreThreshold = parsed
        else limit = parsed
      }
      index += 1
      continue
    }
    if (token.startsWith('-')) throw new Error(missingScopeMessage)
    queryParts.push(token)
  }

  return {
    query: queryParts.join(' ').trim(),
    scopeUri,
    limit,
    scoreThreshold,
    sessionId,
  }
}

function sessionToRef(session: Pick<SessionListItem, 'session_id' | 'uri'>) {
  return {
    label: session.session_id,
    meta: 'session',
    uri: session.uri || `viking://~/sessions/${session.session_id}`,
  }
}

export function TerminalPanel({
  currentUri,
  entries,
  onOpenResource,
  openingUri,
  onSessionChange,
  sessionId,
  toolbarContainer,
}: {
  currentUri: string
  entries: VikingFsEntry[]
  onOpenResource: ResourceOpenHandler
  openingUri: string | null
  onSessionChange: (sessionId: string) => void
  sessionId?: string
  toolbarContainer: HTMLDivElement | null
}) {
  const { t } = useTranslation('filesystem')
  const { connectionRole, identityScopeKey } = useAppConnection()
  const { invalidateAll } = useInvalidateVikingFs()
  const [command, setCommand] = useState('')
  const [running, setRunning] = useState(false)
  const compileSkills = useQuery({
    queryKey: ['compile-skills', identityScopeKey],
    queryFn: ({ signal }) => fetchCompileSkills(signal),
    enabled: /^(?:ov\s+)?\/?compile\s/.test(command),
  })
  const identityRef = useRef(identityScopeKey)
  identityRef.current = identityScopeKey
  const mountedRef = useRef(false)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])
  const resourcePollsRef = useRef(new Set<AbortController>())
  const trackedResourceEntriesRef = useRef(new Set<string>())
  useEffect(() => {
    return () => {
      for (const controller of resourcePollsRef.current) controller.abort()
      resourcePollsRef.current.clear()
    }
  }, [identityScopeKey])
  const [suggestionsOpen, setSuggestionsOpen] = useState(false)
  const [activeSuggestionIndex, setActiveSuggestionIndex] = useState(0)
  const [previewSuggestionIndex, setPreviewSuggestionIndex] = useState<
    number | null
  >(null)
  const [commandHistory, setCommandHistory] = useState<string[]>(() =>
    terminalPageCache?.identityScopeKey === identityScopeKey
      ? terminalPageCache.commandHistory
      : [],
  )
  const [history, setHistory] = useState<TerminalEntry[]>(() =>
    terminalPageCache?.identityScopeKey === identityScopeKey
      ? terminalPageCache.history
      : [],
  )
  const [historyOpen, setHistoryOpen] = useState(false)
  const [inputFocused, setInputFocused] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const suggestionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const scrollRef = useRef<HTMLDivElement>(null)

  const commands = useMemo<TerminalCommandView[]>(
    () =>
      TERMINAL_COMMANDS.filter(
        (item) =>
          !item.adminOnly ||
          connectionRole === 'admin' ||
          connectionRole === 'root',
      ).map((item) => {
        return {
          ...item,
          description: t(`terminal.commands.${item.key}.description`),
          usage: t(`terminal.commands.${item.key}.usage`),
        }
      }),
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

  const activeCommand = useMemo(() => {
    const rawQuery = command.trimStart()
    return [...commands]
      .sort((a, b) => b.command.length - a.command.length)
      .find(
        (item) =>
          rawQuery === item.command || rawQuery.startsWith(`${item.command} `),
      )
  }, [command, commands])

  const inputHint = useMemo(
    () => getCommandInputHint(command, [...commands, ...SESSION_SUBCOMMANDS]),
    [command, commands],
  )

  const suggestions = useMemo<TerminalSuggestion[]>(() => {
    const rawQuery = command.trimStart()
    const query = rawQuery.toLowerCase()
    const commandOrder = [
      '/add-resource',
      '/add-skill',
      '/compile',
      '/find',
      '/read',
      '/write',
      '/add-memory',
      '/session',
      '/ls',
      '/tree',
      '/stat',
      '/search',
      '/grep',
      '/glob',
      '/abstract',
      '/overview',
      '/status',
      '/health',
      '/task',
      '/wait',
    ]
    const commandMatches =
      query === '/'
        ? [...commands]
            .sort((a, b) => {
              const aIndex = commandOrder.indexOf(a.command)
              const bIndex = commandOrder.indexOf(b.command)
              return (
                (aIndex === -1 ? commandOrder.length : aIndex) -
                (bIndex === -1 ? commandOrder.length : bIndex)
              )
            })
            .map((item) => ({
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

    const sessionSubcommandMatches =
      activeCommand?.command === '/session'
        ? (() => {
            const rawBody = rawQuery.slice(activeCommand.command.length)
            const body = rawBody.trimStart()
            const [partial = ''] = body.split(/\s+/)
            const isChoosingSubcommand =
              body.length === 0 ||
              (!rawBody.endsWith(' ') && !body.slice(partial.length).trim())

            if (!isChoosingSubcommand) return []

            return SESSION_SUBCOMMANDS.filter((item) => {
              const description = t(
                `terminal.commandExamples.${item.examples[0]}.description`,
              )
              return (
                !partial ||
                item.key.toLowerCase().startsWith(partial.toLowerCase()) ||
                description.toLowerCase().includes(partial.toLowerCase())
              )
            }).map((item) => {
              return {
                ...activeCommand,
                command: item.key,
                description: t(
                  `terminal.commandExamples.${item.examples[0]}.description`,
                ),
                group: 'subcommand' as const,
                id: `subcommand:session:${item.key}`,
                insertText: item.insertText,
                key: `session:${item.key}`,
                usage: item.usage,
              }
            })
          })()
        : []

    const historyMatches = (query ? commandHistory : [])
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
    return [
      ...compileSuggestions(
        rawQuery,
        entries.filter((entry) => entry.isDir).map((entry) => entry.uri),
        compileSkills.data?.map((skill) => skill.uri) || [],
      ).map((insertText) => ({
        adminOnly: false,
        command: insertText,
        description: t('compile:terminalHint'),
        executable: false,
        group: 'core' as const,
        id: `compile:${insertText}`,
        insertText,
        key: 'compile',
        usage: insertText,
      })),
      ...commandMatches,
      ...resourceMatches,
      ...sessionSubcommandMatches,
      ...historyMatches,
    ].filter((item) => {
      if (seen.has(item.insertText)) return false
      seen.add(item.insertText)
      return true
    })
  }, [
    activeCommand,
    command,
    commandHistory,
    commands,
    resourceCandidates,
    t,
    entries,
    compileSkills.data,
  ])

  const helpCommand = useMemo(() => {
    if (!activeCommand) return undefined
    return activeCommand
  }, [activeCommand])

  const hoveredHelpCommand =
    suggestionsOpen && previewSuggestionIndex !== null
      ? commands.find(
          (item) =>
            item.command === suggestions[previewSuggestionIndex]?.command,
        )
      : undefined
  const displayHelpCommand = hoveredHelpCommand ?? helpCommand

  const selectedSessionSubcommand = useMemo(() => {
    if (
      helpCommand?.command !== '/session' ||
      displayHelpCommand?.command !== '/session'
    )
      return undefined
    const body = command
      .trimStart()
      .slice(helpCommand.command.length)
      .trimStart()
    const [subcommand = ''] = body.split(/\s+/)
    return SESSION_SUBCOMMANDS_BY_KEY.get(subcommand)
  }, [command, displayHelpCommand, helpCommand])

  const sessionSubcommandRows = useMemo(
    () =>
      SESSION_SUBCOMMANDS.map((item) => {
        const exampleKey = item.examples[0]
        return {
          ...item,
          description: t(`terminal.commandExamples.${exampleKey}.description`),
        }
      }),
    [t],
  )

  const showSessionSubcommandList =
    displayHelpCommand?.command === '/session' && !selectedSessionSubcommand

  const helpTitle = selectedSessionSubcommand
    ? `/session ${selectedSessionSubcommand.key}`
    : displayHelpCommand?.command
  const helpDescription = selectedSessionSubcommand
    ? t(
        `terminal.commandExamples.${selectedSessionSubcommand.examples[0]}.description`,
      )
    : displayHelpCommand?.description
  const helpUsage = selectedSessionSubcommand
    ? selectedSessionSubcommand.usage
    : displayHelpCommand?.usage

  const commandParameters = useMemo(
    () =>
      (
        selectedSessionSubcommand?.parameters ??
        (showSessionSubcommandList
          ? []
          : (displayHelpCommand?.parameters ?? []))
      ).map((key) => {
        const meta: {
          type: 'Text' | 'URI' | 'Number' | 'Boolean'
          required?: boolean
          defaultValue?: string
        } = PARAMETER_META[key] ?? { type: 'Text' }
        return {
          description: t(`terminal.commandParameters.${key}.description`),
          key,
          name: t(`terminal.commandParameters.${key}.name`),
          ...meta,
          ...(key === 'nodeLimit' &&
          ['/grep', '/glob'].includes(displayHelpCommand?.command ?? '')
            ? { defaultValue: '256' }
            : {}),
        }
      }),
    [
      displayHelpCommand,
      selectedSessionSubcommand,
      showSessionSubcommandList,
      t,
    ],
  )

  const commandExamples = useMemo(
    () =>
      (
        selectedSessionSubcommand?.examples ??
        (showSessionSubcommandList ? [] : (displayHelpCommand?.examples ?? []))
      ).map((key) => {
        return {
          code: t(`terminal.commandExamples.${key}.code`),
          description: t(`terminal.commandExamples.${key}.description`),
          key,
        }
      }),
    [
      displayHelpCommand,
      selectedSessionSubcommand,
      showSessionSubcommandList,
      t,
    ],
  )

  const canUseCurrentScope =
    displayHelpCommand?.command === '/find' ||
    displayHelpCommand?.command === '/search'

  const showCommandAssist =
    inputFocused &&
    (Boolean(displayHelpCommand) || (suggestionsOpen && suggestions.length > 0))

  useEffect(() => {
    setActiveSuggestionIndex(0)
    setPreviewSuggestionIndex(null)
  }, [suggestions.length])

  useEffect(() => {
    suggestionRefs.current = suggestionRefs.current.slice(0, suggestions.length)
  }, [suggestions.length])

  useEffect(() => {
    if (!suggestionsOpen) return
    suggestionRefs.current[activeSuggestionIndex]?.scrollIntoView({
      block: 'nearest',
    })
  }, [activeSuggestionIndex, suggestionsOpen])

  useEffect(() => {
    scrollRef.current?.scrollTo({
      behavior: 'smooth',
      top: scrollRef.current.scrollHeight,
    })
  }, [history.length, running])

  const append = useCallback((entry: Omit<TerminalEntry, 'id'>) => {
    const id = crypto.randomUUID()
    setHistory((prev) =>
      [
        ...prev,
        {
          ...entry,
          createdAt: Date.now(),
          id,
        },
      ].slice(-TERMINAL_ENTRY_HISTORY_LIMIT),
    )
    return id
  }, [])

  const updateEntry = useCallback(
    (id: string, changes: Partial<Omit<TerminalEntry, 'id'>>) => {
      setHistory((prev) =>
        prev.map((entry) =>
          entry.id === id ? { ...entry, ...changes } : entry,
        ),
      )
    },
    [],
  )

  const trackBackgroundTask = useCallback(
    (
      taskId: string,
      title: string,
      completedBody: string,
      notifyOnComplete = true,
      existingEntryId?: string,
      resource?: { fallbackUri?: string; source: string },
    ) => {
      const entryId =
        existingEntryId ??
        append({
          backgroundTaskCompletedBody: completedBody,
          backgroundTaskNotify: notifyOnComplete,
          backgroundTaskFallbackUri: resource?.fallbackUri,
          backgroundTaskSource: resource?.source,
          body: t('terminal.resourceProcessing', { taskId }),
          kind: 'info',
          resourceTaskId: taskId,
          title,
        })
      if (trackedResourceEntriesRef.current.has(entryId)) return
      trackedResourceEntriesRef.current.add(entryId)
      const controller = new AbortController()
      resourcePollsRef.current.add(controller)
      void (async () => {
        try {
          const task = await waitForResourceTask(
            taskId,
            async (id) =>
              getOvResult<ResourceTaskState>(
                getTaskByTaskId({
                  path: { task_id: id },
                  signal: controller.signal,
                }),
              ),
            { signal: controller.signal },
          )
          if (
            controller.signal.aborted ||
            identityRef.current !== identityScopeKey
          )
            return
          if (!task) {
            updateEntry(entryId, {
              body: t('terminal.resourceStillProcessing', { taskId }),
            })
          } else if (task.status !== 'completed') {
            updateEntry(entryId, {
              body: t('terminal.backgroundTaskFailed', {
                reason: task.error || task.status,
                taskId,
              }),
              kind: 'error',
            })
          } else {
            await invalidateAll()
            const resultUri = taskResultResourceUri(task, resource?.fallbackUri)
            const details = taskResultDetails(taskId, task, resultUri).map(
              ({ key, ...detail }) => ({
                ...detail,
                label: t(`terminal.taskDetails.${key}`),
              }),
            )
            updateEntry(entryId, {
              body: resource
                ? t('terminal.resourceCompleted', {
                    uri: resultUri || resource.source,
                  })
                : completedBody,
              details,
              kind: 'success',
              refs: resultUri ? [{ uri: resultUri }] : undefined,
            })
            if (notifyOnComplete) {
              toast.success(t('terminal.taskCompleted'), {
                description: t('terminal.taskCompletedHint'),
                duration: Infinity,
                action: {
                  label: t('terminal.viewResult'),
                  onClick: () => {
                    if (resultUri) {
                      void onOpenResource(resultUri)
                    } else {
                      document
                        .getElementById(`terminal-entry-${entryId}`)
                        ?.scrollIntoView({
                          behavior: 'smooth',
                          block: 'center',
                        })
                    }
                  },
                },
              })
            }
          }
        } catch (error) {
          if (
            !controller.signal.aborted &&
            identityRef.current === identityScopeKey
          )
            updateEntry(entryId, {
              body: t('terminal.backgroundTaskFailed', {
                reason: getErrorMessage(error),
                taskId,
              }),
              kind: 'error',
            })
        } finally {
          resourcePollsRef.current.delete(controller)
        }
      })()
    },
    [append, identityScopeKey, invalidateAll, onOpenResource, t, updateEntry],
  )

  useEffect(() => {
    if (previousIdentityScopeKey.current !== identityScopeKey) return
    for (const entry of history) {
      if (entry.kind !== 'info' || !entry.resourceTaskId) continue
      if (trackedResourceEntriesRef.current.has(entry.id)) continue
      trackBackgroundTask(
        entry.resourceTaskId,
        entry.title,
        entry.backgroundTaskCompletedBody ?? t('terminal.taskCompleted'),
        entry.backgroundTaskNotify ?? false,
        entry.id,
        entry.backgroundTaskSource
          ? {
              fallbackUri: entry.backgroundTaskFallbackUri,
              source: entry.backgroundTaskSource,
            }
          : undefined,
      )
    }
  }, [history, identityScopeKey, t, trackBackgroundTask])

  const clearHistory = useCallback(() => {
    setHistory([])
  }, [])

  const rememberCommand = useCallback((raw: string) => {
    const trimmed = raw.trim()
    if (!trimmed) return
    setCommandHistory((prev) => {
      const next = [
        trimmed,
        ...prev.filter((item) => item.toLowerCase() !== trimmed.toLowerCase()),
      ].slice(0, TERMINAL_COMMAND_HISTORY_LIMIT)
      return next
    })
  }, [])

  const compileSubmission = useRef<{ raw: string; key: string } | null>(null)
  const previousIdentityScopeKey = useRef(identityScopeKey)
  useEffect(() => {
    if (previousIdentityScopeKey.current !== identityScopeKey) return
    terminalPageCache = { identityScopeKey, history, commandHistory }
  }, [identityScopeKey, history, commandHistory])
  useEffect(() => {
    compileSubmission.current = null
    removeStoredValue(
      createIdentityStorageKey(
        TERMINAL_COMMAND_HISTORY_STORAGE_KEY,
        identityScopeKey,
      ),
    )
    removeStoredValue(
      createIdentityStorageKey(
        TERMINAL_ENTRY_HISTORY_STORAGE_KEY,
        identityScopeKey,
      ),
    )
    if (previousIdentityScopeKey.current !== identityScopeKey) {
      terminalPageCache = null
      trackedResourceEntriesRef.current.clear()
      setHistory([])
      setCommandHistory([])
      setCommand('')
      previousIdentityScopeKey.current = identityScopeKey
    }
  }, [identityScopeKey])
  const runCommand = useCallback(
    async (raw: string) => {
      const trimmed = raw.trim()
      if (!trimmed || running) return

      const isCompile = isCompileCommand(trimmed)
      const isSensitiveImport =
        /^(?:ov\s+)?\/?add-(?:memory|skill)(?:\s|$)/.test(trimmed)
      const isSensitiveResource =
        /^(?:ov\s+)?\/?(?:add-resource|write)(?:\s|$)/.test(trimmed)
      const safeHistory = isCompile
        ? compileHistory(trimmed)
        : isSensitiveImport || isSensitiveResource
          ? {
              title:
                trimmed.match(/add-(?:memory|skill|resource)|write/)?.[0] ??
                'command',
              remember: false,
            }
          : { title: trimmed, remember: true }
      append({ kind: 'command', title: trimmed })
      if (safeHistory.remember) rememberCommand(safeHistory.title)
      setCommand('')
      setSuggestionsOpen(false)
      setRunning(true)

      try {
        if (isSensitiveImport) {
          if (/^(?:ov\s+)?\/?add-memory(?:\s|$)/.test(trimmed)) {
            const { content, sessionId: targetSessionId } =
              parseAddMemory(trimmed)
            const submission = await submitMemoryCommand(
              content,
              targetSessionId,
              () =>
                mountedRef.current && identityRef.current === identityScopeKey,
              { createSession, addMessage, commitSession },
            )
            if (!submission) return
            const { id, result } = submission
            if (result.task_id) {
              trackBackgroundTask(
                result.task_id,
                '/add-memory',
                t('terminal.memoryCompleted', { id }),
                false,
              )
            } else {
              append({
                kind: 'success',
                title: '/add-memory',
                body: joinBodyLines([
                  t('terminal.addMemorySubmitted'),
                  id,
                  formatJson(result),
                ]),
                refs: [{ uri: `viking://~/sessions/${id}` }],
              })
            }
            return
          }
          const { source, targetUri } = parseAddSkill(trimmed)
          const result = await addNewSkill(
            source,
            targetUri as `viking://${string}`,
          )
          if (identityRef.current !== identityScopeKey) return
          if (result.task_id) {
            trackBackgroundTask(
              result.task_id,
              '/add-skill',
              t('terminal.skillCompleted'),
            )
          } else {
            append({
              kind: 'success',
              title: '/add-skill',
              body: t('terminal.addSkillSubmitted'),
            })
          }
          return
        }
        if (isCompile) {
          if (/^(?:ov\s+)?\/?compile$/.test(trimmed)) {
            append({
              kind: 'info',
              title: 'compile',
              body: t('compile:terminalHelp'),
              compileForm: true,
            })
            return
          }
          const recoveryKey = `compile-submission:${identityScopeKey}`
          const result = await runCompileSubmission(
            trimmed,
            compileSubmission,
            (status) =>
              t(`compile:statuses.${status}`, { defaultValue: status }),
            (key) => {
              if (identityRef.current !== identityScopeKey)
                throw new DOMException('Identity changed', 'AbortError')
              try {
                if (key) sessionStorage.setItem(recoveryKey, key)
                else sessionStorage.removeItem(recoveryKey)
              } catch {
                /* optional recovery */
              }
            },
            () => {
              try {
                return sessionStorage.getItem(recoveryKey)
              } catch {
                return null
              }
            },
            (stage) =>
              t(`compile:stages.${stage.replace(/^compile:\s*/, '')}`, {
                defaultValue: stage,
              }),
          )
          if (identityRef.current !== identityScopeKey) return
          append({
            kind: 'success',
            title: 'compile',
            body: result.body,
            compileTaskId: result.taskId,
          })
          return
        }
        const normalized = trimmed.replace(/^ov\s+/, '')
        const commandInput = normalized.startsWith('/')
          ? normalized
          : `/${normalized}`
        const [name = '', ...args] = commandInput.split(/\s+/)
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
            if (body) throw new Error(t('terminal.waitUsage'))
            const result = await getOvResult<unknown>(postSystemWait())
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
            const {
              query,
              scopeUri,
              limit,
              scoreThreshold,
              sessionId: searchSessionId,
            } = parseScopedSearchInput(
              body,
              currentUri,
              t('terminal.searchUsage', { name }),
            )
            if (!query) throw new Error(t('terminal.searchUsage', { name }))
            const result = await (name === '/find' ? fetchFind : fetchSearch)(
              query,
              {
                limit: limit ?? 10,
                targetUri: scopeUri,
                scoreThreshold,
                ...(name === '/search' ? { sessionId: searchSessionId } : {}),
              },
            )
            const scopeText = scopeUri ?? t('terminal.globalScope')
            const summary = t('terminal.hits', {
              memories: result.memories.length,
              resources: result.resources.length,
              skills: result.skills.length,
            })
            append({
              body: joinBodyLines([
                t('terminal.searchScopeLine', { scope: scopeText }),
                result.query_plan?.reasoning || summary,
                result.query_plan?.reasoning ? summary : undefined,
              ]),
              kind: 'success',
              refs: searchResultToRefs(result, groupLabels),
              title: `${name} ${body}`,
            })
            return
          }
          case '/grep':
          case '/glob': {
            const tokens = tokenize(trimmed)
            const pattern = tokens[1]
            if (!pattern || pattern.startsWith('-'))
              throw new Error(t('terminal.patternUsage', { name }))
            let uri = ROOT_URI
            let limit = 256
            let caseInsensitive = false
            for (let i = 2; i < tokens.length; i++) {
              const flag = tokens[i]
              if (flag === '-i' && name === '/grep') {
                caseInsensitive = true
                continue
              }
              const value = tokens[++i]
              if (!value) throw new Error(t('terminal.patternUsage', { name }))
              if (flag === '--uri' || flag === '-u') uri = value
              else if (['--node-limit', '-n', '--limit'].includes(flag)) {
                const parsed = Number(value)
                if (!Number.isInteger(parsed) || parsed < 0)
                  throw new Error(t('terminal.patternUsage', { name }))
                limit = parsed
              } else throw new Error(t('terminal.patternUsage', { name }))
            }
            const result =
              name === '/grep'
                ? await fetchGrep(pattern, { uri, limit, caseInsensitive })
                : await fetchGlob(pattern, { uri, limit })
            append({
              body: t('terminal.patternHits', {
                count: result.resources.length,
                uri,
              }),
              kind: 'success',
              refs: searchResultToRefs(result, groupLabels),
              title: `${name} ${pattern}`,
            })
            return
          }
          case '/write': {
            const request = parseWrite(trimmed)
            const result = await getOvResult<unknown>(
              postContentWrite({ body: request }),
            )
            if (identityRef.current !== identityScopeKey) return
            append({
              body: formatJson(result),
              kind: 'success',
              refs: [{ uri: request.uri }],
              title: `/write ${request.uri}`,
            })
            await invalidateAll()
            await onOpenResource(request.uri)
            return
          }
          case '/add-resource': {
            const request = parseAddResource(trimmed)
            const result = await getOvResult<ResourceImportResult>(
              postResources({ body: request }),
            )
            if (identityRef.current !== identityScopeKey) return
            if (result.status === 'error') {
              throw new Error(
                result.errors?.join('; ') ||
                  result.message ||
                  t('terminal.resourceFailed'),
              )
            }
            const taskId = result.task_id?.trim()
            const rootUri = result.root_uri || request.to
            if (!taskId) {
              await invalidateAll()
              if (rootUri) await onOpenResource(rootUri)
              append({
                body: t('terminal.resourceCompleted', {
                  uri: rootUri || request.path,
                }),
                kind: 'success',
                title: '/add-resource',
                refs: rootUri ? [{ uri: rootUri }] : undefined,
              })
              return
            }
            trackBackgroundTask(
              taskId,
              '/add-resource',
              t('terminal.resourceCompleted', {
                uri: rootUri || request.path,
              }),
              true,
              undefined,
              { fallbackUri: rootUri, source: request.path },
            )
            return
          }
          case '/session': {
            const { flags, positional } = parseOptions(body)
            const subcommand = positional.shift() ?? 'current'
            const requireCurrentSession = () => {
              if (!sessionId) throw new Error(t('terminal.sessionMissing'))
              return sessionId
            }
            const resolveSessionId = () =>
              positional.shift() ?? requireCurrentSession()
            const sessionRef = (id: string): ResourceRef => ({
              label: id,
              meta: 'session',
              uri: `viking://~/sessions/${id}`,
            })

            switch (subcommand) {
              case 'current': {
                const id = requireCurrentSession()
                append({
                  body: t('terminal.sessionCurrentBody', { id }),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: '/session current',
                })
                return
              }
              case 'list': {
                const sessions = await fetchSessions()
                append({
                  body: t('terminal.sessionListBody', {
                    count: sessions.length,
                  }),
                  kind: 'success',
                  refs: sessions.map(sessionToRef),
                  title: '/session list',
                })
                return
              }
              case 'create': {
                const requestedId = positional.shift()
                const result = await createSession(requestedId)
                registerFilesystemAgentSessionId(
                  result.session_id,
                  identityScopeKey,
                )
                onSessionChange(result.session_id)
                append({
                  body: joinBodyLines([
                    t('terminal.sessionCreatedBody', {
                      id: result.session_id,
                    }),
                    formatJson(result),
                  ]),
                  kind: 'success',
                  refs: [sessionRef(result.session_id)],
                  title: '/session create',
                })
                return
              }
              case 'switch': {
                const id = positional.shift()
                if (!id) throw new Error(t('terminal.sessionUsage'))
                registerFilesystemAgentSessionId(id, identityScopeKey)
                onSessionChange(id)
                append({
                  body: t('terminal.sessionSwitchedBody', { id }),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: `/session switch ${id}`,
                })
                return
              }
              case 'get': {
                const id = resolveSessionId()
                const result = await fetchSession(id)
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: `/session get ${id}`,
                })
                return
              }
              case 'context': {
                const id = resolveSessionId()
                const result = await fetchSessionContext(
                  id,
                  getNumberFlag(flags, 'token-budget'),
                )
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: `/session context ${id}`,
                })
                return
              }
              case 'messages': {
                const id = resolveSessionId()
                const result = await fetchSessionMessages(id)
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: `/session messages ${id}`,
                })
                return
              }
              case 'archive': {
                const id =
                  positional.length > 1
                    ? positional.shift()!
                    : requireCurrentSession()
                const archiveId = positional.shift()
                if (!archiveId) throw new Error(t('terminal.sessionUsage'))
                const result = await fetchSessionArchive(id, archiveId)
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [
                    {
                      label: archiveId,
                      meta: 'archive',
                      uri: `viking://~/sessions/${id}/history/${archiveId}`,
                    },
                  ],
                  title: `/session archive ${id} ${archiveId}`,
                })
                return
              }
              case 'commit': {
                const id = resolveSessionId()
                const result = await commitSession(
                  id,
                  getNumberFlag(flags, 'keep-recent'),
                )
                if (result.task_id) {
                  trackBackgroundTask(
                    result.task_id,
                    `/session commit ${id}`,
                    t('terminal.memoryCompleted', { id }),
                    false,
                  )
                  return
                }
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: `/session commit ${id}`,
                })
                return
              }
              case 'message': {
                const roleIndex = positional.findIndex(
                  (item) => item === 'user' || item === 'assistant',
                )
                if (roleIndex < 0) throw new Error(t('terminal.sessionUsage'))
                const id =
                  roleIndex > 0
                    ? positional.slice(0, roleIndex).join(' ')
                    : requireCurrentSession()
                const role = positional[roleIndex] as 'user' | 'assistant'
                const content = positional
                  .slice(roleIndex + 1)
                  .join(' ')
                  .trim()
                if (!content) throw new Error(t('terminal.sessionUsage'))
                const result = await addMessage(id, role, content)
                append({
                  body: joinBodyLines([
                    t('terminal.sessionMessageAddedBody', { id }),
                    formatJson(result),
                  ]),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: `/session message ${id}`,
                })
                return
              }
              case 'tool-results': {
                const id = resolveSessionId()
                const result = await fetchSessionToolResults(id, {
                  limit: getNumberFlag(flags, 'limit'),
                  toolName: getLastFlag(flags, 'tool-name'),
                })
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: `/session tool-results ${id}`,
                })
                return
              }
              case 'tool-result': {
                const id =
                  positional.length > 1
                    ? positional.shift()!
                    : requireCurrentSession()
                const toolResultId = positional.shift()
                if (!toolResultId) throw new Error(t('terminal.sessionUsage'))
                const result = await fetchSessionToolResult(id, toolResultId, {
                  includeMetadata: !getBooleanFlag(flags, 'no-metadata'),
                  limit: getNumberFlag(flags, 'limit'),
                  offset: getNumberFlag(flags, 'offset'),
                })
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [
                    {
                      label: toolResultId,
                      meta: 'tool result',
                      uri: `viking://~/sessions/${id}/tool-results/${toolResultId}`,
                    },
                  ],
                  title: `/session tool-result ${id} ${toolResultId}`,
                })
                return
              }
              case 'tool-search': {
                const id =
                  positional.length > 2
                    ? positional.shift()!
                    : requireCurrentSession()
                const toolResultId = positional.shift()
                const query = positional.join(' ').trim()
                if (!toolResultId || !query) {
                  throw new Error(t('terminal.sessionUsage'))
                }
                const result = await searchSessionToolResult(
                  id,
                  toolResultId,
                  query,
                  {
                    contextChars: getNumberFlag(flags, 'context-chars'),
                    limit: getNumberFlag(flags, 'limit'),
                  },
                )
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [
                    {
                      label: toolResultId,
                      meta: 'tool search',
                      uri: `viking://~/sessions/${id}/tool-results/${toolResultId}`,
                    },
                  ],
                  title: `/session tool-search ${id} ${toolResultId}`,
                })
                return
              }
              case 'delete': {
                const id = positional.shift()
                if (!id) throw new Error(t('terminal.sessionDeleteUsage'))
                const result = await deleteSession(id)
                append({
                  body: joinBodyLines([
                    t('terminal.sessionDeletedBody', { id }),
                    formatJson(result),
                  ]),
                  kind: 'success',
                  title: `/session delete ${id}`,
                })
                return
              }
              default:
                throw new Error(t('terminal.sessionUsage'))
            }
          }
          default:
            throw new Error(t('terminal.unknownCommand'))
        }
      } catch (error) {
        if (identityRef.current !== identityScopeKey) return
        append({
          body:
            error instanceof CompileCommandError
              ? t(`compile:commandErrors.${error.code}`)
              : error instanceof TerminalActionError
                ? t(`terminal.${error.code}`)
                : getErrorMessage(error),
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
      identityScopeKey,
      invalidateAll,
      onOpenResource,
      onSessionChange,
      running,
      rememberCommand,
      sessionId,
      t,
      updateEntry,
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

  const insertCurrentScope = useCallback(() => {
    const trimmed = command.trimEnd()
    const hasScope = trimmed.match(/(?:^|\s)(?:--uri|--scope|-u)(?:=|\s)/)
    const commandName = helpCommand?.command ?? trimmed.split(/\s+/)[0]
    const body = trimmed.slice(commandName.length).trim()
    const next = hasScope
      ? command
      : body
        ? `${trimmed} --uri ${currentUri}`
        : `${commandName}  --uri ${currentUri}`
    const cursorPosition =
      hasScope || body ? next.length : commandName.length + 1
    setCommand(next)
    setSuggestionsOpen(false)
    window.requestAnimationFrame(() => {
      const input = inputRef.current
      input?.focus()
      input?.setSelectionRange(cursorPosition, cursorPosition)
    })
  }, [command, currentUri, helpCommand])

  const quickStartExamples = useMemo<TerminalQuickStartExample[]>(
    () => [
      {
        action: () => acceptSuggestion({ insertText: '/add-resource ' }),
        code: t('terminal.quickStart.addResource.code'),
        command: t('terminal.quickStart.addResource.command'),
        key: 'add-resource',
      },
      {
        action: () => acceptSuggestion({ insertText: '/add-skill ' }),
        code: t('terminal.quickStart.addSkill.code'),
        command: t('terminal.quickStart.addSkill.command'),
        key: 'add-skill',
      },
      {
        action: () => acceptSuggestion({ insertText: '/add-memory ' }),
        code: t('terminal.quickStart.addMemory.code'),
        command: t('terminal.quickStart.addMemory.command'),
        key: 'add-memory',
      },
      {
        code: t('terminal.quickStart.find.code'),
        command: t('terminal.quickStart.find.command'),
        key: 'find',
        action: () =>
          acceptSuggestion({
            insertText: t('terminal.quickStart.find.command'),
          }),
      },
    ],
    [acceptSuggestion, t],
  )

  return (
    <>
      {toolbarContainer
        ? createPortal(
            <>
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
            </>,
            toolbarContainer,
          )
        : null}
      <div className="flex min-h-0 flex-1 flex-col">
        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
        >
          <div className="space-y-3">
            {history.length === 0 && !running ? (
              <TerminalQuickStart
                examples={quickStartExamples}
                title={t('terminal.quickStart.title')}
                heading={t('terminal.quickStart.heading')}
                description={t('terminal.quickStart.description')}
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
        <div className="border-t bg-background p-3">
          <form
            className="relative rounded-xl border bg-background px-3 py-2 focus-within:border-ring"
            onSubmit={(event) => {
              event.preventDefault()
              void runCommand(command)
            }}
          >
            <span
              className="block truncate font-mono text-[10px] text-muted-foreground"
              title={t('terminal.scopeLabel', { uri: currentUri })}
            >
              {currentUri}
            </span>
            <div className="flex min-w-0 items-center gap-2">
              <div className="relative min-w-0 flex-1">
                {inputHint ? (
                  <div
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-0 flex h-8 min-w-0 items-center overflow-hidden whitespace-pre font-mono text-xs"
                  >
                    <span className="text-transparent">{command}</span>
                    <span className="truncate text-muted-foreground/70">
                      {inputHint}
                    </span>
                  </div>
                ) : null}
                <input
                  ref={inputRef}
                  value={command}
                  onBlur={() => {
                    window.setTimeout(() => {
                      setInputFocused(false)
                      setSuggestionsOpen(false)
                    }, 120)
                  }}
                  onChange={(event) => {
                    setCommand(event.target.value)
                    setSuggestionsOpen(true)
                    setPreviewSuggestionIndex(null)
                  }}
                  onFocus={() => {
                    setInputFocused(true)
                    setSuggestionsOpen(true)
                  }}
                  onKeyDown={(event) => {
                    if (!suggestionsOpen || suggestions.length === 0) return
                    if (event.key === 'ArrowDown') {
                      event.preventDefault()
                      setActiveSuggestionIndex((current) =>
                        Math.min(current + 1, suggestions.length - 1),
                      )
                      setPreviewSuggestionIndex((current) =>
                        Math.min(
                          (current ?? activeSuggestionIndex) + 1,
                          suggestions.length - 1,
                        ),
                      )
                      return
                    }
                    if (event.key === 'ArrowUp') {
                      event.preventDefault()
                      setActiveSuggestionIndex((current) =>
                        Math.max(current - 1, 0),
                      )
                      setPreviewSuggestionIndex((current) =>
                        Math.max((current ?? activeSuggestionIndex) - 1, 0),
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
                      acceptSuggestion(suggestions[activeSuggestionIndex])
                      return
                    }
                    if (event.key === 'Escape') {
                      setSuggestionsOpen(false)
                    }
                  }}
                  placeholder={t('terminal.placeholder')}
                  className="relative h-8 w-full min-w-0 bg-transparent font-mono text-xs outline-none placeholder:text-muted-foreground/80"
                />
              </div>
              {showCommandAssist ? (
                <div className="pointer-events-none absolute bottom-[calc(100%+0.5rem)] left-0 right-0 z-30">
                  {displayHelpCommand ? (
                    <div
                      className={cn(
                        'pointer-events-auto max-h-[min(420px,52vh)] overflow-y-auto rounded-xl border bg-popover p-4 shadow-lg',
                        suggestionsOpen && suggestions.length > 0
                          ? 'absolute bottom-0 right-[calc(100%+2px)] hidden w-[400px] lg:block'
                          : 'w-full',
                      )}
                    >
                      <div className="flex min-w-0 items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate font-mono text-base font-semibold text-foreground">
                            {helpTitle}
                          </div>
                          <div className="mt-1 text-xs leading-5 text-muted-foreground">
                            {helpDescription}
                          </div>
                        </div>
                      </div>
                      <div className="mt-4 space-y-4">
                        {showSessionSubcommandList ? (
                          <div className="min-w-0">
                            <div className="mb-1.5 text-[11px] font-medium text-muted-foreground">
                              {t('terminal.helpSubcommands')}
                            </div>
                            <div className="overflow-hidden rounded-md border">
                              <table className="w-full table-fixed border-collapse text-xs">
                                <tbody className="divide-y">
                                  {sessionSubcommandRows.map((item) => (
                                    <tr key={item.key}>
                                      <td className="w-40 align-top bg-muted/30 px-2 py-1.5 font-mono text-foreground">
                                        <button
                                          type="button"
                                          className="block max-w-full truncate text-left text-primary hover:underline"
                                          title={item.usage}
                                          onClick={() =>
                                            acceptSuggestion({
                                              insertText: item.insertText,
                                            })
                                          }
                                          onMouseDown={(event) =>
                                            event.preventDefault()
                                          }
                                        >
                                          {item.key}
                                        </button>
                                      </td>
                                      <td className="align-top px-2 py-1.5 leading-4 text-muted-foreground">
                                        {item.description}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        ) : (
                          <>
                            {commandExamples.length > 0 ? (
                              <div className="min-w-0">
                                <div className="mb-2 text-xs text-muted-foreground">
                                  {t('terminal.quickStart.title')}
                                </div>
                                <div className="space-y-1">
                                  {commandExamples.map((example, index) => (
                                    <button
                                      key={example.key}
                                      type="button"
                                      className="flex w-full items-start gap-2 rounded-lg bg-muted/70 px-3 py-2 text-left font-mono text-xs leading-5 text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                      title={example.description}
                                      onMouseDown={(event) =>
                                        event.preventDefault()
                                      }
                                      onClick={() =>
                                        acceptSuggestion({
                                          insertText: example.code,
                                        })
                                      }
                                    >
                                      <span className="w-4 shrink-0 text-muted-foreground">
                                        {index + 1}
                                      </span>
                                      <span className="min-w-0 break-all">
                                        {example.code}
                                      </span>
                                    </button>
                                  ))}
                                </div>
                              </div>
                            ) : null}
                            <div className="min-w-0">
                              <div className="mb-1.5 text-sm font-semibold text-foreground">
                                {t('terminal.helpParameters')}
                              </div>
                              <div className="mb-1 break-all rounded-md border bg-muted/70 px-3 py-2 font-mono text-xs leading-5 text-foreground">
                                {helpUsage}
                              </div>
                              {commandParameters.length > 0 ? (
                                <div className="overflow-hidden rounded-md border">
                                  <table className="w-full table-fixed border-collapse text-[11px]">
                                    <thead className="border-b bg-muted/40 text-muted-foreground">
                                      <tr>
                                        <th className="w-[32%] px-2 py-1.5 text-left font-medium">
                                          {t('terminal.parameterName')}
                                        </th>
                                        <th className="w-[12%] px-1 py-1.5 text-center font-medium">
                                          {t('terminal.parameterRequired')}
                                        </th>
                                        <th className="w-[41%] px-2 py-1.5 text-left font-medium">
                                          {t('terminal.parameterDescription')}
                                        </th>
                                        <th className="w-[15%] px-1 py-1.5 text-center font-medium">
                                          {t('terminal.parameterDefault')}
                                        </th>
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y">
                                      {commandParameters.map((parameter) => (
                                        <tr key={parameter.key}>
                                          <td className="align-top px-2 py-1.5 text-foreground">
                                            <span className="block break-all font-mono leading-4">
                                              {parameter.name}
                                            </span>
                                            <span className="mt-1 inline-block rounded bg-muted px-1 py-0.5 text-[9px] leading-none text-muted-foreground">
                                              {t(
                                                `terminal.parameterTypes.${parameter.type}`,
                                              )}
                                            </span>
                                          </td>
                                          <td className="align-top px-1 py-1.5 text-center text-muted-foreground">
                                            {parameter.required
                                              ? t('terminal.yes')
                                              : t('terminal.no')}
                                          </td>
                                          <td className="align-top px-2 py-1.5 text-muted-foreground">
                                            <div className="flex min-w-0 items-start justify-between gap-2">
                                              <span className="min-w-0 break-words leading-4">
                                                {parameter.description}
                                              </span>
                                              {canUseCurrentScope &&
                                              parameter.key === 'scope' ? (
                                                <Button
                                                  type="button"
                                                  variant="outline"
                                                  size="sm"
                                                  className="h-6 shrink-0 px-2 text-xs"
                                                  onClick={insertCurrentScope}
                                                  onMouseDown={(event) =>
                                                    event.preventDefault()
                                                  }
                                                >
                                                  {t(
                                                    'terminal.currentScopeAction',
                                                  )}
                                                </Button>
                                              ) : null}
                                            </div>
                                          </td>
                                          <td className="break-all px-1 py-1.5 text-center font-mono text-muted-foreground">
                                            {parameter.defaultValue ?? '—'}
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              ) : (
                                <div className="rounded-md border border-dashed bg-muted/40 px-3 py-2 text-center text-xs text-muted-foreground">
                                  {t('terminal.noParameters')}
                                </div>
                              )}
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  ) : null}
                  {suggestionsOpen && suggestions.length > 0 ? (
                    <div className="pointer-events-auto max-h-[min(360px,45vh)] overflow-y-auto rounded-xl border bg-popover px-2 py-3 shadow-lg">
                      {suggestions.map((suggestion, index) => (
                        <div key={suggestion.id}>
                          {index === 0 ||
                          suggestions[index - 1]?.group !== suggestion.group ? (
                            <div className="px-2.5 pb-1 pt-2 text-[10px] text-muted-foreground">
                              {t(`terminal.paletteGroups.${suggestion.group}`)}
                            </div>
                          ) : null}
                          <button
                            ref={(node) => {
                              suggestionRefs.current[index] = node
                            }}
                            type="button"
                            className={cn(
                              'min-h-8 w-full min-w-0 rounded-md px-2.5 py-1 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                              index === activeSuggestionIndex
                                ? 'bg-muted text-foreground'
                                : 'hover:bg-muted/60',
                            )}
                            onClick={() => acceptSuggestion(suggestion)}
                            onMouseDown={(event) => event.preventDefault()}
                            onMouseEnter={() => {
                              setActiveSuggestionIndex(index)
                              setPreviewSuggestionIndex(index)
                            }}
                          >
                            {suggestion.group === 'history' ||
                            suggestion.group === 'resource' ||
                            suggestion.group === 'subcommand' ? (
                              <span className="flex min-w-0 items-center gap-3">
                                <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium text-foreground">
                                  {suggestion.command}
                                </span>
                                <span className="shrink-0 text-xs leading-4 text-muted-foreground">
                                  {suggestion.description}
                                </span>
                              </span>
                            ) : (
                              <span className="grid min-w-0 grid-cols-[minmax(5.25rem,7.5rem)_minmax(0,1fr)] items-center gap-2.5">
                                <span className="min-w-0 truncate font-mono text-xs font-medium text-foreground">
                                  {suggestion.command}
                                </span>
                                <span className="min-w-0 text-xs leading-4 text-muted-foreground">
                                  {suggestion.description}
                                </span>
                              </span>
                            )}
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
              <Button
                type="submit"
                size="icon-sm"
                disabled={running || !command.trim()}
              >
                <SendIcon className="size-4" />
              </Button>
            </div>
          </form>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {quickStartExamples.map((example) => (
              <button
                key={example.key}
                type="button"
                className="rounded-md border bg-muted/30 px-2 py-1 font-mono text-[11px] text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={example.action}
              >
                {`/${example.key}`}
              </button>
            ))}
          </div>
          <div className="mt-1.5 text-[10px] text-muted-foreground">
            {t('terminal.inputHint')}
          </div>
        </div>
      </div>
      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="gap-4 sm:max-w-2xl">
          <DialogHeader className="pr-10">
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
  description,
  examples,
  heading,
  title,
}: {
  description: string
  examples: TerminalQuickStartExample[]
  heading: string
  title: string
}) {
  return (
    <section className="py-10">
      <h2 className="font-mono text-xl font-normal tracking-tight text-foreground">
        {heading}
        <span
          aria-hidden="true"
          className="ml-1 inline-block h-4 w-1 bg-muted-foreground/30"
        />
      </h2>
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      <div className="mb-2 mt-8 text-xs text-muted-foreground">{title}</div>
      <div className="space-y-2">
        {examples.map((example) => {
          const content = (
            <>
              <span className="flex size-4 shrink-0 items-center justify-center text-muted-foreground">
                <ArrowRightIcon className="size-3" />
              </span>
              <span className="w-[13ch] shrink-0 truncate font-mono text-xs text-foreground">
                {example.key}
              </span>
              <span
                className="min-w-0 flex-1 truncate text-xs text-muted-foreground"
                title={example.code}
              >
                {example.code}
              </span>
            </>
          )

          if (!example.action) {
            return (
              <div
                key={example.key}
                className="flex h-[43px] min-w-0 items-center gap-3 rounded-2xl bg-muted/40 px-4"
              >
                {content}
              </div>
            )
          }

          return (
            <button
              key={example.key}
              type="button"
              className="flex h-[43px] w-full min-w-0 items-center gap-3 rounded-2xl bg-muted/40 px-4 text-left transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
  const { t, i18n: translation } = useTranslation('filesystem')
  if (entry.kind === 'command') {
    return (
      <div id={`terminal-entry-${entry.id}`} className="flex justify-end">
        <div className="flex max-w-[90%] items-end gap-2 rounded-xl bg-muted/60 px-3 py-2.5 font-mono text-xs leading-5">
          <span className="min-w-0 whitespace-pre-wrap break-all">
            {entry.title}
          </span>
          {entry.createdAt && (
            <time className="shrink-0 text-[11px] text-muted-foreground">
              {new Date(entry.createdAt).toLocaleTimeString(
                translation.language,
                {
                  hour: '2-digit',
                  minute: '2-digit',
                },
              )}
            </time>
          )}
        </div>
      </div>
    )
  }
  const Icon =
    entry.kind === 'info' && entry.resourceTaskId
      ? Loader2Icon
      : entry.kind === 'success'
        ? CheckCircle2Icon
        : entry.kind === 'error'
          ? XCircleIcon
          : SparklesIcon

  return (
    <section
      id={`terminal-entry-${entry.id}`}
      className={cn(
        'rounded-lg border bg-background px-3 py-3 text-sm',
        entry.kind === 'error' && 'border-destructive/30 bg-destructive/5',
      )}
    >
      <div className="mb-2 flex items-center gap-2">
        <Icon
          className={cn(
            'size-3.5 shrink-0 text-muted-foreground',
            entry.kind === 'info' && entry.resourceTaskId && 'animate-spin',
            entry.kind === 'success' && 'text-primary',
            entry.compileTaskId && 'text-emerald-600',
            entry.kind === 'error' && 'text-destructive',
          )}
        />
        <span className="min-w-0 truncate text-xs font-semibold">
          {entry.title}
        </span>
        {entry.compileTaskId && (
          <Link
            to="/compile/tasks/$taskId"
            params={{ taskId: entry.compileTaskId }}
            className="ml-auto shrink-0 text-xs text-primary hover:underline"
          >
            {t('compile:viewTask')}
          </Link>
        )}
        {entry.resourceTaskId && (
          <Link
            to="/tasks"
            className="ml-auto shrink-0 text-xs text-primary hover:underline"
          >
            {t('terminal.viewResourceTask')}
          </Link>
        )}
      </div>
      {entry.compileForm && (
        <Link to="/compile/new" className="text-xs text-primary underline">
          {t('compile:openForm')}
        </Link>
      )}
      {entry.compileTaskId ? (
        <CompileTaskResult
          taskId={entry.compileTaskId}
          fallback={entry.body}
          onOpenResource={onOpenResource}
        />
      ) : entry.body ? (
        <pre className="whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 text-xs leading-5 text-muted-foreground">
          {entry.body}
        </pre>
      ) : null}
      {entry.details?.length ? (
        <dl className="mt-2 space-y-2">
          {entry.details.map((detail) => (
            <div
              key={detail.label}
              className="flex min-w-0 gap-2 rounded-[10px] bg-muted/40 px-3 py-2.5 font-mono text-xs"
            >
              <dt className="shrink-0 text-muted-foreground">{detail.label}</dt>
              <dd className="min-w-0 break-all">
                {detail.uri ? (
                  <button
                    type="button"
                    className="text-left text-primary hover:underline"
                    onClick={() => void onOpenResource(detail.uri!)}
                  >
                    {detail.value}
                  </button>
                ) : (
                  detail.value
                )}
              </dd>
            </div>
          ))}
        </dl>
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

export function CompileTaskResult({
  taskId,
  fallback,
  onOpenResource,
}: {
  taskId: string
  fallback?: string
  onOpenResource: ResourceOpenHandler
}) {
  const { t } = useTranslation('compile')
  const { identityScopeKey } = useAppConnection()
  const { invalidateAll, invalidatePreview } = useInvalidateVikingFs()
  const refreshedTasksRef = useRef(new Set<string>())
  const query = useQuery({
    queryKey: ['compile-task', identityScopeKey, taskId],
    queryFn: ({ signal }) => fetchCompileTask(taskId, signal),
    refetchInterval: (result) =>
      !result.state.data || activeCompileTask(result.state.data.status)
        ? 5000
        : false,
  })
  const task = query.data
  useEffect(() => {
    const key = `${identityScopeKey}:${taskId}`
    if (task?.status !== 'completed' || refreshedTasksRef.current.has(key))
      return
    refreshedTasksRef.current.add(key)
    void invalidateAll()
    void invalidatePreview()
  }, [identityScopeKey, invalidateAll, invalidatePreview, task?.status, taskId])
  const rows = task
    ? [
        { label: t('terminalResult.taskId'), value: task.task_id },
        {
          label: t('terminalResult.taskType'),
          value: task.task_type || 'compile',
        },
        {
          label: t('terminalResult.status'),
          value: t(`statuses.${task.status}`, {
            defaultValue: task.status || 'unknown',
          }),
        },
        task.created_at && {
          label: t('terminalResult.createdAt'),
          value: String(task.created_at),
        },
        task.updated_at && {
          label: t('terminalResult.updatedAt'),
          value: String(task.updated_at),
        },
        task.resource_id && {
          label: t('terminalResult.resourceId'),
          value: task.resource_id,
        },
        task.meta?.request?.to && {
          label: t('terminalResult.outputUri'),
          value: task.meta.request.to,
          uri: task.meta.request.to,
        },
        ...(task.meta?.request?.from ?? []).map((uri, index) => ({
          label: t('terminalResult.inputUri', { index: index + 1 }),
          value: uri,
          uri,
        })),
        task.meta?.request && {
          label: t('terminalResult.request'),
          value: JSON.stringify(task.meta.request),
        },
        task.stage && {
          label: t('terminalResult.stage'),
          value: t(`stages.${task.stage.replace(/^compile:\s*/, '')}`, {
            defaultValue: task.stage,
          }),
        },
        task.created_at_iso && {
          label: t('terminalResult.createdAtIso'),
          value: task.created_at_iso,
        },
        task.updated_at_iso && {
          label: t('terminalResult.updatedAtIso'),
          value: task.updated_at_iso,
        },
        task.error && { label: t('terminalResult.error'), value: task.error },
      ].filter((row): row is { label: string; value: string; uri?: string } =>
        Boolean(row),
      )
    : []

  return (
    <>
      {query.isError && (
        <p className="mb-2 text-xs text-destructive">{t('stale')}</p>
      )}
      {task ? (
        <dl className="space-y-2 font-mono text-xs leading-5">
          {rows.map((row) => (
            <div
              key={row.label}
              className="min-w-0 rounded-lg bg-muted/40 px-3 py-2.5"
            >
              <dt className="inline">{row.label}: </dt>
              <dd className="inline break-all">
                {row.uri ? (
                  <button
                    type="button"
                    className="text-left text-primary underline-offset-2 hover:underline"
                    onClick={() => void onOpenResource(row.uri!)}
                  >
                    {row.value}
                  </button>
                ) : (
                  row.value
                )}
              </dd>
            </div>
          ))}
        </dl>
      ) : fallback ? (
        <pre className="whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 text-xs leading-5 text-muted-foreground">
          {fallback}
        </pre>
      ) : null}
    </>
  )
}
