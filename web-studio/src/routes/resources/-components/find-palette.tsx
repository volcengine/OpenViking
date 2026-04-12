import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Brain, FileText, FolderOpen, Loader2, Search, Wrench, X } from 'lucide-react'

import { cn } from '#/lib/utils'

import { fileNameFromUri, parentUri as getParentUri } from '../-lib/normalize'
import { useVikingFind, useVikingFsStat } from '../-hooks/viking-fm'
import type { FindContextType, FindResultItem, GroupedFindResult } from '../-types/viking-fm'
import { FilePreview } from './file-preview'
import { DirBrowser } from './dir-browser'

interface FindPaletteProps {
  open: boolean
  onClose: () => void
  onNavigate: (uri: string) => void
  onNavigateDir: (uri: string) => void
  onScopeChange: (uri: string) => void
  scopeUri?: string
}

interface FlatItem {
  type: FindContextType
  item: FindResultItem
  flatIndex: number
}

const TYPE_META: Record<FindContextType, { label: string; icon: typeof Brain; color: string; bgColor: string }> = {
  resource: { label: 'Resources', icon: FileText, color: 'text-blue-500', bgColor: 'bg-blue-500/15' },
  memory: { label: 'Memories', icon: Brain, color: 'text-amber-500', bgColor: 'bg-amber-500/15' },
  skill: { label: 'Skills', icon: Wrench, color: 'text-emerald-500', bgColor: 'bg-emerald-500/15' },
}

const COLUMNS: Array<{ key: keyof Pick<GroupedFindResult, 'resources' | 'memories' | 'skills'>; type: FindContextType }> = [
  { key: 'resources', type: 'resource' },
  { key: 'memories', type: 'memory' },
  { key: 'skills', type: 'skill' },
]

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

function toFsEntry(item: FindResultItem): { uri: string; name: string; isDir: boolean; size: string; sizeBytes: null; modTime: string; modTimestamp: null; abstract: string } {
  return {
    uri: item.uri,
    name: fileNameFromUri(item.uri),
    isDir: item.uri.endsWith('/'),
    size: '',
    sizeBytes: null,
    modTime: '',
    modTimestamp: null,
    abstract: item.abstract,
  }
}

