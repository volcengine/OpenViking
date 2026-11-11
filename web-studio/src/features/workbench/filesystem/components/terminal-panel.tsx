import { Tui } from '@openviking/react/components/tui'
import type { TuiRecord } from '@openviking/react/components/tui'
import '@openviking/react/components/tui/style.css'
import { createRandomUuid } from '#/shared/lib/browser-crypto'
import {
  active as activeCompileTask,
  fetchCompileTask,
} from '#/features/workbench/compile/lib/api'
import {
  CompileCommandError,
  compileHistory,
  isCompileCommand,
  tokenize,
} from '#/features/workbench/compile/lib/commands'
import { runCompileSubmission } from '#/features/workbench/compile/lib/terminal'
import { useInvalidateVikingFs } from '#/features/workbench/resources/hooks/viking-fm'
import { addNewSkill } from '#/features/workbench/skills/lib/add-skill'
import i18n from '#/i18n/core'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  CheckCircle2Icon,
  HistoryIcon,
  Loader2Icon,
  SparklesIcon,
  TrashIcon,
  XCircleIcon,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import {
  fetchDirectoryLevelContent,
  fetchFileContent,
  fetchFind,
  fetchFsList,
  fetchFsStat,
  fetchFsTree,
  fetchSearch,
} from '#/features/workbench/resources/lib/api'
import { normalizeDirUri } from '#/shared/lib/viking-fs'
import { VIKING_URI_RE } from '#/shared/lib/viking-uri'
import type { VikingFsEntry } from '#/shared/types/viking-fs'
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
} from '#/features/workbench/sessions/lib/api'
import {
  deleteFs,
  getHealth,
  getOvResult,
  getSystemStatus,
  getTaskByTaskId,
  ovClient,
  postContentWrite,
  postFsMkdir,
  postFsMv,
  postResources,
  postSystemWait,
} from '#/shared/api/ov-client/index'
import { fetchGlob, fetchGrep } from '#/shared/api/retrieval'
import { cn } from '#/shared/lib/utils'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import type { SessionListItem } from '@ov-server/api/v1/sessions'

import { ResourceRefList } from '#/features/workbench/filesystem/components/resource-ref-list'
import {
  ROOT_URI,
  TERMINAL_COMMANDS,
} from '#/features/workbench/filesystem/lib/constants'
import type { ResourceTaskState } from '#/features/workbench/filesystem/lib/terminal-actions'
import {
  parseAddMemory,
  parseAddResource,
  parseAddSkill,
  parseMkdir,
  parseRemove,
  parseTransfer,
  parseWrite,
  submitMemoryCommand,
  taskResultDetails,
  taskResultResourceUri,
  TerminalActionError,
  waitForResourceTask,
} from '#/features/workbench/filesystem/lib/terminal-actions'
import type {
  ResourceOpenHandler,
  ResourceRef,
  TerminalCommandParameterKey,
  TerminalCommandView,
  TerminalEntry,
} from '#/features/workbench/filesystem/lib/types'
import {
  cleanVikingUri,
  createIdentityStorageKey,
  entryToRef,
  getErrorMessage,
  registerFilesystemAgentSessionId,
  removeStoredValue,
  searchResultToRefs,
  visibleContextEntries,
} from '#/features/workbench/filesystem/lib/utils'
import type { ResourceImportResult } from '#/features/workbench/resources/lib/resource-import-types'

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
  recursive: { type: 'Boolean', defaultValue: 'false' },
  targetUri: { type: 'URI', required: true },
  sourceUri: { type: 'URI', required: true },
  directoryDescription: { type: 'Text' },
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

