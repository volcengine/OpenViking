import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, createFileRoute, useNavigate } from '@tanstack/react-router'
import { Brain, FileText, FolderOpen, Loader2, SearchIcon, SendIcon, Upload, Workflow, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { cn } from '#/lib/utils'
import { fetchFind, fetchFindAllTypes, fetchSearch } from './resources/-lib/api'
import { useVikingFsList } from './resources/-hooks/viking-fm'
import type { FindContextType, FindResultItem, GroupedFindResult } from './resources/-types/viking-fm'
import { fileNameFromUri, normalizeDirUri, normalizeFileUri, parentUri as getParentUri } from './resources/-lib/normalize'

const RESULT_COUNT_OPTIONS = [5, 10, 20, 50] as const
const DEFAULT_RESULT_COUNT = 10
const RETRIEVAL_MODES = ['find', 'search'] as const
type RetrievalMode = typeof RETRIEVAL_MODES[number]
const DEFAULT_RETRIEVAL_MODE: RetrievalMode = 'find'
const RETRIEVAL_SCOPES = ['all', 'resources', 'custom'] as const
type RetrievalScope = typeof RETRIEVAL_SCOPES[number]
const DEFAULT_RETRIEVAL_SCOPE: RetrievalScope = 'all'
const DEFAULT_CUSTOM_PATH_INPUT = 'resources/'
const LAST_RETRIEVAL_SEARCH_KEY = 'openviking.web-studio.retrieval.lastSearch'

type RetrievalSearch = {
  q?: string
  mode?: RetrievalMode
  count?: number
  scope?: RetrievalScope
  path?: string
  session?: string
}

function isRetrievalMode(value: unknown): value is RetrievalMode {
  return typeof value === 'string' && (RETRIEVAL_MODES as readonly string[]).includes(value)
}

function isRetrievalScope(value: unknown): value is RetrievalScope {
  return typeof value === 'string' && (RETRIEVAL_SCOPES as readonly string[]).includes(value)
}

function parseResultCount(value: unknown): number | undefined {
  const numeric = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN
  return RESULT_COUNT_OPTIONS.includes(numeric as (typeof RESULT_COUNT_OPTIONS)[number]) ? numeric : undefined
}

function validateRetrievalSearch(search: Record<string, unknown>): RetrievalSearch {
  const q = typeof search.q === 'string' ? search.q.trim() : undefined
  const count = parseResultCount(search.count)
  const path = typeof search.path === 'string' ? search.path.trim() : undefined
  const session = typeof search.session === 'string' ? search.session.trim() : undefined

  return {
    ...(q && { q }),
    ...(isRetrievalMode(search.mode) && { mode: search.mode }),
    ...(count && { count }),
    ...(isRetrievalScope(search.scope) && { scope: search.scope }),
    ...(path && { path }),
    ...(session && { session }),
  }
}

function hasRetrievalSearch(search: RetrievalSearch): boolean {
  return Boolean(search.q || search.mode || search.count || search.scope || search.path || search.session)
}

function readLastRetrievalSearch(): RetrievalSearch | undefined {
  if (typeof window === 'undefined') {
    return undefined
  }

  try {
    const raw = window.sessionStorage.getItem(LAST_RETRIEVAL_SEARCH_KEY)
    if (!raw) {
      return undefined
    }

    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return undefined
    }

    const search = validateRetrievalSearch(parsed as Record<string, unknown>)
    return search.q ? search : undefined
  } catch {
    return undefined
  }
}

function writeLastRetrievalSearch(search: RetrievalSearch) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    window.sessionStorage.setItem(LAST_RETRIEVAL_SEARCH_KEY, JSON.stringify(search))
  } catch {
    // Ignore storage failures in restricted environments.
  }
}

