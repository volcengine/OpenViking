import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronRight, FileText, Folder, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { cn } from '#/lib/utils'
import { useTransientScrollbar } from '#/hooks/use-transient-scrollbar'

import { normalizeDirUri, fileNameFromUri, parentUri } from '../-lib/normalize'
import { useVikingFsList } from '../-hooks/viking-fm'
import type { VikingFsEntry } from '../-types/viking-fm'
import { FilePreview } from './file-preview'

interface DirBrowserProps {
  startUri: string
  onConfirm: (uri: string) => void
  onCancel: () => void
}

export function DirBrowser({ startUri, onConfirm, onCancel }: DirBrowserProps) {
  const { t } = useTranslation('resources')
  const [focusUri, setFocusUri] = useState(() => normalizeDirUri(startUri))
  const [activeCol, setActiveCol] = useState<'left' | 'right'>('left')
  const [leftIndex, setLeftIndex] = useState(0)
  const [rightIndex, setRightIndex] = useState(0)
  const [selectedFile, setSelectedFile] = useState<VikingFsEntry | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const leftParent = parentUri(focusUri)
  const isRootFocus = focusUri === 'viking://'
  const leftQuery = useVikingFsList(leftParent, { output: 'agent', showAllHidden: true, nodeLimit: 200 })
  const rightQuery = useVikingFsList(focusUri, { output: 'agent', showAllHidden: true, nodeLimit: 200 })

  const leftDirs = useMemo(
    () => (leftQuery.data?.entries ?? []).filter((e) => e.isDir),
    [leftQuery.data],
  )
  const rightDirs = useMemo(
    () => (rightQuery.data?.entries ?? []).filter((e) => e.isDir),
    [rightQuery.data],
  )
  const rightFiles = useMemo(
    () => (rightQuery.data?.entries ?? []).filter((e) => !e.isDir),
    [rightQuery.data],
  )

  const visibleSingleColumn = isRootFocus

  useEffect(() => {
    if (visibleSingleColumn) return
    const idx = leftDirs.findIndex((e) => normalizeDirUri(e.uri) === focusUri)
    if (idx >= 0) setLeftIndex(idx)
  }, [visibleSingleColumn, leftDirs, focusUri])

  useEffect(() => {
    setRightIndex(0)
    setSelectedFile(null)
  }, [focusUri])

  useEffect(() => {
    if (!visibleSingleColumn) return
    setActiveCol('left')
    setLeftIndex(0)
  }, [visibleSingleColumn])

  useEffect(() => {
    setFocusUri(normalizeDirUri(startUri))
    setActiveCol('left')
    setLeftIndex(0)
    setRightIndex(0)
  }, [startUri])

  useEffect(() => {
    if (!containerRef.current) return
    const el = containerRef.current.querySelector('[data-active="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  }, [leftIndex, rightIndex, activeCol])

  /** Called by parent (FindPalette) via onKeyDown forwarding */
  const handleKey = (key: string): boolean => {
    if (visibleSingleColumn) {
      switch (key) {
        case 'ArrowDown': {
          const next = Math.min(leftIndex + 1, rightDirs.length - 1)
          setLeftIndex(next)
          return true
        }
        case 'ArrowUp': {
          const next = Math.max(leftIndex - 1, 0)
          setLeftIndex(next)
          return true
        }
        case 'ArrowLeft':
          return true
        case 'ArrowRight': {
          const nextDir = rightDirs[leftIndex]
          if (nextDir) {
            setFocusUri(normalizeDirUri(nextDir.uri))
            setActiveCol('left')
          }
          return true
        }
        case 'Enter': {
          const selectedUri = rightDirs[leftIndex]
            ? normalizeDirUri(rightDirs[leftIndex].uri)
            : focusUri
          onConfirm(selectedUri)
          return true
        }
        case 'Escape': {
          onCancel()
          return true
        }
        default:
          return false
      }
    }

    switch (key) {
      case 'ArrowDown': {
        if (activeCol === 'left') {
          const next = Math.min(leftIndex + 1, leftDirs.length - 1)
          setLeftIndex(next)
          if (leftDirs[next]) setFocusUri(normalizeDirUri(leftDirs[next].uri))
        } else {
          setRightIndex((i) => Math.min(i + 1, rightDirs.length - 1))
        }
        return true
      }
      case 'ArrowUp': {
        if (activeCol === 'left') {
          const next = Math.max(leftIndex - 1, 0)
          setLeftIndex(next)
          if (leftDirs[next]) setFocusUri(normalizeDirUri(leftDirs[next].uri))
        } else {
          setRightIndex((i) => Math.max(i - 1, 0))
        }
        return true
      }
      case 'ArrowRight': {
        if (activeCol === 'left' && rightDirs.length > 0) {
          setActiveCol('right')
        } else if (activeCol === 'right' && rightDirs[rightIndex]?.isDir) {
          setFocusUri(normalizeDirUri(rightDirs[rightIndex].uri))
          setActiveCol('left')
        }
        return true
      }
      case 'ArrowLeft': {
        if (activeCol === 'right') {
          setActiveCol('left')
        } else if (leftParent !== focusUri) {
          setFocusUri(leftParent)
          setActiveCol('left')
        }
        return true
      }
      case 'Enter': {
        const selectedUri = activeCol === 'left'
          ? focusUri
          : rightDirs[rightIndex]
            ? normalizeDirUri(rightDirs[rightIndex].uri)
            : focusUri
        onConfirm(selectedUri)
        return true
      }
      case 'Escape': {
        onCancel()
        return true
      }
      default:
        return false
    }
  }

  // Expose handleKey to parent via ref-like pattern using a stable callback
  // We use a useEffect + document listener approach but scoped to when component is mounted
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (handleKey(e.key)) {
        e.preventDefault()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  })

  const isLoading = visibleSingleColumn ? rightQuery.isLoading : leftQuery.isLoading
  const canGoBack = focusUri !== 'viking://'

  const handleGoBack = () => {
    if (!canGoBack) return
    setFocusUri(leftParent)
    setActiveCol('left')
    setRightIndex(0)
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b bg-background/85 px-3 py-2 backdrop-blur-sm">
        <button
          type="button"
          onClick={handleGoBack}
          disabled={!canGoBack}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors',
            canGoBack
              ? 'text-foreground/75 hover:bg-muted hover:text-foreground'
              : 'cursor-not-allowed text-muted-foreground/45',
          )}
        >
          <ArrowLeft className="size-3.5" />
          <span>{t('dirBrowser.back')}</span>
        </button>
        <div className="max-w-[55%] truncate rounded-md bg-blue-500/10 px-2.5 py-1 text-sm font-semibold text-blue-700 dark:text-blue-300">
          {focusUri}
        </div>
      </div>
      <div
        ref={containerRef}
        className="flex min-h-0 flex-1 min-w-0 justify-start overflow-hidden bg-[linear-gradient(180deg,color-mix(in_oklch,var(--muted)_35%,transparent),transparent_18%)]"
      >
      {isLoading ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : (
        visibleSingleColumn ? (
          <DirColumn
            className="min-w-0 flex-1"
            label="viking://"
            dirs={rightDirs}
            activeIndex={leftIndex}
            t={t}
            onSelect={(entry) => {
              setFocusUri(normalizeDirUri(entry.uri))
              setActiveCol('left')
              setSelectedFile(null)
            }}
          />
        ) : (
          <>
            <DirColumn
              className="w-[clamp(15rem,28vw,18rem)] shrink-0"
              label={leftParent === 'viking://' ? 'viking://' : fileNameFromUri(leftParent.replace(/\/$/, ''))}
              dirs={leftDirs}
              activeIndex={activeCol === 'left' ? leftIndex : -1}
              focusedUri={focusUri}
              t={t}
              onSelect={(entry) => {
                const uri = normalizeDirUri(entry.uri)
                setFocusUri(uri)
                setActiveCol('left')
                setSelectedFile(null)
              }}
            />
            <DirColumn
              className="min-w-0 flex-1"
              label={fileNameFromUri(focusUri.replace(/\/$/, ''))}
              dirs={rightDirs}
              files={rightFiles}
              activeIndex={activeCol === 'right' ? rightIndex : -1}
              isLoading={rightQuery.isLoading}
              t={t}
              onSelect={(entry) => {
                setFocusUri(normalizeDirUri(entry.uri))
                setActiveCol('right')
                setSelectedFile(null)
              }}
              selectedFileUri={selectedFile?.uri}
              onSelectFile={(entry) => {
                setSelectedFile(entry)
                setActiveCol('right')
              }}
            />
            {selectedFile && (
              <div className="flex h-full w-[28rem] min-w-0 flex-col overflow-hidden bg-background">
                <FilePreview
                  file={selectedFile}
                  onClose={() => setSelectedFile(null)}
                  showCloseButton={false}
                />
              </div>
            )}
          </>
        )
      )}
      </div>
    </div>
  )
}

