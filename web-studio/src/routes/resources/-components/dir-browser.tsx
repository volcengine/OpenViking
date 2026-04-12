import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, Folder, Loader2 } from 'lucide-react'

import { cn } from '#/lib/utils'

import { normalizeDirUri, fileNameFromUri, parentUri } from '../-lib/normalize'
import { useVikingFsList } from '../-hooks/viking-fm'
import type { VikingFsEntry } from '../-types/viking-fm'

interface DirBrowserProps {
  startUri: string
  onSelect: (uri: string) => void
  onCancel: () => void
}

export function DirBrowser({ startUri, onSelect, onCancel }: DirBrowserProps) {
  const [focusUri, setFocusUri] = useState(() => normalizeDirUri(startUri))
  const [activeCol, setActiveCol] = useState<'left' | 'right'>('left')
  const [leftIndex, setLeftIndex] = useState(0)
  const [rightIndex, setRightIndex] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)

  const leftParent = parentUri(focusUri)
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

  useEffect(() => {
    const idx = leftDirs.findIndex((e) => normalizeDirUri(e.uri) === focusUri)
    if (idx >= 0) setLeftIndex(idx)
  }, [leftDirs, focusUri])

  useEffect(() => {
    setRightIndex(0)
  }, [focusUri])

  useEffect(() => {
    if (!containerRef.current) return
    const el = containerRef.current.querySelector('[data-active="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  }, [leftIndex, rightIndex, activeCol])

  /** Called by parent (FindPalette) via onKeyDown forwarding */
  const handleKey = (key: string): boolean => {
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
        onSelect(selectedUri)
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

  const isLoading = leftQuery.isLoading

  return (
    <div ref={containerRef} className="flex h-72 divide-x overflow-hidden">
      {isLoading ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          <DirColumn
            label={leftParent === 'viking://' ? 'viking://' : fileNameFromUri(leftParent.replace(/\/$/, ''))}
            dirs={leftDirs}
            activeIndex={activeCol === 'left' ? leftIndex : -1}
            focusedUri={focusUri}
            onSelect={(entry) => {
              const uri = normalizeDirUri(entry.uri)
              setFocusUri(uri)
              setActiveCol('left')
            }}
            onHover={(entry) => {
              const uri = normalizeDirUri(entry.uri)
              setFocusUri(uri)
              const idx = leftDirs.findIndex((e) => normalizeDirUri(e.uri) === uri)
              if (idx >= 0) setLeftIndex(idx)
            }}
          />
          <DirColumn
            label={fileNameFromUri(focusUri.replace(/\/$/, ''))}
            dirs={rightDirs}
            activeIndex={activeCol === 'right' ? rightIndex : -1}
            isLoading={rightQuery.isLoading}
            onSelect={(entry) => {
              onSelect(normalizeDirUri(entry.uri))
            }}
            onHover={(_, i) => {
              setRightIndex(i)
              setActiveCol('right')
            }}
          />
        </>
      )}
    </div>
  )
}

function DirColumn({
  label,
  dirs,
  activeIndex,
  focusedUri,
  isLoading,
  onSelect,
  onHover,
}: {
  label: string
  dirs: VikingFsEntry[]
  activeIndex: number
  focusedUri?: string
  isLoading?: boolean
  onSelect: (entry: VikingFsEntry) => void
  onHover: (entry: VikingFsEntry, index: number) => void
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-1.5 border-b bg-muted/30 px-3 py-1.5 text-xs font-medium text-muted-foreground">
        <Folder className="size-3" />
        <span className="truncate">{label}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {isLoading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          </div>
        ) : dirs.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-muted-foreground/70">空目录</div>
        ) : (
          dirs.map((entry, i) => {
            const isActive = i === activeIndex
            const isFocused = focusedUri != null && normalizeDirUri(entry.uri) === focusedUri

            return (
              <button
                key={entry.uri}
                type="button"
                data-active={isActive}
                className={cn(
                  'flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors',
                  isActive
                    ? 'bg-primary/10 text-foreground'
                    : isFocused
                      ? 'bg-muted/60 text-foreground'
                      : 'text-foreground/80 hover:bg-muted/40',
                )}
                onClick={() => onSelect(entry)}
                onMouseEnter={() => onHover(entry, i)}
              >
                <Folder className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate">{entry.name}</span>
                <ChevronRight className="ml-auto size-3 shrink-0 text-muted-foreground/50" />
              </button>
            )
          })
        )}
      </div>
    </div>
  )
}
