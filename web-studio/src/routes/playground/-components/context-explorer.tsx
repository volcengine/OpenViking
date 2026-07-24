import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChevronRightIcon,
  ClipboardListIcon,
  FileTextIcon,
  FolderIcon,
  FolderTreeIcon,
  Loader2Icon,
  PlusIcon,
  RefreshCcwIcon,
  SearchIcon,
} from 'lucide-react'

import { Button } from '#/components/ui/button'
import { cn } from '#/lib/utils'
import { useVikingFsList } from '#/routes/resources/-hooks/viking-fm'
import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'

import { sortTreeEntries, visibleContextEntries } from '../-lib/utils'
import { ROOT_URI } from '../-lib/constants'

const TREE_INDENT_WIDTH = 16
const TREE_ROW_PADDING = 6
const TREE_GUIDE_OFFSET = 8
const TREE_CHILD_CONTENT_OFFSET = 26

export function ContextExplorerHeader({
  activeTaskCount,
  hasActiveTasks,
  hasTasks,
  isRefreshing,
  isRefreshingTasks,
  onAddResource,
  onOpenProcessingTasks,
  onOpenSearch,
  onRefresh,
}: {
  activeTaskCount: number
  hasActiveTasks: boolean
  hasTasks: boolean
  isRefreshing: boolean
  isRefreshingTasks: boolean
  onAddResource: () => void
  onOpenProcessingTasks: () => void
  onOpenSearch: () => void
  onRefresh: () => void
}) {
  const { t } = useTranslation(['playground', 'resources'])
  const showProcessingTasks = hasTasks || isRefreshingTasks

  return (
    <div className="border-b px-3 py-3">
      <div className="flex items-center gap-2">
        <div className="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <FolderTreeIcon className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">{t('explorer.title')}</div>
        </div>
        {showProcessingTasks ? (
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            className="relative"
            title={t('processingTasks.title', { ns: 'resources' })}
            onClick={onOpenProcessingTasks}
          >
            <ClipboardListIcon
              className={cn(
                'size-4',
                (hasActiveTasks || isRefreshingTasks) && 'text-primary',
              )}
            />
            {activeTaskCount > 0 ? (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold leading-none text-primary-foreground">
                {activeTaskCount}
              </span>
            ) : null}
          </Button>
        ) : null}
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          title={t('explorer.search')}
          onClick={onOpenSearch}
        >
          <SearchIcon className="size-4" />
        </Button>
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          title={t('explorer.addResource')}
          onClick={onAddResource}
        >
          <PlusIcon className="size-4" />
        </Button>
        <Button
          type="button"
          size="icon-sm"
          variant="ghost"
          title={t('explorer.refresh')}
          onClick={onRefresh}
        >
          <RefreshCcwIcon
            className={cn('size-4', isRefreshing && 'animate-spin')}
          />
        </Button>
      </div>
    </div>
  )
}

const NAMESPACE_DESCRIPTION_KEYS: Partial<
  Record<
    string,
    | 'explorer.namespaces.agent'
    | 'explorer.namespaces.resources'
    | 'explorer.namespaces.user'
  >
> = {
  agent: 'explorer.namespaces.agent',
  resources: 'explorer.namespaces.resources',
  user: 'explorer.namespaces.user',
} as const

const NAMESPACE_ORDER: Partial<Record<string, number>> = {
  user: 0,
  resources: 1,
  agent: 2,
}

