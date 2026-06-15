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
import {
  addMessage,
  commitSession,
  createSession,
  deleteSession,
  extractSession,
  fetchSession,
  fetchSessionArchive,
  fetchSessionContext,
  fetchSessionMessages,
  fetchSessions,
  fetchSessionToolResult,
  fetchSessionToolResults,
  recordSessionUsed,
  searchSessionToolResult,
} from '#/lib/sessions/api'
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
import type { SessionListItem } from '@ov-server/api/v1/sessions'

import { ROOT_URI, TERMINAL_COMMANDS } from '../-lib/constants'
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
  entryToRef,
  getErrorMessage,
  readStoredJsonArray,
  registerPlaygroundAgentSessionId,
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

const COMMAND_HELP_FALLBACKS: Partial<
  Record<string, { description: string; usage: string }>
> = {
  '/abstract': {
    description: '读取目录摘要',
    usage: '/abstract viking://resources/...',
  },
  '/add-resource': {
    description: '添加外部资源',
    usage: '/add-resource',
  },
  '/find': {
    description: '查找相关资源',
    usage: '/find 查询词',
  },
  '/health': {
    description: '查看后端健康状态',
    usage: '/health',
  },
  '/ls': {
    description: '查看已有资源',
    usage: '/ls [viking://resources/...]',
  },
  '/overview': {
    description: '读取目录概览',
    usage: '/overview viking://resources/...',
  },
  '/read': {
    description: '读取资源文件',
    usage: '/read viking://resources/.../file.md',
  },
  '/search': {
    description: '语义检索上下文',
    usage: '/search 查询词',
  },
  '/session': {
    description: '管理 Agent 会话',
    usage: '/session 子命令',
  },
  '/stat': {
    description: '查看资源元信息',
    usage: '/stat viking://resources/...',
  },
  '/status': {
    description: '检查连通状态',
    usage: '/status',
  },
  '/tree': {
    description: '展示目录树',
    usage: '/tree [viking://resources/...]',
  },
  '/wait': {
    description: '等待服务就绪',
    usage: '/wait [--timeout seconds]',
  },
}

const PARAMETER_HELP_FALLBACKS: Record<
  TerminalCommandParameterKey,
  { description: string; name: string }
> = {
  archiveId: {
    description: '读取 archive 时必填。',
    name: 'archive_id',
  },
  contextChars: {
    description: 'tool-search 子命令使用，控制命中上下文长度。',
    name: '--context-chars 数量',
  },
  contexts: {
    description: 'used 子命令可重复传入，记录本轮实际使用的上下文。',
    name: '--context uri',
  },
  keepRecent: {
    description: 'commit 子命令使用，提交后保留最近 N 条 live messages。',
    name: '--keep-recent 数量',
  },
  limit: {
    description: 'tool result 列表、读取或搜索时限制返回数量。',
    name: '--limit 数量',
  },
  messageContent: {
    description: 'message 子命令使用，要追加到 session 的文本内容。',
    name: 'content',
  },
  messageRole: {
    description: 'message 子命令使用，支持 user 或 assistant。',
    name: 'role',
  },
  offset: {
    description: 'tool-result 子命令使用，从指定字符偏移开始读取。',
    name: '--offset 数量',
  },
  query: {
    description: '要检索的关键词或语义问题。',
    name: '查询词',
  },
  scope: {
    description: '可选。不填则全局搜索；传 . 使用当前目录；传 uri 使用指定目录。',
    name: '--scope <.|uri>',
  },
  sessionAction: {
    description:
      'current、list、create、switch、get、context、messages、archive、commit、extract、message、used、tool-results、tool-result、tool-search、delete。',
    name: '子命令',
  },
  sessionId: {
    description: '可选。省略时多数子命令使用当前 Agent session；delete 必须显式指定。',
    name: 'session_id',
  },
  skillJson: {
    description: 'used 子命令使用，记录实际使用的 skill 信息。',
    name: '--skill-json JSON',
  },
  timeout: {
    description: '可选。等待服务就绪的最长时间。',
    name: '--timeout 秒',
  },
  tokenBudget: {
    description: 'context 子命令使用，限制组装上下文的 token 预算。',
    name: '--token-budget 数量',
  },
  toolName: {
    description: 'tool-results 子命令使用，按工具名过滤。',
    name: '--tool-name 名称',
  },
  toolResultId: {
    description: '读取或搜索外部化 tool result 时必填。',
    name: 'tool_result_id',
  },
  uri: {
    description: '可选或必填的 viking:// 资源路径，取决于命令用法。',
    name: 'uri',
  },
}

