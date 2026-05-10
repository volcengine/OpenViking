import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { Brain, FileText, FolderOpen, Loader2, SearchIcon, SendIcon, Wrench } from 'lucide-react'
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
import { fetchFind, fetchFindAllTypes } from './resources/-lib/api'
import type { FindContextType, FindResultItem, GroupedFindResult } from './resources/-types/viking-fm'
import { fileNameFromUri, parentUri as getParentUri } from './resources/-lib/normalize'

export const Route = createFileRoute('/retrieval')({
  component: RetrievalPage,
})

const RESULT_COUNT_OPTIONS = [5, 10, 20, 50] as const

const TYPE_META: Record<FindContextType, { label: string; icon: typeof Brain; color: string; bgColor: string }> = {
  resource: { label: 'Resources', icon: FileText, color: 'text-blue-500', bgColor: 'bg-blue-500/15' },
  memory: { label: 'Memories', icon: Brain, color: 'text-amber-500', bgColor: 'bg-amber-500/15' },
  skill: { label: 'Skills', icon: Wrench, color: 'text-emerald-500', bgColor: 'bg-emerald-500/15' },
}

const LOADING_HINTS = [
  '正在检索向量索引...',
  '扫描知识库层级结构...',
  '匹配语义相关内容...',
  '对结果重排序...',
]

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

function RetrievalPage() {
  const { t } = useTranslation('retrieval')
  const [query, setQuery] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [resultCount, setResultCount] = useState<number>(10)
  const [pathInput, setPathInput] = useState('/')
  const inputRef = useRef<HTMLInputElement>(null)

  const targetUri = useMemo(() => {
    const trimmed = pathInput.trim()
    if (!trimmed || trimmed === '/') return undefined
    const path = trimmed.startsWith('viking://') ? trimmed : `viking://${trimmed.replace(/^\//, '')}`
    return path.endsWith('/') ? path : `${path}/`
  }, [pathInput])

  const hasSubmitted = submittedQuery.trim().length > 0

  const findQuery = useQuery<GroupedFindResult>({
    queryKey: ['retrieval-find', submittedQuery, targetUri, resultCount],
    queryFn: () =>
      targetUri
        ? fetchFind(submittedQuery, { targetUri, limit: resultCount })
        : fetchFindAllTypes(submittedQuery, { limit: resultCount }),
    enabled: hasSubmitted,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    placeholderData: (prev) => prev,
  })

  const data = hasSubmitted ? findQuery.data : undefined
  const hasResults = data && data.total > 0
  const flatItems = useMemo(() => (data ? flattenResults(data) : []), [data])

  const handleSubmit = useCallback(() => {
    const trimmed = query.trim()
    if (trimmed.length > 0) {
      setSubmittedQuery(trimmed)
    }
  }, [query])

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
        <Select value="find" onValueChange={() => {}}>
          <SelectTrigger size="sm">
            <SelectValue>{t('controls.function')}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="find">{t('controls.function')}</SelectItem>
          </SelectContent>
        </Select>

        <Select value={String(resultCount)} onValueChange={(v) => setResultCount(Number(v))}>
          <SelectTrigger size="sm">
            <SelectValue>{t('controls.resultCount')} {resultCount}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {RESULT_COUNT_OPTIONS.map((n) => (
              <SelectItem key={n} value={String(n)}>{n}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Input
          value={pathInput}
          onChange={(e) => setPathInput(e.target.value)}
          placeholder={t('controls.pathPlaceholder')}
          className="h-8 w-32 font-mono text-sm"
        />
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
              <SearchIcon className="size-10 text-muted-foreground/25" />
              <p className="text-sm text-muted-foreground/60">{t('empty.title')}</p>
            </div>
          ) : findQuery.isLoading ? (
            <LoadingHint />
          ) : findQuery.error ? (
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
              {flatItems.map((fi) => {
                const { name, parent } = displayName(fi.item.uri)
                const meta = TYPE_META[fi.type]
                const Icon = meta.icon

                return (
                  <div
                    key={`${fi.item.uri}-${fi.flatIndex}`}
                    className="flex items-start gap-3 px-4 py-3 transition-colors hover:bg-muted/40"
                  >
                    <div className={cn('mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold uppercase tracking-wide', meta.bgColor, meta.color)}>
                      <Icon className="size-3" />
                      <span>{meta.label}</span>
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
                  </div>
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
  const [hintIndex, setHintIndex] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => {
      setHintIndex((i) => (i + 1) % LOADING_HINTS.length)
    }, 1500)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="flex min-h-80 flex-col items-center justify-center gap-3">
      <Loader2 className="size-6 animate-spin text-muted-foreground/50" />
      <p key={hintIndex} className="animate-palette-in text-xs text-muted-foreground/60">
        {LOADING_HINTS[hintIndex]}
      </p>
    </div>
  )
}