export function ContextTree({
  currentUri,
  expandedKeys,
  onExpandedKeysChange,
  onSelectDirectory,
  onSelectFile,
  selectedFileUri,
}: {
  currentUri: string
  expandedKeys: Set<string>
  onExpandedKeysChange: (next: Set<string>) => void
  onSelectDirectory: (entry: VikingFsEntry) => void
  onSelectFile: (entry: VikingFsEntry) => void
  selectedFileUri?: string | null
}) {
  const { t } = useTranslation('playground')
  const rootQuery = useVikingFsList(ROOT_URI, {
    output: 'agent',
    showAllHidden: true,
    nodeLimit: 200,
    sortBy: 'name',
    sortOrder: 'asc',
  })
  const namespaces = useMemo(
    () =>
      [...visibleContextEntries(rootQuery.data?.entries ?? [])].sort(
        (left, right) => {
          const leftOrder =
            NAMESPACE_ORDER[left.name.toLowerCase()] ?? Number.POSITIVE_INFINITY
          const rightOrder =
            NAMESPACE_ORDER[right.name.toLowerCase()] ??
            Number.POSITIVE_INFINITY
          return leftOrder - rightOrder || left.name.localeCompare(right.name)
        },
      ),
    [rootQuery.data?.entries],
  )

  return (
    <div className="h-full overflow-auto px-2 py-2 font-mono">
      {rootQuery.isLoading ? (
        <div className="flex h-7 items-center gap-2 px-1.5 text-xs text-muted-foreground">
          <Loader2Icon className="size-3 animate-spin" />
          {t('explorer.loading')}
        </div>
      ) : rootQuery.isError ? (
        <div className="px-1.5 text-xs leading-7 text-destructive">
          {t('dirBrowser.error')}
        </div>
      ) : namespaces.length === 0 ? (
        <div className="px-1.5 text-xs leading-7 text-muted-foreground/60">
          {t('explorer.empty')}
        </div>
      ) : (
        namespaces.map((entry) => {
          const normalizedName = entry.name.toLowerCase()
          const descriptionKey = NAMESPACE_DESCRIPTION_KEYS[normalizedName]

          return (
            <ContextTreeNode
              key={entry.uri}
              currentUri={currentUri}
              entry={{
                ...entry,
                name: entry.name,
                abstract: descriptionKey ? t(descriptionKey) : entry.abstract,
              }}
              expandedKeys={expandedKeys}
              level={0}
              onExpandedKeysChange={onExpandedKeysChange}
              onSelectDirectory={onSelectDirectory}
              onSelectFile={onSelectFile}
              selectedFileUri={selectedFileUri}
            />
          )
        })
      )}
    </div>
  )
}

export function PlaygroundResizeHandle({
  active,
  label,
  onPointerDown,
}: {
  active: boolean
  label: string
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void
}) {
  return (
    <div
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      data-active={active}
      className="group hidden w-2 shrink-0 cursor-col-resize touch-none items-center justify-center border-x border-transparent transition-colors hover:bg-primary/10 active:bg-primary/15 data-[active=true]:bg-primary/15 lg:flex"
      onPointerDown={onPointerDown}
    >
      <span className="h-full w-px bg-border transition-colors group-hover:bg-primary/60 group-data-[active=true]:bg-primary" />
    </div>
  )
}