function DirColumn({
  className,
  label,
  dirs,
  files = [],
  activeIndex,
  focusedUri,
  isLoading,
  selectedFileUri,
  t,
  onSelect,
  onSelectFile,
}: {
  className?: string
  label: string
  dirs: VikingFsEntry[]
  files?: VikingFsEntry[]
  activeIndex: number
  focusedUri?: string
  isLoading?: boolean
  selectedFileUri?: string
  t: (key: string) => string
  onSelect: (entry: VikingFsEntry) => void
  onSelectFile?: (entry: VikingFsEntry) => void
}) {
  const { isScrolling, onScroll } = useTransientScrollbar()

  return (
    <div className={cn('flex min-w-0 flex-col overflow-hidden border-r last:border-r-0', className)}>
      <div className="flex min-h-11 items-center gap-1.5 border-b bg-blue-500/8 px-3 py-2 text-xs font-semibold tracking-[0.08em] text-blue-700/80 uppercase backdrop-blur-sm dark:text-blue-300/85">
        <Folder className="size-3.5 text-blue-600/75 dark:text-blue-300/75" />
        <span className="truncate normal-case tracking-normal text-sm font-semibold text-blue-700/90 dark:text-blue-200">{label}</span>
      </div>
      <div
        className="scrollbar-fade min-h-0 flex-1 overflow-y-auto overscroll-contain"
        data-scrolling={isScrolling || undefined}
        onScroll={onScroll}
      >
        {isLoading ? (
          <div className="flex h-full items-center justify-center px-4 py-10">
            <div className="inline-flex items-center gap-2 rounded-full border bg-background/70 px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
              <Loader2 className="size-3.5 animate-spin" />
              <span>{t('dirBrowser.loading')}</span>
            </div>
          </div>
        ) : dirs.length === 0 && files.length === 0 ? (
          <div className="flex h-full items-center justify-center px-6 py-10">
            <div className="max-w-[13rem] text-center">
              <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-2xl bg-muted/60 text-muted-foreground/70 shadow-inner">
                <Folder className="size-4" />
              </div>
              <p className="text-sm font-medium text-foreground/70">{t('dirBrowser.empty.title')}</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground/75">
                {t('dirBrowser.empty.subtitle')}
              </p>
            </div>
          </div>
        ) : (
          <>
            {dirs.map((entry, i) => {
              const isActive = i === activeIndex
              const isFocused = focusedUri != null && normalizeDirUri(entry.uri) === focusedUri

              return (
                <button
                  key={entry.uri}
                  type="button"
                  data-active={isActive}
                  className={cn(
                    'group relative flex w-full items-center gap-2.5 border-b border-border/40 px-3 py-2 text-left text-sm transition-colors',
                    isActive
                      ? 'bg-primary/8 text-foreground'
                      : isFocused
                        ? 'bg-muted/55 text-foreground'
                        : 'text-foreground/80 hover:bg-muted/35',
                  )}
                  onClick={() => onSelect(entry)}
                >
                  {isActive && (
                    <span className="absolute inset-y-1 left-0 w-0.5 rounded-r bg-primary" />
                  )}
                  <Folder className={cn(
                    'size-3.5 shrink-0 transition-colors',
                    isActive ? 'text-primary/80' : 'text-muted-foreground',
                  )}
                  />
                  <span className="truncate font-medium">{entry.name}</span>
                  <ChevronRight className="ml-auto size-3 shrink-0 text-muted-foreground/45 transition-transform group-hover:translate-x-0.5" />
                </button>
              )
            })}
            {files.length > 0 && (
              <div className="border-t border-border/50 bg-muted/10 px-3 py-2 text-[11px] font-medium text-muted-foreground/70">
                {t('dirBrowser.filesSection')}
              </div>
            )}
            {files.map((entry) => {
              const isSelectedFile = selectedFileUri === entry.uri

              return (
              <button
                key={entry.uri}
                type="button"
                className={cn(
                  'flex w-full items-center gap-2.5 border-b border-border/30 px-3 py-2 text-left text-sm last:border-b-0 transition-colors',
                  isSelectedFile
                    ? 'bg-blue-500/8 text-foreground'
                    : 'text-muted-foreground/80 hover:bg-muted/25 hover:text-foreground/85',
                )}
                onClick={() => onSelectFile?.(entry)}
              >
                <FileText className={cn('size-3.5 shrink-0', isSelectedFile ? 'text-blue-600/75 dark:text-blue-300/75' : 'text-muted-foreground/65')} />
                <span className="truncate">{entry.name}</span>
              </button>
              )
            })}
          </>
        )}
      </div>
    </div>
  )
}