const EXAMPLE_HELP_FALLBACKS: Partial<
  Record<string, { code: string; description: string }>
> = {
  'abstract.target': {
    code: '/abstract viking://resources/',
    description: '读取目录摘要',
  },
  'addResource.default': {
    code: '/add-resource',
    description: '打开添加资源表单',
  },
  'find.current': {
    code: '/find agent --scope .',
    description: '使用当前高亮目录',
  },
  'find.global': {
    code: '/find agent',
    description: '全局查找相关资源',
  },
  'find.scoped': {
    code: '/find agent --scope viking://resources/',
    description: '只在指定目录查找',
  },
  'health.default': {
    code: '/health',
    description: '查看后端健康状态',
  },
  'ls.current': {
    code: '/ls',
    description: '列出当前目录',
  },
  'ls.target': {
    code: '/ls viking://resources/',
    description: '列出指定目录',
  },
  'overview.target': {
    code: '/overview viking://resources/',
    description: '读取目录概览',
  },
  'read.file': {
    code: '/read viking://resources/file.md',
    description: '读取并打开文件',
  },
  'search.current': {
    code: '/search agent --scope .',
    description: '使用当前高亮目录',
  },
  'search.global': {
    code: '/search agent',
    description: '全局语义检索',
  },
  'search.scoped': {
    code: '/search agent --scope viking://resources/',
    description: '只在指定目录检索',
  },
  'session.archive': {
    code: '/session archive [session_id] <archive_id>',
    description: '读取指定 archive',
  },
  'session.commit': {
    code: '/session commit [session_id] --keep-recent 10',
    description: '归档并触发记忆提取',
  },
  'session.context': {
    code: '/session context [session_id] --token-budget 8000',
    description: '读取组装后的 session context',
  },
  'session.create': {
    code: '/session create [session_id]',
    description: '创建并切换到新 session',
  },
  'session.current': {
    code: '/session',
    description: '查看当前 active session',
  },
  'session.delete': {
    code: '/session delete <session_id>',
    description: '删除指定 session',
  },
  'session.extract': {
    code: '/session extract [session_id]',
    description: '从 session 中提取记忆',
  },
  'session.get': {
    code: '/session get [session_id]',
    description: '查看 session 元信息',
  },
  'session.list': {
    code: '/session list',
    description: '列出所有 session',
  },
  'session.message': {
    code: '/session message [session_id] user hello',
    description: '向 session 追加消息',
  },
  'session.messages': {
    code: '/session messages [session_id]',
    description: '读取 session 消息列表',
  },
  'session.switch': {
    code: '/session switch <session_id>',
    description: '切换 Agent 面板会话',
  },
  'session.toolResult': {
    code: '/session tool-result [session_id] <tool_result_id>',
    description: '读取一个 tool result',
  },
  'session.toolResults': {
    code: '/session tool-results [session_id] --limit 20',
    description: '列出外部化 tool results',
  },
  'session.toolSearch': {
    code: '/session tool-search [session_id] <tool_result_id> query',
    description: '在 tool result 中搜索',
  },
  'session.used': {
    code: '/session used [session_id] --context viking://resources/...',
    description: '记录实际使用的上下文或 skill',
  },
  'stat.target': {
    code: '/stat viking://resources/file.md',
    description: '查看资源元信息',
  },
  'status.default': {
    code: '/status',
    description: '检查 Agent 和 API 连通状态',
  },
  'tree.current': {
    code: '/tree',
    description: '展示当前目录树',
  },
  'tree.target': {
    code: '/tree viking://resources/',
    description: '展示指定目录树',
  },
  'wait.default': {
    code: '/wait',
    description: '等待服务就绪',
  },
  'wait.timeout': {
    code: '/wait --timeout 30',
    description: '指定等待秒数',
  },
}

type SessionSubcommandHelp = {
  description: string
  examples: string[]
  insertText: string
  key: string
  parameters: TerminalCommandParameterKey[]
  usage: string
}