export function ContextTreeNode({
  currentUri,
  entry,
  expandedKeys,
  level,
  onExpandedKeysChange,
  onSelectDirectory,
  onSelectFile,
  selectedFileUri,
}: {
  currentUri: string
  entry: VikingFsEntry
  expandedKeys: Set<string>
  level: number
  onExpandedKeysChange: (next: Set<string>) => void
  onSelectDirectory: (entry: VikingFsEntry) => void
  onSelectFile: (entry: VikingFsEntry) => void
  selectedFileUri?: string | null
}) {
  const { t } = useTranslation('playground')
  const isOpen = expandedKeys.has(entry.uri)
  const isFileSelected = !entry.isDir && selectedFileUri === entry.uri
  const isDirSelected =
    entry.isDir && currentUri === entry.uri && !selectedFileUri
  const isSelected = isDirSelected || isFileSelected
  const namespaceHint = level === 0 ? entry.abstract : ''
  const rowRef = useRef<HTMLDivElement>(null)
  const shouldLoadChildren = entry.isDir && isOpen
  const listQuery = useVikingFsList(
    entry.uri,
    {
      output: 'agent',
      showAllHidden: true,
      nodeLimit: 200,
    },
    shouldLoadChildren,
  )
  const children = useMemo(
    () => sortTreeEntries(visibleContextEntries(listQuery.data?.entries ?? [])),
    [listQuery.data?.entries],
  )

  useEffect(() => {
    if (!isSelected) return

    window.requestAnimationFrame(() => {
      rowRef.current?.scrollIntoView({
        block: 'center',
        inline: 'nearest',
      })
    })
  }, [entry.uri, isSelected])

  const toggle = useCallback(() => {
    if (!entry.isDir) return
    const next = new Set(expandedKeys)
    if (isOpen) next.delete(entry.uri)
    else next.add(entry.uri)
    onExpandedKeysChange(next)
  }, [entry.isDir, entry.uri, expandedKeys, isOpen, onExpandedKeysChange])

  const select = useCallback(() => {
    if (entry.isDir) {
      onSelectDirectory(entry)
      if (!isOpen || isSelected) {
        toggle()
      }
    } else {
      onSelectFile(entry)
    }
  }, [entry, isOpen, isSelected, onSelectDirectory, onSelectFile, toggle])

  return (
    <div className="relative min-w-0">
      <TreeIndentGuides level={level} />
      <div
        ref={rowRef}
        className={cn(
          'group relative z-10 flex h-7 cursor-pointer select-none items-center gap-1.5 rounded-md px-1.5 text-xs transition-colors',
          isSelected
            ? 'bg-muted text-foreground'
            : 'text-muted-foreground hover:bg-muted/55 hover:text-foreground',
        )}
        style={{ paddingLeft: treeRowPadding(level) }}
        onClick={select}
      >
        {entry.isDir ? (
          <button
            type="button"
            className="inline-flex size-4 shrink-0 items-center justify-center rounded-sm text-muted-foreground transition-colors group-hover:text-foreground"
            onClick={(event) => {
              event.stopPropagation()
              toggle()
            }}
          >
            <ChevronRightIcon
              className={cn(
                'size-3 transition-transform',
                isOpen && 'rotate-90',
              )}
            />
          </button>
        ) : (
          <span className="size-4 shrink-0" />
        )}
        {entry.isDir ? (
          <FolderIcon
            className={cn(
              'size-4 shrink-0',
              isOpen ? 'text-primary/80' : 'text-muted-foreground',
            )}
          />
        ) : (
          <FileTextIcon className="size-4 shrink-0 text-muted-foreground" />
        )}
        <span
          className={cn(
            'truncate',
            namespaceHint ? 'shrink-0 text-foreground' : 'min-w-0 flex-1',
          )}
        >
          {entry.name}
        </span>
        {namespaceHint ? (
          <span className="min-w-0 flex-1 truncate font-sans text-xs text-muted-foreground">
            {namespaceHint}
          </span>
        ) : null}
        {entry.name === '_abstract.md' ? (
          <span className="shrink-0 rounded bg-muted px-1 font-sans text-[10px] text-muted-foreground">
            {t('explorer.abstractLevel')}
          </span>
        ) : entry.name === '_overview.md' ? (
          <span className="shrink-0 rounded bg-muted px-1 font-sans text-[10px] text-muted-foreground">
            {t('explorer.overviewLevel')}
          </span>
        ) : null}
      </div>

      {entry.isDir && isOpen ? (
        <div className="relative min-w-0">
          {listQuery.isLoading ? (
            <div
              className="relative flex h-7 items-center gap-2 px-1.5 text-xs text-muted-foreground"
              style={{ paddingLeft: treeChildContentPadding(level) }}
            >
              <TreeIndentGuides level={level + 1} />
              <Loader2Icon className="size-3 animate-spin" />
              {t('explorer.loading')}
            </div>
          ) : children.length > 0 ? (
            children.map((child) => (
              <ContextTreeNode
                key={child.uri}
                currentUri={currentUri}
                entry={child}
                expandedKeys={expandedKeys}
                level={level + 1}
                onExpandedKeysChange={onExpandedKeysChange}
                onSelectDirectory={onSelectDirectory}
                onSelectFile={onSelectFile}
                selectedFileUri={selectedFileUri}
              />
            ))
          ) : (
            <div
              className="relative h-7 px-1.5 text-xs leading-7 text-muted-foreground/60"
              style={{ paddingLeft: treeChildContentPadding(level) }}
            >
              <TreeIndentGuides level={level + 1} />
              {t('explorer.empty')}
            </div>
          )}
        </div>
      ) : null}
    </div>
  )
}

export function TreeIndentGuides({ level }: { level: number }) {
  if (level <= 0) return null

  return (
    <div className="pointer-events-none absolute inset-y-0 left-0 right-0 z-0">
      {Array.from({ length: level }, (_, index) => (
        <span
          key={index}
          className="absolute bottom-0 top-0 w-px bg-border/70"
          style={{ left: treeGuideLeft(index) }}
        />
      ))}
    </div>
  )
}

export function treeGuideLeft(index: number): string {
  return `${TREE_ROW_PADDING + index * TREE_INDENT_WIDTH + TREE_GUIDE_OFFSET}px`
}

export function treeRowPadding(level: number): string {
  return `${level * TREE_INDENT_WIDTH + TREE_ROW_PADDING}px`
}

export function treeChildContentPadding(level: number): string {
  return `${(level + 1) * TREE_INDENT_WIDTH + TREE_CHILD_CONTENT_OFFSET}px`
}

export function PanelTab({
  active,
  icon: Icon,
  label,
  onClick,
}: {
  active: boolean
  icon: typeof FolderIcon
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={cn(
        'inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-xs font-medium transition-colors',
        active
          ? 'bg-foreground text-background shadow-sm'
          : 'text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
      onClick={onClick}
    >
      <Icon className="size-3.5" />
      {label}
    </button>
  )
}
