import studioI18n from '#/i18n/core'
import { useQueryClient } from '@tanstack/react-query'
import type { Ref, PointerEvent as ReactPointerEvent } from 'react'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useImperativeHandle, useRef, useEffect, useMemo } from 'react'
import { useFileBrowser } from '@openviking/react/modules/file-browser'
import { toast } from 'sonner'
import { FileTree } from '@openviking/react/components/file-tree'
import '@openviking/react/components/file-tree/style.css'
import { fetchFsStat } from '#/features/workbench/resources/lib/api'
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries'
import '@openviking/react/components/file-browser-entries/style.css'
import styles from './context-explorer.module.css'
import { useTranslation } from 'react-i18next'

import type { VikingFsEntry } from '#/shared/types/viking-fs'

import { ROOT_URI } from '#/features/workbench/filesystem/lib/constants'

export function ContextExplorerHeader({
  onAddResource,
}: {
  onAddResource?: () => void
}) {
  const { t, i18n } = useTranslation('filesystem')
  const { connection, identityScopeKey } = useAppConnection()
  const locale = i18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'
  return (
    <div className={styles.header}>
      <FileBrowserEntries
        key={identityScopeKey}
        locale={locale}
        agentConnect={{
          apiKey: connection.apiKey,
          baseUrl: connection.baseUrl,
          locale,
        }}
        onImportResources={onAddResource}
        labels={{
          connectAgent: t('ide.connectAgent'),
          importResources: t('explorer.addResource'),
        }}
      />
    </div>
  )
}

export function ContextTree({
  refreshRef,
  isRefreshing = false,
  onOpenSearch,
  onRefresh,
  currentUri,
  expandedKeys,
  onExpandedKeysChange,
  onSelectDirectory,
  onSelectFile,
  selectedFileUri,
  rootUri = ROOT_URI,
}: {
  isRefreshing?: boolean
  onOpenSearch?: () => void
  onRefresh?: () => Promise<void>
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
  // This is an initial location, not a controlled expansion value. Changing it
  // on selection reinitializes the shared module and discards all loaded branches.
  // StudioUiProvider remounts this subtree when the connection identity changes.
  const initialExpandedUri = useRef(currentUri)
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
    defaultExpandedUri: initialExpandedUri.current,
    loadPreview: false,
    locale: studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US',
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
  const rows = useMemo(() => {
    const source = browser.rows
    if (rootUri !== ROOT_URI) return source
    const start = source.findIndex(
      (row) => row.kind === 'entry' && row.uri === 'viking://user',
    )
    if (start <= 0) return source
    const next = source.findIndex(
      (row, index) => index > start && row.depth <= source[start].depth,
    )
    const end = next < 0 ? source.length : next
    // Rows are flattened: move the complete user subtree, including loading rows.
    return [
      ...source.slice(start, end),
      ...source.slice(0, start),
      ...source.slice(end),
    ]
  }, [browser.rows, rootUri])
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
      locale={studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
      className={styles.tree}
      search={Boolean(onOpenSearch)}
      refresh={{ loading: isRefreshing }}
      rows={rows}
      selectedId={browser.selectedId}
      isReady={browser.isReady}
      errorFallback={browser.treeError?.message}
      aria-label={t('explorer.title')}
      operations={{
        ...browser.actions,
        search: onOpenSearch
          ? async () => {
              onOpenSearch()
            }
          : undefined,
        refresh: onRefresh ?? browser.actions.refresh,
        toggleExpand: async (row) => {
          const next = new Set(expandedKeys)
          if (next.has(row.uri)) next.delete(row.uri)
          else next.add(row.uri)
          onExpandedKeysChange(next)
          await browser.actions.toggleExpand(row)
        },
      }}
      labels={{
        empty: t('explorer.empty'),
        loading: t('explorer.loading'),
        search: t('ide.search'),
        refresh: t('explorer.refresh'),
      }}
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
