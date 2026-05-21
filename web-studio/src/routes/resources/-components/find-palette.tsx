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
  useVikingFsList,
  useVikingFsStat,
  useVikingFsTree,
} from '../-hooks/viking-fm'
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

const findPaletteSession = {
  inputQuery: '',
  targetUri: undefined as string | undefined,
}
const KEY_ENTER_LABEL = 'Enter'
const KEY_ESCAPE_LABEL = 'esc'

function parseScopeCommand(query: string): string | null {
  const trimmed = query.trim()
  if (!trimmed.startsWith('/')) return null

  const rawPath = trimmed.slice(1).trim()
  if (!rawPath) return null

  const normalizedPath = rawPath
    .split('/')
    .map((part) => part.trim())
    .filter(Boolean)
    .join('/')

  if (!normalizedPath) return 'viking://'

  return normalizeDirUri(`viking://${normalizedPath}`)
}

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
  const [query, setQuery] = useState(() => findPaletteSession.inputQuery)
  const [findTargetUri, setFindTargetUri] = useState(() =>
    normalizeDirUri(findPaletteSession.targetUri || scopeUri || 'viking://'),
  )
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const resultsRef = useRef<HTMLDivElement>(null)
  const composingRef = useRef(false)

  const isDirMode = query === '/'
  const scopeCommandUri = isDirMode ? null : parseScopeCommand(query)
  const trimmedQuery = query.trim()
  const hasQuery = trimmedQuery.length > 0 && !scopeCommandUri && !isDirMode
  const isRoot = findTargetUri === 'viking://'
  const searchSpec = useMemo(
    () => getResourceSearchSpec(query, findTargetUri),
    [query, findTargetUri],
  )

  const scopeValidationQuery = useVikingFsList(
    scopeCommandUri || 'viking://',
    { output: 'agent', showAllHidden: true, nodeLimit: 1 },
    Boolean(scopeCommandUri && scopeCommandUri !== 'viking://'),
  )
  const isScopeCommandValid =
    Boolean(scopeCommandUri) &&
    (scopeCommandUri === 'viking://' || scopeValidationQuery.isSuccess)

  const treeQuery = useVikingFsTree(
    searchSpec?.rootUri || 'viking://',
    { output: 'agent', showAllHidden: true, nodeLimit: 2000, levelLimit: 100 },
    hasQuery && Boolean(searchSpec),
  )

  const filteredEntries = useMemo(() => {
    if (!hasQuery || !treeQuery.data?.nodes) return []
    return filterResourceSearchEntries(treeQuery.data.nodes, searchSpec)
  }, [hasQuery, treeQuery.data?.nodes, searchSpec])

  const hasResults = filteredEntries.length > 0
  const activeEntry =
    activeIndex >= 0 ? (filteredEntries[activeIndex] ?? null) : null

  const statQuery = useVikingFsStat(activeEntry?.uri)
  const previewEntry = useMemo(() => {
    if (!activeEntry) return null
    if (statQuery.data) {
      return {
        ...activeEntry,
        size: statQuery.data.size,
        sizeBytes: statQuery.data.sizeBytes,
        modTime: statQuery.data.modTime,
      }
    }
    return activeEntry
  }, [activeEntry, statQuery.data])

  useEffect(() => {
    if (open) {
      setActiveIndex(0)
      requestAnimationFrame(() => {
        inputRef.current?.focus()
        inputRef.current?.select()
      })
    }
  }, [open])

  useEffect(() => {
    setActiveIndex(0)
  }, [filteredEntries])

  useEffect(() => {
    findPaletteSession.inputQuery = query
  }, [query])

  useEffect(() => {
    findPaletteSession.targetUri = findTargetUri
  }, [findTargetUri])

  useEffect(() => {
    if (!resultsRef.current) return
    const el = resultsRef.current.querySelector('[data-active="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (composingRef.current) return
      if (isDirMode) {
        if (e.key === 'Escape') {
          e.preventDefault()
          onClose()
        }
        return
      }
      if (scopeCommandUri) {
        switch (e.key) {
          case 'Enter':
            e.preventDefault()
            if (!isScopeCommandValid) return
            setFindTargetUri(scopeCommandUri)
            setActiveIndex(0)
            setQuery('')
            return
          case 'Escape':
            e.preventDefault()
            onClose()
            return
        }
      }
      if (!hasQuery || filteredEntries.length === 0) {
        if (e.key === 'Escape') {
          e.preventDefault()
          onClose()
        }
        return
      }
      switch (e.key) {
        case 'ArrowDown': {
          e.preventDefault()
          setActiveIndex((i) => Math.min(i + 1, filteredEntries.length - 1))
          break
        }
        case 'ArrowUp': {
          e.preventDefault()
          setActiveIndex((i) => Math.max(i - 1, 0))
          break
        }
        case 'Enter':
          e.preventDefault()
          if (activeEntry) {
            onNavigate(activeEntry.uri)
            onClose()
          }
          break
        case 'Escape':
          e.preventDefault()
          onClose()
          break
      }
    },
    [
      query,
      hasQuery,
      filteredEntries,
      activeEntry,
      onNavigate,
      onClose,
      isDirMode,
      scopeCommandUri,
      isScopeCommandValid,
    ],
  )

  if (!open) return null

  const showPreview = hasQuery && activeEntry !== null && !activeEntry.isDir
  const paletteWidth = showPreview
    ? 'w-[min(92vw,67rem)]'
    : 'w-[min(90vw,45rem)]'

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh] sm:px-6"
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
          'animate-palette-in relative flex h-[46rem] max-h-[84vh] max-w-full flex-col overflow-hidden rounded-xl border bg-background shadow-2xl shadow-black/20 transition-[width] duration-300',
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
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/70"
          />
          {query && (
            <button
              type="button"
              className="rounded-md p-1 text-muted-foreground/70 transition-colors hover:text-foreground"
              onClick={() => {
                setActiveIndex(0)
                setQuery('')
              }}
            >
              <X className="size-3.5" />
            </button>
          )}
          <span className="flex items-center gap-1 text-xs text-muted-foreground/70">
            {isRoot ? (
              t('searchPalette.scope.global')
            ) : (
              <>
                <FolderOpen className="size-3" />
                {t('searchPalette.scope.current', {
                  name: findTargetUri.split('/').filter(Boolean).pop(),
                })}
              </>
            )}
          </span>
        </div>

        {/* Body */}
        <div className="flex min-h-0 flex-1" ref={resultsRef}>
          {isDirMode ? (
            <DirBrowser
              startUri={findTargetUri}
              onConfirm={(uri) => {
                setFindTargetUri(normalizeDirUri(uri))
                setActiveIndex(0)
                setQuery('')
              }}
              onCancel={() => {
                setActiveIndex(0)
                setQuery('')
              }}
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
                {scopeCommandUri ? (
                  <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
                    <FolderOpen
                      className={cn(
                        'size-6',
                        isScopeCommandValid
                          ? 'text-blue-500/50'
                          : 'text-destructive/60',
                      )}
                    />
                    {scopeValidationQuery.isLoading ? (
                      <div>
                        <p className="text-sm font-medium text-foreground/80">
                          {t('searchPalette.scopeState.validatingTitle')}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground/75">
                          {t('searchPalette.scopeState.validatingPrefix')}{' '}
                          <span className="font-medium text-foreground/80">
                            {scopeCommandUri}
                          </span>{' '}
                          {t('searchPalette.scopeState.validatingSuffix')}
                        </p>
                      </div>
                    ) : isScopeCommandValid ? (
                      <div>
                        <p className="text-sm font-medium text-foreground/80">
                          {t('searchPalette.scopeState.switchTitle')}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground/75">
                          {t('searchPalette.scopeState.switchPrefix')}{' '}
                          <kbd className="rounded border border-border bg-muted/50 px-1 py-0.5 font-mono text-[11px] text-foreground/70">
                            {KEY_ENTER_LABEL}
                          </kbd>{' '}
                          {t('searchPalette.scopeState.switchMiddle')}{' '}
                          <span className="font-medium text-foreground/80">
                            {scopeCommandUri}
                          </span>
                        </p>
                      </div>
                    ) : (
                      <div>
                        <p className="text-sm font-medium text-destructive">
                          {t('searchPalette.scopeState.invalidTitle')}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground/75">
                          {t('searchPalette.scopeState.invalidPrefix')}{' '}
                          <span className="font-medium text-foreground/80">
                            {scopeCommandUri}
                          </span>{' '}
                          {t('searchPalette.scopeState.invalidSuffix')}
                        </p>
                      </div>
                    )}
                  </div>
                ) : !hasQuery ? (
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
                ) : treeQuery.isLoading ? (
                  <div className="flex flex-col items-center gap-3 py-12">
                    <Loader2 className="size-5 animate-spin text-muted-foreground/50" />
                    <p className="text-xs text-muted-foreground/60">
                      {t('searchPalette.scopeState.validatingTitle')}
                    </p>
                  </div>
                ) : treeQuery.error ? (
                  <div className="px-4 py-6 text-center text-xs text-destructive">
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