function readResourceArgument(body: string): string {
  if (!body.startsWith("'") && !body.startsWith('"')) return body
  const [, uri, ...extra] = tokenize(`/resource ${body}`)
  if (!uri || extra.length)
    throw new Error(i18n.t('filesystem:terminal.enterUri'))
  return uri
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
  const { t, i18n: translation } = useTranslation('filesystem')
  const { connectionRole, identityScopeKey } = useAppConnection()
  const { invalidateAll } = useInvalidateVikingFs()
  const [command, setCommand] = useState('')
  const [running, setRunning] = useState(false)

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

  const append = useCallback((entry: Omit<TerminalEntry, 'id'>) => {
    const id = createRandomUuid()
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
      const commandId = append({ kind: 'command', title: safeHistory.title })
      const appendResult = (entry: Omit<TerminalEntry, 'id' | 'commandId'>) =>
        append({ ...entry, commandId })
      if (safeHistory.remember) rememberCommand(safeHistory.title)
      setCommand('')
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
              appendResult({
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
            appendResult({
              kind: 'success',
              title: '/add-skill',
              body: t('terminal.addSkillSubmitted'),
            })
          }
          return
        }
        if (isCompile) {
          if (/^(?:ov\s+)?\/?compile$/.test(trimmed)) {
            appendResult({
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
          appendResult({
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
        const resourceArgument = () =>
          readResourceArgument(commandInput.slice(name.length).trim())

        if (trimmed.startsWith('viking://')) {
          await onOpenResource(trimmed)
          appendResult({
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
            appendResult({
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
            appendResult({
              body: formatJson(health),
              kind: 'success',
              title: 'health',
            })
            return
          }
          case '/wait': {
            if (body) throw new Error(t('terminal.waitUsage'))
            const result = await getOvResult<unknown>(postSystemWait())
            appendResult({
              body: formatJson(result),
              kind: 'success',
              title: 'wait',
            })
            return
          }
          case '/ls': {
            const target = body
              ? normalizeDirUri(resourceArgument())
              : currentUri
            const result = body
              ? await fetchFsList(target, {
                  nodeLimit: 60,
                  output: 'agent',
                  showAllHidden: true,
                })
              : { entries, uri: currentUri }
            const visibleEntries = visibleContextEntries(result.entries)
            appendResult({
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
            const target = body
              ? normalizeDirUri(resourceArgument())
              : currentUri
            const result = await fetchFsTree(target, {
              nodeLimit: 80,
              output: 'agent',
              showAllHidden: true,
            })
            const visibleEntries = visibleContextEntries(result.nodes)
            appendResult({
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
            const uri = cleanVikingUri(resourceArgument())
            if (!uri) throw new Error(t('terminal.enterUri'))
            const entry = await fetchFsStat(uri, { throwOnError: true })
            appendResult({
              body: formatJson(entry),
              kind: 'success',
              refs: [entryToRef(entry)],
              title: `stat ${uri}`,
            })
            return
          }
          case '/read': {
            if (!body) throw new Error(t('terminal.readUsage'))
            const uri = cleanVikingUri(resourceArgument())
            if (!uri) throw new Error(t('terminal.enterUri'))
            const content = await fetchFileContent(uri, {
              limit: 1200,
              raw: true,
            })
            await onOpenResource(uri)
            appendResult({
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
            const uri = cleanVikingUri(resourceArgument())
            if (!uri) throw new Error(t('terminal.enterUri'))
            const level = name === '/abstract' ? 'abstract' : 'overview'
            const content = await fetchDirectoryLevelContent(uri, level)
            await onOpenResource(uri)
            appendResult({
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
            appendResult({
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
            appendResult({
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
          case '/mkdir': {
            const request = parseMkdir(trimmed)
            await getOvResult<unknown>(postFsMkdir({ body: request }))
            if (identityRef.current !== identityScopeKey) return
            appendResult({
              kind: 'success',
              title: `/mkdir ${request.uri}`,
              body: t('terminal.mkdirCreated', { uri: request.uri }),
              refs: [{ uri: request.uri }],
            })
            await invalidateAll()
            return
          }
          case '/mv':
          case '/cp': {
            const action = name === '/mv' ? 'mv' : 'cp'
            const request = parseTransfer(trimmed, action)
            await getOvResult<unknown>(
              action === 'mv'
                ? postFsMv({ body: request })
                : ovClient.client.post({
                    url: '/api/v1/fs/cp',
                    body: request,
                    headers: { 'Content-Type': 'application/json' },
                  }),
            )
            if (identityRef.current !== identityScopeKey) return
            appendResult({
              kind: 'success',
              title: `${name} ${request.from_uri} ${request.to_uri}`,
              body: t(
                action === 'mv'
                  ? 'terminal.mvCompleted'
                  : 'terminal.cpCompleted',
                { from: request.from_uri, to: request.to_uri },
              ),
              refs: [{ uri: request.to_uri }],
            })
            await invalidateAll()
            return
          }
          case '/rm': {
            const request = parseRemove(trimmed)
            if (
              !window.confirm(
                t(
                  request.recursive
                    ? 'terminal.rmConfirmRecursive'
                    : 'terminal.rmConfirm',
                  { uri: request.uri },
                ),
              )
            ) {
              appendResult({ kind: 'info', title: t('terminal.rmCancelled') })
              return
            }
            if (identityRef.current !== identityScopeKey) return
            await getOvResult<unknown>(deleteFs({ query: request }))
            if (identityRef.current !== identityScopeKey) return
            appendResult({
              kind: 'success',
              title: `/rm ${request.uri}`,
              body: t('terminal.rmDeleted', { uri: request.uri }),
            })
            await invalidateAll()
            return
          }
          case '/write': {
            const request = parseWrite(trimmed)
            const result = await getOvResult<unknown>(
              postContentWrite({ body: request }),
            )
            if (identityRef.current !== identityScopeKey) return
            appendResult({
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
              appendResult({
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
                appendResult({
                  body: t('terminal.sessionCurrentBody', { id }),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: '/session current',
                })
                return
              }
              case 'list': {
                const sessions = await fetchSessions()
                appendResult({
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
                appendResult({
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
                appendResult({
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
                appendResult({
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
                appendResult({
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
                appendResult({
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
                appendResult({
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
                appendResult({
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
                appendResult({
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
                appendResult({
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
                appendResult({
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
                appendResult({
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
                appendResult({
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
        appendResult({
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

  const recalledCommand = useRef<{
    index: number
    draft: string
    value: string
  } | null>(null)
  const firstResultByCommand = new Map<string, TerminalEntry>()
  const commandById = new Map<string, TerminalEntry>()
  for (const entry of history) {
    if (entry.kind === 'command') commandById.set(entry.id, entry)
    else if (entry.commandId && !firstResultByCommand.has(entry.commandId))
      firstResultByCommand.set(entry.commandId, entry)
  }
  const records: TuiRecord[] = history.flatMap((entry) => {
    const commandEntry = entry.kind === 'command'
    const parent = entry.commandId
      ? commandById.get(entry.commandId)
      : undefined
    if (parent && firstResultByCommand.get(parent.id) === entry) return []
    const result = commandEntry ? firstResultByCommand.get(entry.id) : entry
    const richResult =
      result &&
      (result.compileTaskId ||
        result.resourceTaskId ||
        result.compileForm ||
        result.details?.length ||
        result.refs?.length ||
        result.backgroundTaskSource)
    const status =
      result?.kind === 'error'
        ? 'error'
        : running && !result && history.at(-1) === entry
          ? 'pending'
          : 'success'
    return [
      {
        id: entry.id,
        command: parent?.title ?? entry.title,
        createdAtMs: entry.createdAt,
        status,
        content: !result
          ? undefined
          : richResult
            ? {
                variant: 'write-result',
                lines: [
                  <TerminalHistoryItem
                    key={result.id}
                    entry={result}
                    onOpenResource={onOpenResource}
                    openingUri={openingUri}
                  />,
                ],
              }
            : {
                variant: 'text',
                text:
                  result.body ?? (commandEntry || parent ? result.title : ''),
              },
      },
    ]
  })
  const acceptsResource = (commandName: string) =>
    /^\/(?:rm|read|stat|ls|tree|abstract|overview)$/.test(commandName)
  const resourceInput = (commandName: string, uri: string) =>
    `${commandName} '${uri.replaceAll("'", "'\\''")}'`
  const resourceCompletions = commands
    .filter(
      (item) =>
        acceptsResource(item.command) && command.startsWith(`${item.command} `),
    )
    .flatMap((item) =>
      resourceCandidates.map((uri) => ({
        name: `${item.command.slice(1)} ${uri}`,
        inputText: resourceInput(item.command, uri),
        description: uri,
        usage: item.usage,
        group: 'resources',
      })),
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
        <Tui
          locale={translation.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
          value={command}
          onValueChange={(value) => {
            recalledCommand.current = null
            setCommand(value)
          }}
          disabled={running}
          records={records}
          onKeyDownCapture={(event) => {
            // The shared history derives from every displayed record. Studio's history
            // excludes sensitive commands and result titles. Let completion handle its arrows.
            if (
              running ||
              event.defaultPrevented ||
              event.nativeEvent.isComposing ||
              event.nativeEvent.keyCode === 229 ||
              event.shiftKey ||
              (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') ||
              !(event.target instanceof HTMLTextAreaElement) ||
              event.target.hasAttribute('aria-controls')
            )
              return
            event.stopPropagation()
            if (!commandHistory.length) return
            const input = event.target
            if (input.selectionStart !== input.selectionEnd) return
            const previous = recalledCommand.current
            const browsing = previous?.value === command
            const up =
              event.key === 'ArrowUp' &&
              !command.slice(0, input.selectionStart).includes('\n')
            const down =
              event.key === 'ArrowDown' &&
              browsing &&
              !command.slice(input.selectionEnd).includes('\n')
            if (!up && !down) return
            event.preventDefault()
            const index = Math.max(
              0,
              Math.min(
                commandHistory.length,
                (browsing ? previous.index : commandHistory.length) +
                  (up ? -1 : 1),
              ),
            )
            const draft = browsing ? previous.draft : command
            const value =
              commandHistory[commandHistory.length - 1 - index] ?? draft
            recalledCommand.current = { index, draft, value }
            setCommand(value)
          }}
          commands={[
            ...resourceCompletions,
            ...[
              ...commands,
              ...SESSION_SUBCOMMANDS.map((item) => ({
                ...item,
                command: item.insertText,
                group: 'core',
                description: t(
                  `terminal.commandExamples.${item.examples[0]}.description`,
                ),
              })),
            ].map((item) => ({
              name: item.command.replace(/^\//, ''),
              inputText: item.command,
              group: item.group,
              description: item.description,
              usage: item.usage,
              params: item.parameters?.map((key) => ({
                label: t(`terminal.commandParameters.${key}.name`),
                description: t(`terminal.commandParameters.${key}.description`),
                required: PARAMETER_META[key]?.required,
                defaultValue: PARAMETER_META[key]?.defaultValue,
              })),
              examples: [
                ...(item.examples ?? []).map((key) => ({
                  value: t(`terminal.commandExamples.${key}.code`),
                  label: t(`terminal.commandExamples.${key}.description`),
                })),
                ...(acceptsResource(item.command)
                  ? resourceCandidates.slice(0, 20).map((uri) => ({
                      value: resourceInput(item.command, uri),
                      label: uri,
                    }))
                  : []),
              ],
            })),
          ]}
          onSubmit={(value) => {
            void runCommand(value)
          }}
          onOpenUri={(uri) => {
            void onOpenResource(uri)
          }}
          labels={{
            placeholder: t('terminal.placeholder'),
            send: t('terminal.inputHint'),
          }}
          quickCommands={['add-resource', 'add-skill', 'add-memory', 'find']}
        />
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

const TERMINAL_RESOURCE_LINK = new RegExp(`(${VIKING_URI_RE.source})`, 'g')

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
            search={{
              file: undefined,
              uri: undefined,
              panel: undefined,
              session: undefined,
            }}
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
          {entry.body.split(TERMINAL_RESOURCE_LINK).map((part, index) =>
            index % 2 ? (
              <button
                key={index}
                type="button"
                className="break-all text-primary hover:underline"
                onClick={() => void onOpenResource(part)}
              >
                {part}
              </button>
            ) : (
              part
            ),
          )}
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
