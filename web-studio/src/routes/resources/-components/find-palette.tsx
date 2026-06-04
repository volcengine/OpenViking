import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  FileIcon,
  FolderIcon,
  FolderOpen,
  Loader2,
  Search,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { cn } from '#/lib/utils'
import { useTransientScrollbar } from '#/hooks/use-transient-scrollbar'

import {
  fileNameFromUri,
  normalizeDirUri,
  parentUri as getParentUri,
} from '../-lib/normalize'
import {
  filterResourceSearchEntries,
  getResourceSearchSpec,
} from '../-lib/find-search'
import {
  PALETTE_ROOT_URI,
  buildDirBrowseQuery,
  isResetGlobalCommand,
  parsePaletteMode,
} from '../-lib/palette-mode'
import {
  useVikingFsList,
  useVikingFsStat,
  useVikingFsTree,
  useDebouncedValue,
} from '../-hooks/viking-fm'
import { useListNavigation } from '../-hooks/use-list-navigation'
import type { VikingFsEntry } from '../-types/viking-fm'
import { FilePreview } from './file-preview'
import { DirBrowser } from './dir-browser'

interface FindPaletteProps {
  open: boolean
  onClose: () => void
  onNavigate: (uri: string) => void
  onNavigateDir: (uri: string) => void
  scopeUri?: string
}
const KEY_ESCAPE_LABEL = 'esc'

function displayName(uri: string): { name: string; parent: string } {
  const name = fileNameFromUri(uri)
  const dir = getParentUri(uri)
  const segments = dir.replace(/\/$/, '').split('/').filter(Boolean)
  const parent = segments.length > 1 ? segments.slice(-1)[0] : dir
  return { name, parent }
}

