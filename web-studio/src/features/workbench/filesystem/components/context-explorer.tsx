import { useQueryClient } from '@tanstack/react-query'
import type { Ref, PointerEvent as ReactPointerEvent } from 'react'
import { AgentConnectDialog } from './agent-connect-dialog'
import { useState, useImperativeHandle, useRef, useEffect } from 'react'
import { useFileBrowser } from '@openviking/react/modules/file-browser'
import { toast } from 'sonner'
import { FileTree } from '@openviking/react/components/file-tree'
import '@openviking/react/components/file-tree/style.css'
import { fetchFsStat } from '#/features/workbench/resources/lib/api'
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries'
import '@openviking/react/components/file-browser-entries/style.css'
import styles from './context-explorer.module.css'
import { RefreshCcwIcon, SearchIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { VikingFsEntry } from '#/shared/types/viking-fs'
import { cn } from '#/shared/lib/utils'
import { Button } from '#/shared/ui/button'

import { ROOT_URI } from '#/features/workbench/filesystem/lib/constants'

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
  const [connectOpen, setConnectOpen] = useState(false)

  return (
    <div className={styles.header}>
      <FileBrowserEntries
        onConnectAgent={() => setConnectOpen(true)}
        onImportResources={onAddResource}
        labels={{
          connectAgent: t('ide.connectAgent'),
          importResources: t('explorer.addResource'),
        }}
      />
      {connectOpen ? (
        <AgentConnectDialog open={connectOpen} onOpenChange={setConnectOpen} />
      ) : null}
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

export function ContextTree({
  refreshRef,
  currentUri,
  expandedKeys,
  onExpandedKeysChange,
  onSelectDirectory,
  onSelectFile,
  selectedFileUri,
  rootUri = ROOT_URI,
}: {
  refreshRef?: Ref<{ refresh: () => Promise<void> }>
  currentUri: string
  expandedKeys: Set<string>
  onExpandedKeysChange: (next: Set<string>) => void
  onSelectDirectory: (entry: VikingFsEntry) => void
  onSelectFile: (entry: VikingFsEntry) => void
  selectedFileUri?: string | null
  rootUri?: string
}) {
  const { t } = useTranslation('filesystem')
  const navigationVersion = useRef(0)
  // Invalidate pending stat requests when this identity-scoped tree unmounts.
  useEffect(
    () => () => {
      navigationVersion.current += 1
    },
    [],
  )
  const browser = useFileBrowser({
    rootUri,
    controlled: true,
    currentUri: selectedFileUri || currentUri,
    defaultExpandedUri: currentUri,
    loadPreview: false,
    onUriChange: (uri) => {
      if (!uri) return
      const version = ++navigationVersion.current
      void fetchFsStat(uri)
        .then((entry) => {
          if (navigationVersion.current !== version) return
          entry.isDir ? onSelectDirectory(entry) : onSelectFile(entry)
        })
        .catch((error) =>
          toast.error(
            error instanceof Error
              ? error.message
              : t('dirBrowser.error', { ns: 'resources' }),
          ),
        )
    },
  })
  const cache = useQueryClient().getQueryCache()
  const refresh = browser.actions.refresh
  // Observe Studio mutations in the external query cache; the module owns its own data.
  // Coalesce invalidations and unsubscribe on identity unmount to avoid stale refreshes.
  useEffect(() => {
    let active = true
    let queued = false
    const unsubscribe = cache.subscribe((event) => {
      if (
        event.type !== 'updated' ||
        event.action.type !== 'invalidate' ||
        !String(event.query.queryKey[0]).startsWith('viking-fs-') ||
        queued
      )
        return
      queued = true
      queueMicrotask(() => {
        queued = false
        if (active) void refresh()
      })
    })
    return () => {
      active = false
      unsubscribe()
    }
  }, [cache, refresh])
  useImperativeHandle(
    refreshRef,
    () => ({ refresh: browser.actions.refresh }),
    [browser.actions.refresh],
  )
  return (
    <FileTree
      className={styles.tree}
      rows={browser.rows}
      selectedId={browser.selectedId}
      isReady={browser.isReady}
      errorFallback={browser.treeError?.message}
      aria-label={t('explorer.title')}
      operations={{
        ...browser.actions,
        toggleExpand: async (row) => {
          const next = new Set(expandedKeys)
          if (next.has(row.uri)) next.delete(row.uri)
          else next.add(row.uri)
          onExpandedKeysChange(next)
          await browser.actions.toggleExpand(row)
        },
      }}
      labels={{ empty: t('explorer.empty'), loading: t('explorer.loading') }}
    />
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