export function FindPalette({ open, onClose, onNavigate, onNavigateDir, onScopeChange, scopeUri }: FindPaletteProps) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)
  const composingRef = useRef(false)

  const isDirMode = query === '/'
  const isRoot = !scopeUri || scopeUri === 'viking://'
  const findQuery = useVikingFind(query, !isRoot ? scopeUri : undefined)
  const data = findQuery.data

  const hasResults = data && data.total > 0
  const flatItems = useMemo(() => (data ? flattenResults(data) : []), [data])
  const activeItem = flatItems[activeIndex] ?? null
  const statQuery = useVikingFsStat(activeItem?.item.uri)

  const previewEntry = useMemo(() => {
    if (!activeItem) return null
    const base = toFsEntry(activeItem.item)
    if (statQuery.data) {
      return { ...base, size: statQuery.data.size, sizeBytes: statQuery.data.sizeBytes, modTime: statQuery.data.modTime }
    }
    return base
  }, [activeItem, statQuery.data])

  const visibleColumns = useMemo(() => {
    if (!data) return []
    return COLUMNS.filter((col) => data[col.key].length > 0)
  }, [data])

  // Focus input when opened, preserve last query
  useEffect(() => {
    if (open) {
      setActiveIndex(0)
      requestAnimationFrame(() => {
        inputRef.current?.focus()
        inputRef.current?.select()
      })
    }
  }, [open])

  // Reset index when results change
  useEffect(() => {
    setActiveIndex(0)
  }, [data])

  // Scroll active item into view
  useEffect(() => {
    if (!resultsRef.current) return
    const el = resultsRef.current.querySelector('[data-active="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  // Build column-grouped index for left/right navigation
  const columnGroups = useMemo(() => {
    if (visibleColumns.length <= 1) return null
    return visibleColumns.map((col) =>
      flatItems.filter((fi) => fi.type === col.type).map((fi) => fi.flatIndex),
    )
  }, [visibleColumns, flatItems])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (composingRef.current) return
      if (isDirMode) {
        // Dir mode handles its own keys via DirBrowser
        if (e.key === 'Escape') { e.preventDefault(); onClose() }
        return
      }
      switch (e.key) {
        case 'ArrowDown': {
          e.preventDefault()
          if (columnGroups && activeItem) {
            // Move within current column
            const colIdx = columnGroups.findIndex((g) => g.includes(activeIndex))
            if (colIdx >= 0) {
              const col = columnGroups[colIdx]
              const posInCol = col.indexOf(activeIndex)
              if (posInCol < col.length - 1) setActiveIndex(col[posInCol + 1])
            }
          } else {
            setActiveIndex((i) => Math.min(i + 1, flatItems.length - 1))
          }
          break
        }
        case 'ArrowUp': {
          e.preventDefault()
          if (columnGroups && activeItem) {
            const colIdx = columnGroups.findIndex((g) => g.includes(activeIndex))
            if (colIdx >= 0) {
              const col = columnGroups[colIdx]
              const posInCol = col.indexOf(activeIndex)
              if (posInCol > 0) setActiveIndex(col[posInCol - 1])
            }
          } else {
            setActiveIndex((i) => Math.max(i - 1, 0))
          }
          break
        }
        case 'ArrowRight': {
          e.preventDefault()
          if (columnGroups) {
            const colIdx = columnGroups.findIndex((g) => g.includes(activeIndex))
            if (colIdx >= 0 && colIdx < columnGroups.length - 1) {
              // Jump to same row position in next column, or last item
              const posInCol = columnGroups[colIdx].indexOf(activeIndex)
              const nextCol = columnGroups[colIdx + 1]
              setActiveIndex(nextCol[Math.min(posInCol, nextCol.length - 1)])
            }
          }
          break
        }
        case 'ArrowLeft': {
          e.preventDefault()
          if (columnGroups) {
            const colIdx = columnGroups.findIndex((g) => g.includes(activeIndex))
            if (colIdx > 0) {
              const posInCol = columnGroups[colIdx].indexOf(activeIndex)
              const prevCol = columnGroups[colIdx - 1]
              setActiveIndex(prevCol[Math.min(posInCol, prevCol.length - 1)])
            }
          }
          break
        }
        case 'Enter':
          e.preventDefault()
          if (activeItem) {
            onNavigate(activeItem.item.uri)
            onClose()
          }
          break
        case 'Escape':
          e.preventDefault()
          onClose()
          break
      }
    },
    [flatItems, activeItem, activeIndex, columnGroups, onNavigate, onClose],
  )

  if (!open) return null

  const showPreview = activeItem !== null
  const paletteMaxWidth = showPreview ? 'max-w-4xl' : visibleColumns.length > 1 ? 'max-w-3xl' : 'max-w-lg'

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[12vh]" role="dialog" aria-modal="true" aria-label="搜索">
      <div className="animate-palette-backdrop absolute inset-0 bg-background/60 backdrop-blur-sm" role="presentation" onClick={onClose} />

      <div
        className={cn('animate-palette-in relative flex w-full flex-col overflow-hidden rounded-xl border bg-background shadow-2xl shadow-black/20 transition-[max-width] duration-300', paletteMaxWidth)}
        onKeyDown={handleKeyDown}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 border-b px-4">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            type="text"
            placeholder="搜索... 输入 / 浏览目录"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onCompositionStart={() => { composingRef.current = true }}
            onCompositionEnd={() => { composingRef.current = false }}
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/70"
          />
          {query && (
            <button
              type="button"
              className="rounded-md p-1 text-muted-foreground/70 transition-colors hover:text-foreground"
              onClick={() => setQuery('')}
            >
              <X className="size-3.5" />
            </button>
          )}
          <span className="flex items-center gap-1 text-xs text-muted-foreground/70">
            {isRoot ? '全局' : (
              <><FolderOpen className="size-3" />{scopeUri?.split('/').filter(Boolean).pop()}</>
            )}
          </span>
        </div>

        {/* Body */}
        <div className="flex min-h-0" ref={resultsRef}>
          {isDirMode ? (
            <DirBrowser
              startUri={scopeUri || 'viking://'}
              onSelect={(uri) => {
                onScopeChange(uri)
                setQuery('')
              }}
              onCancel={() => setQuery('')}
            />
          ) : (
            <>
              {/* Results area */}
              <div className={cn('min-h-0 flex-1 overflow-hidden', showPreview && 'border-r')}>
                {!query.trim() ? (
                  <div className="animate-palette-in flex flex-col items-center gap-3 px-4 py-12 text-center">
                    <Search className="size-6 text-muted-foreground/30" />
                    <div>
                      <p className="text-sm text-muted-foreground/70">语义搜索知识库</p>
                      <p className="mt-1 text-xs text-muted-foreground/50">
                        输入 <kbd className="rounded border border-border bg-muted/50 px-1 py-0.5 font-mono text-[11px] text-foreground/70">/</kbd> 浏览目录结构
                      </p>
                    </div>
                  </div>
                ) : findQuery.isLoading ? (
                  <LoadingHint />
                ) : findQuery.error ? (
                  <div className="px-4 py-6 text-center text-xs text-destructive">搜索出错</div>
                ) : !hasResults ? (
                  <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                    <Search className="size-5 text-muted-foreground/25" />
                    <p className="text-sm text-muted-foreground/60">没有找到匹配的内容</p>
                    <p className="text-xs text-muted-foreground/40">试试换个关键词？</p>
                  </div>
                ) : visibleColumns.length > 1 ? (
                  <div className="flex h-80 divide-x overflow-hidden">
                    {visibleColumns.map((col) => {
                      const colItems = flatItems.filter((fi) => fi.type === col.type)
                      return (
                        <ResultColumn
                          key={col.key}
                          type={col.type}
                          items={colItems}
                          activeIndex={activeIndex}
                          onSelect={(fi) => { onNavigate(fi.item.uri); onClose() }}
                          onOpenDir={(fi) => { onNavigateDir(getParentUri(fi.item.uri)); onClose() }}
                        />
                      )
                    })}
                  </div>
                ) : (
                  <div className="max-h-80 overflow-y-auto overscroll-contain">
                    <ResultColumn
                      type={visibleColumns[0]?.type ?? 'resource'}
                      items={flatItems}
                      activeIndex={activeIndex}
                      onSelect={(fi) => { onNavigate(fi.item.uri); onClose() }}
                      onOpenDir={(fi) => { onNavigateDir(getParentUri(fi.item.uri)); onClose() }}
                      hideHeader
                    />
                  </div>
                )}
              </div>

              {/* Preview pane */}
              {showPreview && (
                <div className="animate-palette-preview flex h-80 w-80 flex-col overflow-hidden">
                  <FilePreview
                    file={previewEntry}
                    onClose={() => setActiveIndex(-1)}
                    showCloseButton={false}
                  />
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        {isDirMode ? (
          <div className="flex items-center gap-3 border-t px-4 py-2 text-xs text-muted-foreground/70">
            <span><kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">↑↓</kbd> 选择</span>
            <span><kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">←→</kbd> 层级</span>
            <span><kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">↵</kbd> 确定</span>
            <span><kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">esc</kbd> 取消</span>
          </div>
        ) : hasResults && (
          <div className="flex items-center gap-3 border-t px-4 py-2 text-xs text-muted-foreground/70">
            <span><kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">↑↓</kbd> 导航</span>
            {visibleColumns.length > 1 && (
              <span><kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">←→</kbd> 切栏</span>
            )}
            <span><kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">↵</kbd> 打开</span>
            <span><kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">esc</kbd> 关闭</span>
            <span className="ml-auto tabular-nums">{data!.total} 个结果</span>
          </div>
        )}
      </div>
    </div>
  )
}

/* ---- Loading Hint ---- */

const LOADING_HINTS = [
  '正在检索向量索引...',
  '扫描知识库层级结构...',
  '匹配语义相关内容...',
  '对结果重排序...',
]

function LoadingHint() {
  const [hintIndex, setHintIndex] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => {
      setHintIndex((i) => (i + 1) % LOADING_HINTS.length)
    }, 1500)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="flex flex-col items-center gap-3 py-12">
      <Loader2 className="size-5 animate-spin text-muted-foreground/50" />
      <p key={hintIndex} className="animate-palette-in text-xs text-muted-foreground/60">
        {LOADING_HINTS[hintIndex]}
      </p>
    </div>
  )
}

function ResultColumn({
  type,
  items,
  activeIndex,
  onSelect,
  onOpenDir,
  hideHeader,
}: {
  type: FindContextType
  items: FlatItem[]
  activeIndex: number
  onSelect: (fi: FlatItem) => void
  onOpenDir: (fi: FlatItem) => void
  hideHeader?: boolean
}) {
  const meta = TYPE_META[type]
  const Icon = meta.icon

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      {!hideHeader && (
        <div className={cn('flex items-center gap-1.5 px-3 py-2 text-xs font-semibold uppercase tracking-wider', meta.bgColor)}>
          <Icon className={cn('size-3', meta.color)} />
          <span className={meta.color}>{meta.label}</span>
          <span className="ml-auto tabular-nums text-muted-foreground/70">{items.length}</span>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {items.map((fi, i) => {
          const { name, parent } = displayName(fi.item.uri)
          const isActive = fi.flatIndex === activeIndex

          return (
            <button
              key={`${fi.item.uri}-${fi.flatIndex}`}
              type="button"
              data-active={isActive}
              className={cn(
                'animate-palette-row group relative flex w-full items-start gap-2 px-3 py-2 text-left text-sm transition-colors',
                isActive ? 'bg-primary/8 text-foreground' : 'text-foreground/80 hover:bg-muted/40',
              )}
              style={{ animationDelay: `${i * 30}ms` }}
              onClick={() => onSelect(fi)}
            >
              {isActive && (
                <span className="absolute inset-y-0 left-0 w-0.5 rounded-r bg-primary" />
              )}
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{name}</div>
                <div className="mt-0.5 truncate text-xs text-muted-foreground/80">{parent}</div>
              </div>
              <span
                role="button"
                tabIndex={-1}
                title="打开所在目录"
                className="shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground group-hover:opacity-100 data-[active=true]:opacity-100"
                data-active={isActive}
                onClick={(e) => { e.stopPropagation(); onOpenDir(fi) }}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); onOpenDir(fi) } }}
              >
                <FolderOpen className="size-3.5" />
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