export function FindPalette({
  open,
  onClose,
  onNavigate,
  onNavigateDir,
  scopeUri,
}: FindPaletteProps) {
  const { t } = useTranslation('resources')
  const [query, setQuery] = useState('')
  const [findTargetUri, setFindTargetUri] = useState(() =>
    normalizeDirUri(scopeUri || PALETTE_ROOT_URI),
  )
  const inputRef = useRef<HTMLInputElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)
  const composingRef = useRef(false)

  // Single parse entry point. No component code reads `query` structurally.
  const mode = useMemo(
    () => parsePaletteMode(query, findTargetUri),
    [query, findTargetUri],
  )
  const isRoot = findTargetUri === PALETTE_ROOT_URI
  const showIdleBrowse = mode.kind === 'idle' && !isRoot

  const searchSpec = useMemo(
    () =>
      mode.kind === 'search'
        ? getResourceSearchSpec(mode.query, findTargetUri)
        : null,
    [mode, findTargetUri],
  )

  const idleBrowseQuery = useVikingFsList(
    findTargetUri,
    { output: 'agent', showAllHidden: true },
    showIdleBrowse,
  )
  const idleEntries = useMemo(
    () => (showIdleBrowse ? idleBrowseQuery.data?.entries || [] : []),
    [showIdleBrowse, idleBrowseQuery.data?.entries],
  )

  const treeQuery = useVikingFsTree(
    searchSpec?.rootUri || PALETTE_ROOT_URI,
    { output: 'agent', showAllHidden: true, nodeLimit: 2000, levelLimit: 100 },
    mode.kind === 'search' && Boolean(searchSpec),
  )
  const filteredEntries = useMemo(() => {
    if (mode.kind !== 'search' || !treeQuery.data?.nodes) return []
    return filterResourceSearchEntries(treeQuery.data.nodes, searchSpec)
  }, [mode.kind, treeQuery.data?.nodes, searchSpec])

  // Directory listing lifted up from DirBrowser so the cursor (activeIndex) and
  // keyboard handling can live in one place. DirBrowser is now a pure view.
  const dirListQuery = useVikingFsList(
    mode.kind === 'dirBrowse' ? mode.uri : PALETTE_ROOT_URI,
    { output: 'agent', showAllHidden: true, nodeLimit: 200 },
    mode.kind === 'dirBrowse',
  )
  const dirItems = useMemo(() => {
    if (mode.kind !== 'dirBrowse') return []
    const entries = dirListQuery.data?.entries ?? []
    const dirs = entries.filter((e) => e.isDir)
    const files = entries.filter((e) => !e.isDir)
    const all = [...dirs, ...files]
    if (!mode.filter) return all
    const lower = mode.filter.toLowerCase()
    return all.filter((e) => e.name.toLowerCase().includes(lower))
  }, [mode, dirListQuery.data])
  const hasResults = filteredEntries.length > 0
  const visibleEntries = useMemo(() => {
    if (mode.kind === 'dirBrowse') return dirItems
    if (hasResults) return filteredEntries
    return idleEntries
  }, [mode.kind, dirItems, hasResults, filteredEntries, idleEntries])

  const { index: activeIndex, setIndex, moveUp, moveDown, reset } =
    useListNavigation(visibleEntries.length)
  const activeEntry = activeIndex >= 0 ? (visibleEntries[activeIndex] ?? null) : null

  // Preview stat only for search / idle file cursor. dirBrowse renders its own
  // preview inside DirBrowser. Debounced so arrow-scanning doesn't storm stat.
  const statTargetUri =
    mode.kind !== 'dirBrowse' && activeEntry && !activeEntry.isDir
      ? activeEntry.uri
      : undefined
  const debouncedStatUri = useDebouncedValue(statTargetUri, 150)
  const statQuery = useVikingFsStat(debouncedStatUri)
  const previewEntry = useMemo(() => {
    if (!activeEntry) return null
    if (statQuery.data && debouncedStatUri === activeEntry.uri) {
      return {
        ...activeEntry,
        size: statQuery.data.size,
        sizeBytes: statQuery.data.sizeBytes,
        modTime: statQuery.data.modTime,
      }
    }
    return activeEntry
  }, [activeEntry, statQuery.data, debouncedStatUri])

  const focusInput = useCallback(() => {
    requestAnimationFrame(() => {
      inputRef.current?.focus()
      inputRef.current?.select()
    })
  }, [])

  useEffect(() => {
    if (open) {
      reset()
      focusInput()
    }
  }, [open, reset, focusInput])

  useEffect(() => {
    if (!open) return

    const restoreFocus = () => {
      if (document.visibilityState === 'visible') focusInput()
    }
    window.addEventListener('focus', focusInput)
    document.addEventListener('visibilitychange', restoreFocus)
    return () => {
      window.removeEventListener('focus', focusInput)
      document.removeEventListener('visibilitychange', restoreFocus)
    }
  }, [open, focusInput])

  // Cursor resets on query change (navigation / filter typing) and whenever the
  // visible list identity changes (new data arrives). Arrow moves don't touch
  // query or the list, so they don't trigger a reset.
  useEffect(() => {
    reset()
  }, [query, visibleEntries, reset])

  useEffect(() => {
    if (!resultsRef.current) return
    const el = resultsRef.current.querySelector('[data-active="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])
  // Navigation writes go through buildDirBrowseQuery — the only place that
  // turns a uri back into a query string. setQuery is the single owner.
  const enterDir = useCallback((uri: string) => {
    setQuery(buildDirBrowseQuery(uri))
  }, [])

  const goToParent = useCallback(() => {
    if (mode.kind !== 'dirBrowse') return
    const parent = getParentUri(mode.uri)
    if (parent !== mode.uri) {
      setQuery(buildDirBrowseQuery(parent))
    }
  }, [mode])

  const confirmDirScope = useCallback((uri: string) => {
    setFindTargetUri(normalizeDirUri(uri))
    setQuery('')
  }, [])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (composingRef.current) return

      // `//` + Enter resets scope to global root, regardless of mode.
      if (isResetGlobalCommand(query) && e.key === 'Enter') {
        e.preventDefault()
        setFindTargetUri(PALETTE_ROOT_URI)
        setQuery('')
        return
      }

      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
        return
      }

      if (mode.kind === 'dirBrowse') {
        switch (e.key) {
          case 'ArrowDown':
            e.preventDefault()
            moveDown()
            return
          case 'ArrowUp':
            e.preventDefault()
            moveUp()
            return
          case 'ArrowRight':
          case 'Tab':
            e.preventDefault()
            if (activeEntry?.isDir) {
              enterDir(activeEntry.uri)
            } else if (e.key === 'Tab' && activeEntry) {
              onNavigate(activeEntry.uri)
              onClose()
            }
            return
          case 'ArrowLeft':
            e.preventDefault()
            goToParent()
            return
          case 'Enter':
            e.preventDefault()
            if (activeEntry && !activeEntry.isDir) {
              onNavigate(activeEntry.uri)
              onClose()
            } else if (dirListQuery.isSuccess) {
              confirmDirScope(mode.uri)
            }
            return
        }
        return
      }

      // search / idle list navigation
      if (visibleEntries.length === 0) return
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          moveDown()
          return
        case 'ArrowUp':
          e.preventDefault()
          moveUp()
          return
        case 'Enter':
          e.preventDefault()
          if (!activeEntry) return
          if (mode.kind === 'idle' && activeEntry.isDir) {
            confirmDirScope(activeEntry.uri)
          } else {
            onNavigate(activeEntry.uri)
            onClose()
          }
          return
      }
    },
    [
      query,
      mode,
      activeEntry,
      visibleEntries.length,
      dirListQuery.isSuccess,
      moveUp,
      moveDown,
      enterDir,
      goToParent,
      confirmDirScope,
      onNavigate,
      onClose,
    ],
  )

  if (!open) return null

  const showPreview =
    mode.kind !== 'dirBrowse' && activeEntry !== null && !activeEntry.isDir
  const paletteWidth = showPreview
    ? 'w-[min(92vw,67rem)]'
    : 'w-[min(90vw,45rem)]'
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4 sm:items-start sm:px-6 sm:pt-[12vh]"
      role="dialog"
      aria-modal="true"
      aria-label={t('searchPalette.ariaLabel')}
    >
      <div
        className="animate-palette-backdrop absolute inset-0 bg-background/60 backdrop-blur-sm"
        role="presentation"
        onClick={onClose}
      />

      <div
        className={cn(
          'animate-palette-in relative flex h-[46rem] max-h-[calc(100svh-2rem)] max-w-full flex-col overflow-hidden rounded-xl border bg-background shadow-2xl shadow-black/20 transition-[width] duration-300 sm:max-h-[84vh]',
          paletteWidth,
        )}
        onKeyDown={handleKeyDown}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 border-b px-4">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            type="text"
            placeholder={t('searchPalette.placeholder')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onCompositionStart={() => {
              composingRef.current = true
            }}
            onCompositionEnd={() => {
              composingRef.current = false
            }}
            className="h-12 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground/70 md:text-sm"
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
            {isRoot ? (
              t('searchPalette.scope.global')
            ) : (
              <button
                type="button"
                className="flex items-center gap-1 rounded px-1 py-0.5 transition-colors hover:bg-muted hover:text-foreground"
                title={t('searchPalette.scope.resetToGlobal')}
                onClick={() => setFindTargetUri(PALETTE_ROOT_URI)}
              >
                <FolderOpen className="size-3" />
                {t('searchPalette.scope.current', {
                  name: findTargetUri.split('/').filter(Boolean).pop(),
                })}
                <X className="size-3" />
              </button>
            )}
          </span>
        </div>

        {/* Body */}
        <div className="flex min-h-0 flex-1" ref={resultsRef}>
          {mode.kind === 'dirBrowse' ? (
            <DirBrowser
              currentUri={mode.uri}
              items={dirItems}
              activeIndex={activeIndex}
              loading={dirListQuery.isLoading}
              errored={dirListQuery.isError}
              onCursorChange={setIndex}
              onEnterDir={enterDir}
              onOpenFile={(uri) => {
                onNavigate(uri)
                onClose()
              }}
              onGoBack={goToParent}
            />
          ) : (
            <>
              {/* Results area */}
              <div
                className={cn(
                  'min-h-0 flex-1 overflow-hidden',
                  showPreview && 'border-r',
                )}
              >
                {mode.kind === 'idle' ? (
                  showIdleBrowse && idleBrowseQuery.isError ? (
                    <div
                      role="alert"
                      className="px-4 py-6 text-center text-xs text-destructive"
                    >
                      {t('dirBrowser.error')}
                    </div>
                  ) : showIdleBrowse && idleEntries.length > 0 ? (
                    <DirResultList
                      className="h-full"
                      items={idleEntries}
                      activeIndex={activeIndex}
                      onSelect={(entry) => {
                        if (entry.isDir) {
                          confirmDirScope(entry.uri)
                        } else {
                          onNavigate(entry.uri)
                          onClose()
                        }
                      }}
                      onOpenDir={(entry) => {
                        onNavigateDir(getParentUri(entry.uri))
                        onClose()
                      }}
                    />
                  ) : (
                    <div className="animate-palette-in flex flex-col items-center gap-3 px-4 py-12 text-center">
                      <Search className="size-6 text-muted-foreground/30" />
                      <div>
                        <p className="text-sm text-muted-foreground/70">
                          {t('searchPalette.empty.title')}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground/50">
                          {t('searchPalette.browseDirHint.before')}{' '}
                          <kbd className="rounded border border-border bg-muted/50 px-1 py-0.5 font-mono text-[11px] text-foreground/70">
                            /
                          </kbd>{' '}
                          {t('searchPalette.browseDirHint.after')}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground/50">
                          {t('searchPalette.globalScopeHint.before')}{' '}
                          <kbd className="rounded border border-border bg-muted/50 px-1 py-0.5 font-mono text-[11px] text-foreground/70">
                            //
                          </kbd>{' '}
                          {t('searchPalette.globalScopeHint.after')}
                        </p>
                      </div>
                    </div>
                  )
                ) : treeQuery.isLoading ? (
                  <div className="flex flex-col items-center gap-3 py-12">
                    <Loader2 className="size-5 animate-spin text-muted-foreground/50" />
                    <p className="text-xs text-muted-foreground/60">
                      {t('searchPalette.scopeState.validatingTitle')}
                    </p>
                  </div>
                ) : treeQuery.error ? (
                  <div
                    role="alert"
                    className="px-4 py-6 text-center text-xs text-destructive"
                  >
                    {t('searchPalette.error')}
                  </div>
                ) : !hasResults ? (
                  <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                    <Search className="size-5 text-muted-foreground/25" />
                    <p className="text-sm text-muted-foreground/60">
                      {t('searchPalette.emptyResults.title')}
                    </p>
                    <p className="text-xs text-muted-foreground/40">
                      {t('searchPalette.emptyResults.subtitle')}
                    </p>
                  </div>
                ) : (
                  <DirResultList
                    className="h-full"
                    items={filteredEntries}
                    activeIndex={activeIndex}
                    onSelect={(entry) => {
                      onNavigate(entry.uri)
                      onClose()
                    }}
                    onOpenDir={(entry) => {
                      onNavigateDir(getParentUri(entry.uri))
                      onClose()
                    }}
                  />
                )}
              </div>

              {/* Preview pane */}
              {showPreview && (
                <div className="animate-palette-preview flex h-full w-[32rem] flex-col overflow-hidden">
                  <FilePreview
                    file={previewEntry}
                    onClose={() => setIndex(-1)}
                    showCloseButton={false}
                  />
                </div>
              )}
            </>
          )}
        </div>

        {mode.kind === 'dirBrowse' ? (
          <div className="flex items-center gap-3 border-t px-4 py-2 text-xs text-muted-foreground/70">
            <span>
              <kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                ↑↓
              </kbd>{' '}
              {t('searchPalette.footer.dirMode.select')}
            </span>
            <span>
              <kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                ←→
              </kbd>{' '}
              {t('searchPalette.footer.dirMode.level')}
            </span>
            <span>
              <kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                ↵
              </kbd>{' '}
              {t('searchPalette.footer.dirMode.confirm')}
            </span>
            <span>
              <kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                {KEY_ESCAPE_LABEL}
              </kbd>{' '}
              {t('searchPalette.footer.dirMode.cancel')}
            </span>
          </div>
        ) : (
          hasResults && (
            <div className="flex items-center gap-3 border-t px-4 py-2 text-xs text-muted-foreground/70">
              <span>
                <kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                  ↑↓
                </kbd>{' '}
                {t('searchPalette.footer.resultMode.navigate')}
              </span>
              <span>
                <kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                  ↵
                </kbd>{' '}
                {t('searchPalette.footer.resultMode.open')}
              </span>
              <span>
                <kbd className="rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                  {KEY_ESCAPE_LABEL}
                </kbd>{' '}
                {t('searchPalette.footer.resultMode.close')}
              </span>
              <span className="ml-auto tabular-nums">
                {t('searchPalette.footer.resultMode.count', {
                  count: filteredEntries.length,
                })}
              </span>
            </div>
          )
        )}
      </div>
    </div>
  )
}
function DirResultList({
  className,
  items,
  activeIndex,
  onSelect,
  onOpenDir,
}: {
  className?: string
  items: VikingFsEntry[]
  activeIndex: number
  onSelect: (entry: VikingFsEntry) => void
  onOpenDir: (entry: VikingFsEntry) => void
}) {
  const { t } = useTranslation('resources')
  const { isScrolling, onScroll } = useTransientScrollbar()

  return (
    <div
      className={cn(
        'scrollbar-fade min-h-0 flex-1 overflow-y-auto overscroll-contain',
        className,
      )}
      data-scrolling={isScrolling || undefined}
      onScroll={onScroll}
    >
      {items.map((entry, i) => {
        const { name, parent } = displayName(entry.uri)
        const isActive = i === activeIndex
        const EntryIcon = entry.isDir ? FolderIcon : FileIcon

        return (
          <button
            key={entry.uri}
            type="button"
            data-active={isActive}
            className={cn(
              'animate-palette-row group relative flex w-full items-start gap-3 border-b border-border/50 px-4 py-3 text-left transition-colors last:border-b-0',
              isActive
                ? 'bg-primary/8 text-foreground'
                : 'text-foreground/80 hover:bg-muted/40',
            )}
            style={{ animationDelay: `${i * 24}ms` }}
            onClick={() => onSelect(entry)}
          >
            {isActive && (
              <span className="absolute inset-y-0 left-0 w-0.5 rounded-r bg-primary" />
            )}
            <EntryIcon
              className={cn(
                'mt-0.5 size-4 shrink-0',
                entry.isDir ? 'text-blue-500/70' : 'text-muted-foreground/70',
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{name}</div>
              <div className="mt-0.5 truncate text-xs text-muted-foreground/80">
                {parent}
              </div>
            </div>
            {entry.size && (
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground/60">
                {entry.size}
              </span>
            )}
            <span
              role="button"
              tabIndex={-1}
              title={t('searchPalette.openContainingDirectory')}
              className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-foreground group-hover:opacity-100 data-[active=true]:opacity-100"
              data-active={isActive}
              onClick={(e) => {
                e.stopPropagation()
                onOpenDir(entry)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.stopPropagation()
                  onOpenDir(entry)
                }
              }}
            >
              <FolderOpen className="size-3.5" />
            </span>
          </button>
        )
      })}
    </div>
  )
}