function buildSubmittedSearch(params: {
  q: string
  mode: RetrievalMode
  count: number
  scope: RetrievalScope
  path: string
  session: string
}): RetrievalSearch {
  const q = params.q.trim()
  const path = params.path.trim()
  const session = params.session.trim()

  return {
    q,
    ...(params.mode !== DEFAULT_RETRIEVAL_MODE && { mode: params.mode }),
    ...(params.count !== DEFAULT_RESULT_COUNT && { count: params.count }),
    ...(params.scope !== DEFAULT_RETRIEVAL_SCOPE && { scope: params.scope }),
    ...(params.scope === 'custom' && path && { path }),
    ...(params.mode === 'search' && session && { session }),
  }
}

export const Route = createFileRoute('/retrieval')({
  validateSearch: validateRetrievalSearch,
  component: RetrievalPage,
})

const KNOWN_VIKING_SCOPES = new Set(['agent', 'resources', 'session', 'temp', 'user'])

const TYPE_META: Record<FindContextType, { icon: typeof Brain; color: string; bgColor: string }> = {
  resource: { icon: FileText, color: 'text-blue-500', bgColor: 'bg-blue-500/15' },
  memory: { icon: Brain, color: 'text-amber-500', bgColor: 'bg-amber-500/15' },
  skill: { icon: Wrench, color: 'text-emerald-500', bgColor: 'bg-emerald-500/15' },
}

const LOADING_HINT_KEYS = ['loading.vector', 'loading.scan', 'loading.match', 'loading.rerank'] as const

interface FlatItem {
  type: FindContextType
  item: FindResultItem
  flatIndex: number
}

function flattenResults(data: GroupedFindResult): FlatItem[] {
  const items: FlatItem[] = []
  let idx = 0
  for (const r of data.resources) items.push({ type: 'resource', item: r, flatIndex: idx++ })
  for (const m of data.memories) items.push({ type: 'memory', item: m, flatIndex: idx++ })
  for (const s of data.skills) items.push({ type: 'skill', item: s, flatIndex: idx++ })
  return items
}

function displayName(uri: string): { name: string; parent: string } {
  const name = fileNameFromUri(uri)
  const dir = getParentUri(uri)
  const segments = dir.replace(/\/$/, '').split('/').filter(Boolean)
  const parent = segments.length > 1 ? segments.slice(-1)[0] : dir
  return { name, parent }
}

function isDirectoryResult(item: FindResultItem): boolean {
  return item.uri.endsWith('/') || item.level < 2
}

function resourceSearchForResult(item: FindResultItem): { uri: string; file?: string } {
  const uri = item.uri.trim()
  if (!uri) {
    return { uri: 'viking://' }
  }

  if (isDirectoryResult(item)) {
    return { uri: normalizeDirUri(uri) }
  }

  const file = normalizeFileUri(uri)
  return { uri: getParentUri(file), file }
}

function normalizeScopeInput(value: string): string | undefined {
  const trimmed = value.trim()
  if (!trimmed || trimmed === '/' || trimmed === 'viking://') {
    return undefined
  }

  if (trimmed.startsWith('viking://')) {
    return normalizeDirUri(trimmed)
  }

  const path = trimmed.replace(/^\/+/, '')
  if (!path) {
    return undefined
  }

  const [scope] = path.split('/')
  const scopedPath = KNOWN_VIKING_SCOPES.has(scope) ? path : `resources/${path}`
  return normalizeDirUri(`viking://${scopedPath}`)
}

function resolveScopeTargetUri(scope: RetrievalScope, customPathInput: string): string | undefined {
  if (scope === 'all') {
    return undefined
  }

  if (scope === 'resources') {
    return 'viking://resources/'
  }

  return normalizeScopeInput(customPathInput)
}