const SESSION_SUBCOMMANDS: SessionSubcommandHelp[] = [
  {
    description: '查看当前 active session',
    examples: ['session.current'],
    insertText: '/session current',
    key: 'current',
    parameters: [],
    usage: '/session current',
  },
  {
    description: '列出所有 session',
    examples: ['session.list'],
    insertText: '/session list',
    key: 'list',
    parameters: [],
    usage: '/session list',
  },
  {
    description: '创建并切换到新 session',
    examples: ['session.create'],
    insertText: '/session create ',
    key: 'create',
    parameters: ['sessionId'],
    usage: '/session create [session_id]',
  },
  {
    description: '切换 Agent 面板会话',
    examples: ['session.switch'],
    insertText: '/session switch ',
    key: 'switch',
    parameters: ['sessionId'],
    usage: '/session switch <session_id>',
  },
  {
    description: '查看 session 元信息',
    examples: ['session.get'],
    insertText: '/session get ',
    key: 'get',
    parameters: ['sessionId'],
    usage: '/session get [session_id]',
  },
  {
    description: '读取组装后的 session context',
    examples: ['session.context'],
    insertText: '/session context ',
    key: 'context',
    parameters: ['sessionId', 'tokenBudget'],
    usage: '/session context [session_id] --token-budget 8000',
  },
  {
    description: '读取 session 消息列表',
    examples: ['session.messages'],
    insertText: '/session messages ',
    key: 'messages',
    parameters: ['sessionId'],
    usage: '/session messages [session_id]',
  },
  {
    description: '读取指定 archive',
    examples: ['session.archive'],
    insertText: '/session archive ',
    key: 'archive',
    parameters: ['sessionId', 'archiveId'],
    usage: '/session archive [session_id] <archive_id>',
  },
  {
    description: '归档并触发记忆提取',
    examples: ['session.commit'],
    insertText: '/session commit ',
    key: 'commit',
    parameters: ['sessionId', 'keepRecent'],
    usage: '/session commit [session_id] --keep-recent 10',
  },
  {
    description: '从 session 中提取记忆',
    examples: ['session.extract'],
    insertText: '/session extract ',
    key: 'extract',
    parameters: ['sessionId'],
    usage: '/session extract [session_id]',
  },
  {
    description: '向 session 追加消息',
    examples: ['session.message'],
    insertText: '/session message ',
    key: 'message',
    parameters: ['sessionId', 'messageRole', 'messageContent'],
    usage: '/session message [session_id] user hello',
  },
  {
    description: '记录实际使用的上下文或 skill',
    examples: ['session.used'],
    insertText: '/session used ',
    key: 'used',
    parameters: ['sessionId', 'contexts', 'skillJson'],
    usage: '/session used [session_id] --context viking://resources/...',
  },
  {
    description: '列出外部化 tool results',
    examples: ['session.toolResults'],
    insertText: '/session tool-results ',
    key: 'tool-results',
    parameters: ['sessionId', 'toolName', 'limit'],
    usage: '/session tool-results [session_id] --limit 20',
  },
  {
    description: '读取一个 tool result',
    examples: ['session.toolResult'],
    insertText: '/session tool-result ',
    key: 'tool-result',
    parameters: ['sessionId', 'toolResultId', 'limit', 'offset'],
    usage: '/session tool-result [session_id] <tool_result_id>',
  },
  {
    description: '在 tool result 中搜索',
    examples: ['session.toolSearch'],
    insertText: '/session tool-search ',
    key: 'tool-search',
    parameters: ['sessionId', 'toolResultId', 'query', 'limit', 'contextChars'],
    usage: '/session tool-search [session_id] <tool_result_id> query',
  },
  {
    description: '删除指定 session',
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
  title: string
}

type ScopedSearchInput = {
  query: string
  scopeUri?: string
}

type ParsedOptions = {
  flags: Map<string, string[]>
  positional: string[]
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
    throw new Error(`Invalid --${key}: ${value}`)
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
  const tokens = body.trim().split(/\s+/).filter(Boolean)
  const queryParts: string[] = []
  let scopeUri: string | undefined

  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index]
    if (token === '--scope') {
      const value = tokens[index + 1]
      if (!value) throw new Error(missingScopeMessage)
      scopeUri = value === '.' ? currentUri : normalizeDirUri(value)
      index += 1
      continue
    }
    if (token.startsWith('--scope=')) {
      const value = token.slice('--scope='.length)
      if (!value) throw new Error(missingScopeMessage)
      scopeUri = value === '.' ? currentUri : normalizeDirUri(value)
      continue
    }
    queryParts.push(token)
  }

  return {
    query: queryParts.join(' ').trim(),
    scopeUri,
  }
}

function sessionToRef(session: Pick<SessionListItem, 'session_id' | 'uri'>) {
  return {
    label: session.session_id,
    meta: 'session',
    uri: session.uri || `viking://session/${session.session_id}`,
  }
}

