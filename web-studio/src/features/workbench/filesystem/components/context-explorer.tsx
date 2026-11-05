import styles from './context-explorer.module.css'
import {
  PlugIcon,
  ChevronRightIcon,
  FileTextIcon,
  FolderIcon,
  FolderOpenIcon,
  Loader2Icon,
  UploadIcon,
  RefreshCcwIcon,
  SearchIcon,
} from 'lucide-react'
import type { ComponentProps, PointerEvent as ReactPointerEvent } from 'react'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { useVikingFsList } from '#/features/workbench/resources/hooks/viking-fm'
import type { VikingFsEntry } from '#/shared/types/viking-fs'
import { cn } from '#/shared/lib/utils'
import { Button } from '#/shared/ui/button'

import { ROOT_URI } from '#/features/workbench/filesystem/lib/constants'
import {
  sortTreeEntries,
  visibleContextEntries,
} from '#/features/workbench/filesystem/lib/utils'

const TREE_INDENT_WIDTH = 18
const TREE_ROW_PADDING = 6
const TREE_GUIDE_OFFSET = 8
const TREE_CHILD_CONTENT_OFFSET = 24
const TREE_CONTROL_SIZE = 16
const TREE_CONTROL_GAP = 2

export function ContextExplorerHeader({
  isRefreshing,
  onAddResource,
  onOpenSearch,
  onRefresh,
}: {
  isRefreshing: boolean
  onAddResource?: () => void
  onOpenSearch: () => void
  onRefresh: () => void
}) {
  const { t } = useTranslation(['filesystem', 'resources'])

  return (
    <div className={styles.header}>
      <div className={styles.actions}>
        <a
          href="https://docs.openviking.ai/agent-integrations/01-overview"
          target="_blank"
          rel="noreferrer"
          className={styles.headerAction}
        >
          <PlugIcon className="size-3.5" />
          {t('ide.connectAgent')}
        </a>
        {onAddResource ? <span className="h-3 w-px bg-[#d6d5ff]" /> : null}
        {onAddResource ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className={styles.headerAction}
            onClick={onAddResource}
          >
            <UploadIcon className="size-4" />
            {t('explorer.addResource')}
          </Button>
        ) : null}
      </div>
      <div className={styles.searchRow}>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className={styles.search}
          title={t('explorer.search')}
          onClick={onOpenSearch}
        >
          <SearchIcon className="size-4" />
          <span className="truncate">{t('ide.search')}</span>
        </Button>

        <Button
          type="button"
          size="icon-sm"
          variant="outline"
          title={t('explorer.refresh')}
          className={styles.refresh}
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
    | 'explorer.namespaces.system'
    | 'explorer.namespaces.user'
  >
> = {
  agent: 'explorer.namespaces.agent',
  resources: 'explorer.namespaces.resources',
  system: 'explorer.namespaces.system',
  user: 'explorer.namespaces.user',
} as const

const DIRECTORY_HINTS: Record<string, string> = {
  memories: 'ide.hints.memories',
  experiences: 'ide.hints.experiences',
  preferences: 'ide.hints.preferences',
  entities: 'ide.hints.entities',
  events: 'ide.hints.events',
  peers: 'ide.hints.peers',
  privacy: 'ide.hints.privacy',
  sessions: 'ide.hints.sessions',
  skills: 'ide.hints.skills',
}

const NAMESPACE_LABELS: Record<string, string> = {
  agent: 'Agent',
  resources: 'Resources',
}

const NAMESPACE_ORDER: Partial<Record<string, number>> = {
  user: 0,
  resources: 2,
  agent: 1,
}

const USER_DIRECTORY_ORDER = [
  'memories',
  'peers',
  'privacy',
  'resources',
  'sessions',
  'settings',
  'skills',
  'tools',
]
const MEMORY_DIRECTORY_ORDER = [
  'experiences',
  'preferences',
  'entities',
  'events',
]

function orderContextChildren(
  uri: string,
  entries: VikingFsEntry[],
): VikingFsEntry[] {
  const order = /^viking:\/\/user\/[^/]+\/?$/.test(uri)
    ? USER_DIRECTORY_ORDER
    : /\/memories\/?$/.test(uri)
      ? MEMORY_DIRECTORY_ORDER
      : []
  const rank = (entry: VikingFsEntry) => {
    const index = entry.isDir ? order.indexOf(entry.name.toLowerCase()) : -1
    return index < 0 ? order.length : index
  }
  return sortTreeEntries(entries).sort(
    (left, right) => rank(left) - rank(right),
  )
}

export function ContextTree({
  currentUri,
  expandedKeys,
  onExpandedKeysChange,
  onSelectDirectory,
  onSelectFile,
  selectedFileUri,
  rootUri = ROOT_URI,
}: {
  currentUri: string
  expandedKeys: Set<string>
  onExpandedKeysChange: (next: Set<string>) => void
  onSelectDirectory: (entry: VikingFsEntry) => void
  onSelectFile: (entry: VikingFsEntry) => void
  selectedFileUri?: string | null
  rootUri?: string
}) {
  const { t } = useTranslation('filesystem')
  const rootQuery = useVikingFsList(rootUri, {
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
    <div className={styles.tree}>
      {rootQuery.isLoading ? (
        <div className="flex h-7 items-center gap-2 px-1.5 text-xs text-muted-foreground">
          <Loader2Icon className="size-3 animate-spin" />
          {t('explorer.loading')}
        </div>
      ) : rootQuery.isError ? (
        <div className="px-1.5 text-xs leading-7 text-destructive">
          {t('dirBrowser.error', { ns: 'resources' })}
        </div>
      ) : namespaces.length === 0 ? (
        <div className="px-1.5 text-xs leading-7 text-muted-foreground/60">
          {t('explorer.empty')}
        </div>
      ) : (
        <ul
          role="list"
          aria-label={t('explorer.title')}
          className="m-0 min-w-0 list-none p-0"
        >
          {namespaces.map((entry) => {
            const normalizedName = entry.name.toLowerCase()
            if (normalizedName === 'user')
              return (
                <UserRoots
                  key={entry.uri}
                  entry={entry}
                  currentUri={currentUri}
                  expandedKeys={expandedKeys}
                  onExpandedKeysChange={onExpandedKeysChange}
                  onSelectDirectory={onSelectDirectory}
                  onSelectFile={onSelectFile}
                  selectedFileUri={selectedFileUri}
                />
              )
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
          })}
        </ul>
      )}
    </div>
  )
}

function UserRoots(
  props: Omit<ComponentProps<typeof ContextTreeNode>, 'level'>,
) {
  const { t } = useTranslation('filesystem')
  const query = useVikingFsList(props.entry.uri, {
    output: 'agent',
    showAllHidden: true,
    nodeLimit: 200,
  })
  const users = visibleContextEntries(query.data?.entries ?? []).filter(
    (entry) => entry.isDir,
  )
  if (!query.isSuccess || users.length === 0)
    return <ContextTreeNode {...props} level={0} />
  return (
    <>
      {users.map((entry) => (
        <ContextTreeNode
          {...props}
          key={entry.uri}
          entry={{ ...entry, abstract: t('explorer.namespaces.user') }}
          level={0}
        />
      ))}
    </>
  )
}

export function FilesystemResizeHandle({
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
      className="group relative z-10 hidden -mx-1.5 w-1.5 shrink-0 cursor-col-resize touch-none items-center justify-center border-x border-transparent transition-colors hover:bg-primary/10 active:bg-primary/15 data-[active=true]:bg-primary/15 lg:flex"
      onPointerDown={onPointerDown}
    >
      <span className="h-full w-px bg-transparent transition-colors group-hover:bg-primary/60 group-data-[active=true]:bg-primary" />
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
  const { t } = useTranslation('filesystem')
  const displayName =
    level === 0
      ? (NAMESPACE_LABELS[entry.name.toLowerCase()] ?? entry.name)
      : entry.name
  const isOpen = expandedKeys.has(entry.uri)
  const isFileSelected = !entry.isDir && selectedFileUri === entry.uri
  const isDirSelected =
    entry.isDir && currentUri === entry.uri && !selectedFileUri
  const isSelected = isDirSelected || isFileSelected
  const namespaceHint = entry.isDir
    ? DIRECTORY_HINTS[entry.name.toLowerCase()]
      ? t(DIRECTORY_HINTS[entry.name.toLowerCase()])
      : level === 0
        ? entry.abstract
        : ''
    : ''
  const rowRef = useRef<HTMLDivElement>(null)
  const disclosureLabel = entry.isDir
    ? t(isOpen ? 'explorer.collapseDirectory' : 'explorer.expandDirectory', {
        name: entry.name,
      })
    : ''
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
    () =>
      orderContextChildren(
        entry.uri,
        visibleContextEntries(listQuery.data?.entries ?? []),
      ),
    [entry.uri, listQuery.data?.entries],
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
    <li className="relative min-w-0 list-none">
      <TreeIndentGuides level={level} />
      <div
        ref={rowRef}
        className={styles.row}
        data-selected={isSelected || undefined}
      >
        {entry.isDir ? (
          <button
            type="button"
            aria-expanded={isOpen}
            aria-label={disclosureLabel}
            title={disclosureLabel}
            className={styles.disclosure}
            style={{ left: treeRowPadding(level) }}
            onClick={toggle}
          >
            <ChevronRightIcon
              aria-hidden="true"
              className={cn(
                'size-2.5 transition-transform',
                isOpen && 'rotate-90',
              )}
            />
          </button>
        ) : null}
        <button
          type="button"
          aria-label={entry.name}
          aria-current={isSelected ? 'location' : undefined}
          className={styles.selection}
          style={{ paddingLeft: treeSelectionPadding(level) }}
          onClick={select}
        >
          {entry.isDir ? (
            isOpen ? (
              <FolderOpenIcon
                aria-hidden="true"
                className={styles.nodeIcon}
                strokeWidth={1.5}
              />
            ) : (
              <FolderIcon
                aria-hidden="true"
                className={styles.nodeIcon}
                strokeWidth={1.5}
              />
            )
          ) : (
            <FileTextIcon
              aria-hidden="true"
              className={styles.nodeIcon}
              strokeWidth={1.5}
            />
          )}
          <span className={styles.name} title={entry.name}>
            {displayName}
          </span>
          {namespaceHint ? (
            <span className={styles.hint} title={namespaceHint}>
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
        </button>
      </div>

      {entry.isDir && isOpen ? (
        listQuery.isLoading ? (
          <div
            className="relative flex h-7 items-center gap-2 px-1.5 text-xs text-muted-foreground"
            style={{ paddingLeft: treeChildContentPadding(level) }}
          >
            <TreeIndentGuides level={level + 1} />
            <Loader2Icon className="size-3 animate-spin" />
            {t('explorer.loading')}
          </div>
        ) : children.length > 0 ? (
          <ul role="list" className="m-0 min-w-0 list-none p-0">
            {children.map((child) => (
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
            ))}
          </ul>
        ) : (
          <div
            className="relative h-7 px-1.5 text-xs leading-7 text-muted-foreground/60"
            style={{ paddingLeft: treeChildContentPadding(level) }}
          >
            <TreeIndentGuides level={level + 1} />
            {t('explorer.empty')}
          </div>
        )
      ) : null}
    </li>
  )
}

export function TreeIndentGuides({ level }: { level: number }) {
  if (level <= 0) return null

  return (
    <div className={styles.guides}>
      {Array.from({ length: level }, (_, index) => (
        <span
          key={index}
          className={styles.guide}
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

function treeSelectionPadding(level: number): string {
  return `${level * TREE_INDENT_WIDTH + TREE_ROW_PADDING + TREE_CONTROL_SIZE + TREE_CONTROL_GAP}px`
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
          ? 'bg-background text-foreground shadow-xs'
          : 'text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
      aria-pressed={active}
      onClick={onClick}
    >
      <Icon className="size-3.5" />
      {label}
    </button>
  )
}