function RetrievalPage() {
  const { t } = useTranslation('retrieval')
  const navigate = useNavigate({ from: Route.fullPath })
  const search = Route.useSearch()
  const hasUrlSearch = hasRetrievalSearch(search)
  const restoredSearch = useMemo(() => (hasUrlSearch ? undefined : readLastRetrievalSearch()), [hasUrlSearch])
  const activeSearch = hasUrlSearch ? search : (restoredSearch ?? search)
  const initialQuery = activeSearch.q ?? ''
  const initialMode = activeSearch.mode ?? DEFAULT_RETRIEVAL_MODE
  const initialResultCount = activeSearch.count ?? DEFAULT_RESULT_COUNT
  const initialScope = activeSearch.scope ?? DEFAULT_RETRIEVAL_SCOPE
  const initialCustomPath = activeSearch.path ?? DEFAULT_CUSTOM_PATH_INPUT
  const initialSessionId = activeSearch.session ?? ''
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>(initialMode)
  const [query, setQuery] = useState(initialQuery)
  const [submittedQuery, setSubmittedQuery] = useState(initialQuery)
  const [resultCount, setResultCount] = useState<number>(initialResultCount)
  const [retrievalScope, setRetrievalScope] = useState<RetrievalScope>(initialScope)
  const [customPathInput, setCustomPathInput] = useState(initialCustomPath)
  const [sessionIdInput, setSessionIdInput] = useState(initialSessionId)
  const inputRef = useRef<HTMLInputElement>(null)
  const resourceProbeQuery = useVikingFsList('viking://resources/', {
    output: 'agent',
    nodeLimit: 1,
    recursive: true,
    showAllHidden: false,
  })

  const targetUri = useMemo(() => {
    return resolveScopeTargetUri(retrievalScope, customPathInput)
  }, [customPathInput, retrievalScope])

  const hasSubmitted = submittedQuery.trim().length > 0
  const sessionId = sessionIdInput.trim() || undefined

  const retrievalQuery = useQuery<GroupedFindResult>({
    queryKey: ['retrieval', retrievalMode, submittedQuery, targetUri, resultCount, sessionId],
    queryFn: () => {
      if (retrievalMode === 'search') {
        return fetchSearch(submittedQuery, { targetUri, limit: resultCount, sessionId })
      }

      return targetUri
        ? fetchFind(submittedQuery, { targetUri, limit: resultCount })
        : fetchFindAllTypes(submittedQuery, { limit: resultCount })
    },
    enabled: hasSubmitted,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    placeholderData: (prev) => prev,
  })

  const data = hasSubmitted ? retrievalQuery.data : undefined
  const hasResults = data && data.total > 0
  const hasRetrievableContext = (resourceProbeQuery.data?.entries.length ?? 0) > 0
  const flatItems = useMemo(() => (data ? flattenResults(data) : []), [data])
  const queryPlanItems = data?.query_plan?.queries ?? []

  const handleSubmit = useCallback(() => {
    const trimmed = query.trim()
    if (trimmed.length === 0) {
      return
    }

    const nextSearch = buildSubmittedSearch({
      q: trimmed,
      mode: retrievalMode,
      count: resultCount,
      scope: retrievalScope,
      path: customPathInput,
      session: sessionIdInput,
    })

    setSubmittedQuery(trimmed)
    writeLastRetrievalSearch(nextSearch)
    void navigate({
      search: nextSearch,
      replace: true,
    })
  }, [customPathInput, navigate, query, resultCount, retrievalMode, retrievalScope, sessionIdInput])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
        e.preventDefault()
        handleSubmit()
      }
    },
    [handleSubmit],
  )

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!activeSearch.q) {
      return
    }

    const nextMode = activeSearch.mode ?? DEFAULT_RETRIEVAL_MODE
    const nextResultCount = activeSearch.count ?? DEFAULT_RESULT_COUNT
    const nextScope = activeSearch.scope ?? DEFAULT_RETRIEVAL_SCOPE
    const nextCustomPath = activeSearch.path ?? DEFAULT_CUSTOM_PATH_INPUT
    const nextSessionId = activeSearch.session ?? ''

    setRetrievalMode(nextMode)
    setQuery(activeSearch.q)
    setSubmittedQuery(activeSearch.q)
    setResultCount(nextResultCount)
    setRetrievalScope(nextScope)
    setCustomPathInput(nextCustomPath)
    setSessionIdInput(nextSessionId)

    const nextSearch = buildSubmittedSearch({
      q: activeSearch.q,
      mode: nextMode,
      count: nextResultCount,
      scope: nextScope,
      path: nextCustomPath,
      session: nextSessionId,
    })

    writeLastRetrievalSearch(nextSearch)

    if (!hasUrlSearch) {
      void navigate({
        search: nextSearch,
        replace: true,
      })
    }
  }, [activeSearch.count, activeSearch.mode, activeSearch.path, activeSearch.q, activeSearch.scope, activeSearch.session, hasUrlSearch, navigate])

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      {/* Search input */}
      <div className="flex items-center gap-2 rounded-lg border bg-card px-4 py-3">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('searchPlaceholder')}
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
        />
        <Button
          variant="ghost"
          size="icon"
          className="size-8 shrink-0"
          onClick={handleSubmit}
          disabled={query.trim().length === 0}
        >
          <SendIcon className="size-4" />
        </Button>
      </div>

      {/* Controls bar */}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={retrievalMode} onValueChange={(value) => setRetrievalMode(value as RetrievalMode)}>
          <SelectTrigger size="sm" aria-label={t('controls.function')}>
            <SelectValue>{t(`controls.modes.${retrievalMode}`)}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {RETRIEVAL_MODES.map((mode) => (
              <SelectItem key={mode} value={mode}>{t(`controls.modes.${mode}`)}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={String(resultCount)} onValueChange={(v) => setResultCount(Number(v))}>
          <SelectTrigger size="sm" aria-label={t('controls.resultCount')}>
            <SelectValue>{t('controls.resultCount')} {resultCount}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {RESULT_COUNT_OPTIONS.map((n) => (
              <SelectItem key={n} value={String(n)}>{n}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={retrievalScope} onValueChange={(value) => setRetrievalScope(value as RetrievalScope)}>
          <SelectTrigger size="sm" aria-label={t('controls.scope')}>
            <SelectValue>{t(`controls.scopes.${retrievalScope}.label`)}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {RETRIEVAL_SCOPES.map((scope) => (
              <SelectItem key={scope} value={scope}>
                {t(`controls.scopes.${scope}.label`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {retrievalScope === 'custom' && (
          <Input
            value={customPathInput}
            onChange={(e) => setCustomPathInput(e.target.value)}
            placeholder={t('controls.customScopePlaceholder')}
            aria-label={t('controls.customScope')}
            className="h-8 w-64 font-mono text-sm"
          />
        )}

        <div className="inline-flex h-8 max-w-full items-center gap-1.5 rounded-md border bg-muted/30 px-2.5 text-xs text-muted-foreground">
          <FolderOpen className="size-3.5 shrink-0" />
          <span className="shrink-0">{t('controls.effectiveScope')}</span>
          <span className="max-w-64 truncate font-mono text-foreground">
            {targetUri ?? t('controls.allContexts')}
          </span>
        </div>

        {retrievalMode === 'search' && (
          <Input
            value={sessionIdInput}
            onChange={(e) => setSessionIdInput(e.target.value)}
            placeholder={t('controls.sessionPlaceholder')}
            aria-label={t('controls.sessionId')}
            className="h-8 w-52 font-mono text-sm"
          />
        )}
      </div>

      {/* Results section */}
      <div className="flex flex-col gap-3">
        <h2 className="text-base font-medium">
          {hasSubmitted && hasResults
            ? t('results.topN', { count: Math.min(flatItems.length, resultCount) })
            : t('results.title')}
        </h2>

        <div className="min-h-80 rounded-lg border border-dashed bg-card/50">
          {!hasSubmitted ? (
            <div className="flex min-h-80 flex-col items-center justify-center gap-3 text-center">
              {resourceProbeQuery.isLoading ? (
                <>
                  <Loader2 className="size-8 animate-spin text-muted-foreground/30" />
                  <p className="text-sm text-muted-foreground">{t('empty.checking')}</p>
                </>
              ) : hasRetrievableContext ? (
                <>
                  <SearchIcon className="size-10 text-muted-foreground/25" />
                  <p className="text-sm text-muted-foreground">{t('empty.readyTitle')}</p>
                  <p className="text-xs text-muted-foreground/60">{t('empty.readyDescription')}</p>
                </>
              ) : (
                <>
                  <SearchIcon className="size-10 text-muted-foreground/25" />
                  <p className="text-sm text-muted-foreground">{t('empty.title')}</p>
                  <p className="text-xs text-muted-foreground/60">{t('empty.description')}</p>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="mt-1 gap-1.5"
                    onClick={() => navigate({ to: '/resources', search: { upload: true } })}
                  >
                    <Upload className="size-4" />
                    {t('empty.upload')}
                  </Button>
                </>
              )}
            </div>
          ) : retrievalQuery.isLoading ? (
            <LoadingHint />
          ) : retrievalQuery.error ? (
            <div className="flex min-h-80 items-center justify-center text-sm text-destructive">
              {t('error')}
            </div>
          ) : !hasResults ? (
            <div className="flex min-h-80 flex-col items-center justify-center gap-2 text-center">
              <SearchIcon className="size-8 text-muted-foreground/25" />
              <p className="text-sm text-muted-foreground/60">{t('noResults.title')}</p>
              <p className="text-xs text-muted-foreground/40">{t('noResults.subtitle')}</p>
            </div>
          ) : (
            <div className="divide-y">
              {queryPlanItems.length > 0 && (
                <div className="border-b bg-muted/20 px-4 py-3">
                  <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                    <Workflow className="size-3.5" />
                    <span>{t('queryPlan.title', { count: queryPlanItems.length })}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {queryPlanItems.slice(0, 4).map((plan, index) => (
                      <span
                        key={`${plan.query}-${index}`}
                        className="inline-flex max-w-full items-center gap-1 rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground"
                      >
                        {plan.context_type && (
                          <span className={cn('font-medium', TYPE_META[plan.context_type].color)}>
                            {t(`types.${plan.context_type}`)}
                          </span>
                        )}
                        <span className="truncate">{plan.query}</span>
                      </span>
                    ))}
                    {queryPlanItems.length > 4 && (
                      <span className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
                        {t('queryPlan.more', { count: queryPlanItems.length - 4 })}
                      </span>
                    )}
                  </div>
                </div>
              )}
              {flatItems.map((fi) => {
                const { name, parent } = displayName(fi.item.uri)
                const meta = TYPE_META[fi.type]
                const Icon = meta.icon
                const resourceSearch = resourceSearchForResult(fi.item)

                return (
                  <Link
                    key={`${fi.item.uri}-${fi.flatIndex}`}
                    to="/resources"
                    search={resourceSearch}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-inset"
                  >
                    <div className={cn('mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold uppercase tracking-wide', meta.bgColor, meta.color)}>
                      <Icon className="size-3" />
                      <span>{t(`types.${fi.type}`)}</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{name}</div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground/70">
                        <FolderOpen className="size-3 shrink-0" />
                        <span className="truncate">{parent}</span>
                      </div>
                      {fi.item.abstract && (
                        <p className="mt-1 line-clamp-2 text-xs text-muted-foreground/60">{fi.item.abstract}</p>
                      )}
                    </div>
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-muted-foreground">
                      {fi.item.score.toFixed(3)}
                    </span>
                  </Link>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function LoadingHint() {
  const { t } = useTranslation('retrieval')
  const [hintIndex, setHintIndex] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => {
      setHintIndex((i) => (i + 1) % LOADING_HINT_KEYS.length)
    }, 1500)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="flex min-h-80 flex-col items-center justify-center gap-3">
      <Loader2 className="size-6 animate-spin text-muted-foreground/50" />
      <p key={hintIndex} className="animate-palette-in text-xs text-muted-foreground/60">
        {t(LOADING_HINT_KEYS[hintIndex])}
      </p>
    </div>
  )
}