export function TerminalPanel({
  currentUri,
  entries,
  onOpenAddResource,
  onOpenResource,
  openingUri,
  onSessionChange,
  sessionId,
}: {
  currentUri: string
  entries: VikingFsEntry[]
  onOpenAddResource: () => void
  onOpenResource: ResourceOpenHandler
  openingUri: string | null
  onSessionChange: (sessionId: string) => void
  sessionId?: string
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
        const fallback = COMMAND_HELP_FALLBACKS[item.command]
        return {
          ...item,
          description: t(`terminal.commands.${item.key}.description`, {
            defaultValue: fallback?.description ?? item.command,
          }),
          usage: t(`terminal.commands.${item.key}.usage`, {
            defaultValue: fallback?.usage ?? item.insertText.trim(),
          }),
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

            return SESSION_SUBCOMMANDS.filter(
              (item) =>
                !partial ||
                item.key.toLowerCase().startsWith(partial.toLowerCase()) ||
                item.description.toLowerCase().includes(partial.toLowerCase()),
            ).map((item) => {
              const exampleKey = item.examples[0]
              const fallback = exampleKey
                ? EXAMPLE_HELP_FALLBACKS[exampleKey]
                : undefined
              const description = t(
                `terminal.commandExamples.${exampleKey}.description`,
                {
                  defaultValue: fallback?.description ?? item.description,
                },
              )
              return {
                ...activeCommand,
                command: item.key,
                description,
                group: 'subcommand' as const,
                id: `subcommand:session:${item.key}`,
                insertText: item.insertText,
                key: `session:${item.key}`,
                usage: item.usage,
              }
            })
          })()
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
    return [
      ...commandMatches,
      ...resourceMatches,
      ...sessionSubcommandMatches,
      ...historyMatches,
    ].filter((item) => {
      if (seen.has(item.insertText)) return false
      seen.add(item.insertText)
      return true
    })
  }, [activeCommand, command, commandHistory, commands, resourceCandidates, t])

  const helpCommand = useMemo(() => {
    if (!activeCommand) return undefined
    return activeCommand
  }, [activeCommand])

  const selectedSessionSubcommand = useMemo(() => {
    if (helpCommand?.command !== '/session') return undefined
    const body = command
      .trimStart()
      .slice(helpCommand.command.length)
      .trimStart()
    const [subcommand = ''] = body.split(/\s+/)
    return SESSION_SUBCOMMANDS_BY_KEY.get(subcommand)
  }, [command, helpCommand])

  const sessionSubcommandRows = useMemo(
    () =>
      SESSION_SUBCOMMANDS.map((item) => {
        const exampleKey = item.examples[0]
        const fallback = exampleKey
          ? EXAMPLE_HELP_FALLBACKS[exampleKey]
          : undefined
        return {
          ...item,
          description: t(`terminal.commandExamples.${exampleKey}.description`, {
            defaultValue: fallback?.description ?? item.description,
          }),
        }
      }),
    [t],
  )

  const showSessionSubcommandList =
    helpCommand?.command === '/session' && !selectedSessionSubcommand

  const helpTitle = selectedSessionSubcommand
    ? `/session ${selectedSessionSubcommand.key}`
    : helpCommand?.command
  const helpDescription = selectedSessionSubcommand
    ? t(
        `terminal.commandExamples.${selectedSessionSubcommand.examples[0]}.description`,
        {
          defaultValue:
            EXAMPLE_HELP_FALLBACKS[selectedSessionSubcommand.examples[0]]
              ?.description ?? selectedSessionSubcommand.description,
        },
      )
    : helpCommand?.description
  const helpUsage = selectedSessionSubcommand
    ? selectedSessionSubcommand.usage
    : helpCommand?.usage

  const commandParameters = useMemo(
    () =>
      (
        selectedSessionSubcommand?.parameters ??
        (showSessionSubcommandList ? [] : (helpCommand?.parameters ?? []))
      ).map((key) => {
        const fallback = PARAMETER_HELP_FALLBACKS[key]
        return {
          description: t(`terminal.commandParameters.${key}.description`, {
            defaultValue: fallback.description,
          }),
          key,
          name: t(`terminal.commandParameters.${key}.name`, {
            defaultValue: fallback.name,
          }),
        }
      }),
    [helpCommand, selectedSessionSubcommand, showSessionSubcommandList, t],
  )

  const commandExamples = useMemo(
    () =>
      (
        selectedSessionSubcommand?.examples ??
        (showSessionSubcommandList ? [] : (helpCommand?.examples ?? []))
      ).map((key) => {
        const fallback = EXAMPLE_HELP_FALLBACKS[key]
        return {
          code: t(`terminal.commandExamples.${key}.code`, {
            defaultValue: fallback?.code ?? key,
          }),
          description: t(`terminal.commandExamples.${key}.description`, {
            defaultValue: fallback?.description ?? '',
          }),
          key,
        }
      }),
    [helpCommand, selectedSessionSubcommand, showSessionSubcommandList, t],
  )

  const canUseCurrentScope =
    helpCommand?.command === '/find' || helpCommand?.command === '/search'

  const showCommandAssist =
    inputFocused &&
    (Boolean(helpCommand) || (suggestionsOpen && suggestions.length > 0))

  useEffect(() => {
    setActiveSuggestionIndex(0)
  }, [suggestions.length])

  useEffect(() => {
    suggestionRefs.current = suggestionRefs.current.slice(
      0,
      suggestions.length,
    )
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
      setSuggestionsOpen(false)
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
            const { query, scopeUri } = parseScopedSearchInput(
              body,
              currentUri,
              t('terminal.searchUsage', { name }),
            )
            if (!query) throw new Error(t('terminal.searchUsage', { name }))
            const result = await fetchSearch(query, {
              limit: 8,
              targetUri: scopeUri,
            })
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
          case '/add-resource': {
            onOpenAddResource()
            append({
              body: t('terminal.addResourceBody'),
              kind: 'info',
              title: t('terminal.addResourceTitle'),
            })
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
              uri: `viking://session/${id}`,
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
                registerPlaygroundAgentSessionId(result.session_id)
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
                registerPlaygroundAgentSessionId(id)
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
                      uri: `viking://session/${id}/history/${archiveId}`,
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
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: `/session commit ${id}`,
                })
                return
              }
              case 'extract': {
                const id = resolveSessionId()
                const result = await extractSession(id)
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: `/session extract ${id}`,
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
                const content = positional.slice(roleIndex + 1).join(' ').trim()
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
              case 'used': {
                const id = resolveSessionId()
                const contexts = flags.get('context')
                const skillJson = getLastFlag(flags, 'skill-json')
                const result = await recordSessionUsed(id, {
                  contexts,
                  skill: skillJson ? JSON.parse(skillJson) : undefined,
                })
                append({
                  body: formatJson(result),
                  kind: 'success',
                  refs: [sessionRef(id)],
                  title: `/session used ${id}`,
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
                      uri: `viking://session/${id}/tool-results/${toolResultId}`,
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
                      uri: `viking://session/${id}/tool-results/${toolResultId}`,
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
      onSessionChange,
      running,
      rememberCommand,
      sessionId,
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

  const insertCurrentScope = useCallback(() => {
    const trimmed = command.trimEnd()
    const hasScope = trimmed.match(/(?:^|\s)--scope(?:=|\s)/)
    const commandName = helpCommand?.command ?? trimmed.split(/\s+/)[0]
    const body = trimmed.slice(commandName.length).trim()
    const next = hasScope
      ? command
      : body
        ? `${trimmed} --scope ${currentUri}`
        : `${commandName}  --scope ${currentUri}`
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
            <span
              className="min-w-0 max-w-[60%] truncate rounded-md border bg-muted/40 px-2 py-1 font-mono text-[11px] text-foreground"
              title={currentUri}
            >
              {t('terminal.scopeLabel', { uri: currentUri })}
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
                window.setTimeout(() => {
                  setInputFocused(false)
                  setSuggestionsOpen(false)
                }, 120)
              }}
              onChange={(event) => {
                setCommand(event.target.value)
                setSuggestionsOpen(true)
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
                  acceptSuggestion(suggestions[activeSuggestionIndex])
                  return
                }
                if (event.key === 'Escape') {
                  setSuggestionsOpen(false)
                }
              }}
              placeholder={t('terminal.placeholder')}
              className="h-8 min-w-0 flex-1 bg-transparent font-mono text-sm outline-none placeholder:text-muted-foreground/60"
            />
            {showCommandAssist ? (
              <div className="absolute bottom-[calc(100%+0.5rem)] left-0 right-0 z-20 max-h-[min(72vh,36rem)] overflow-y-auto rounded-xl border bg-popover shadow-xl">
                {helpCommand ? (
                  <div className="border-b p-3">
                    <div className="flex min-w-0 items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate font-mono text-sm font-semibold text-primary">
                          {helpTitle}
                        </div>
                        <div className="mt-0.5 text-xs leading-4 text-muted-foreground">
                          {helpDescription}
                        </div>
                      </div>
                      <div className="shrink-0 rounded-md bg-muted px-2 py-1 font-mono text-[11px] text-muted-foreground">
                        {helpUsage}
                      </div>
                    </div>
                    <div className="mt-3 space-y-3">
                      {showSessionSubcommandList ? (
                        <div className="min-w-0">
                          <div className="mb-1.5 text-[11px] font-medium text-muted-foreground">
                            {t('terminal.helpSubcommands', {
                              defaultValue: '子命令',
                            })}
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
                          <div className="min-w-0">
                            <div className="mb-1.5 text-[11px] font-medium text-muted-foreground">
                              {t('terminal.helpParameters')}
                            </div>
                            {commandParameters.length > 0 ? (
                              <div className="overflow-hidden rounded-md border">
                                <table className="w-full table-fixed border-collapse text-xs">
                                  <tbody className="divide-y">
                                    {commandParameters.map((parameter) => (
                                      <tr key={parameter.key}>
                                        <td className="w-32 align-top bg-muted/30 px-2 py-1.5 font-mono text-foreground">
                                          <span className="block truncate">
                                            {parameter.name}
                                          </span>
                                        </td>
                                        <td className="align-top px-2 py-1.5 text-muted-foreground">
                                          <div className="flex min-w-0 items-start justify-between gap-2">
                                            <span className="min-w-0 leading-4">
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
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            ) : (
                              <div className="rounded-md border px-2 py-1.5 text-xs text-muted-foreground">
                                {t('terminal.noParameters')}
                              </div>
                            )}
                          </div>
                          <div className="min-w-0">
                            <div className="mb-1.5 text-[11px] font-medium text-muted-foreground">
                              {t('terminal.helpExamples')}
                            </div>
                            <div className="overflow-hidden rounded-md border">
                              <table className="w-full table-fixed border-collapse text-xs">
                                <tbody className="divide-y">
                                  {commandExamples.map((example) => (
                                    <tr key={example.key}>
                                      <td className="w-56 align-top bg-muted/30 px-2 py-1.5 font-mono text-foreground">
                                        <span
                                          className="block truncate"
                                          title={example.code}
                                        >
                                          {example.code}
                                        </span>
                                      </td>
                                      <td className="align-top px-2 py-1.5 leading-4 text-muted-foreground">
                                        {example.description}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                ) : null}
                {suggestionsOpen && suggestions.length > 0 ? (
                  <div className="max-h-56 overflow-y-auto p-1.5">
                    {suggestions.map((suggestion, index) => (
                      <button
                        key={suggestion.id}
                        ref={(node) => {
                          suggestionRefs.current[index] = node
                        }}
                        type="button"
                        title={`${suggestion.usage} · ${t('terminal.suggestionsHint')}`}
                        className={cn(
                          'min-h-8 w-full min-w-0 rounded-md px-2.5 py-1 text-left transition-colors',
                          index === activeSuggestionIndex
                            ? 'bg-primary/10 text-foreground'
                            : 'hover:bg-muted/60',
                        )}
                        onClick={() => acceptSuggestion(suggestion)}
                        onMouseDown={(event) => event.preventDefault()}
                        onMouseEnter={() => setActiveSuggestionIndex(index)}
                      >
                        {suggestion.group === 'history' ||
                        suggestion.group === 'resource' ||
                        suggestion.group === 'subcommand' ? (
                          <span className="flex min-w-0 items-center gap-3">
                            <span
                              className="min-w-0 flex-1 truncate font-mono text-xs font-semibold text-primary"
                              title={suggestion.command}
                            >
                              {suggestion.command}
                            </span>
                            <span className="shrink-0 text-xs leading-4 text-muted-foreground">
                              {suggestion.description}
                            </span>
                          </span>
                        ) : (
                          <span className="grid min-w-0 grid-cols-[minmax(5.25rem,7.5rem)_minmax(0,1fr)] items-center gap-2.5">
                            <span
                              className="min-w-0 truncate font-mono text-xs font-semibold text-primary"
                              title={suggestion.command}
                            >
                              {suggestion.command}
                            </span>
                            <span className="min-w-0 text-xs leading-4 text-muted-foreground">
                              {suggestion.description}
                            </span>
                          </span>
                        )}
                      </button>
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
