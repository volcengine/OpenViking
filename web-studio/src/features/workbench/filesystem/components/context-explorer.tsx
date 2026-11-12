import studioI18n from '#/i18n/core'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useRef, useEffect } from 'react'
import { useContextTree } from '../lib/use-context-tree'
import { getErrorMessage } from '../lib/utils'
import { normalizeDirUri, normalizeFileUri } from '#/shared/lib/viking-fs'
import { toast } from 'sonner'
import { FileTree } from '@openviking/react/components/file-tree'
import '@openviking/react/components/file-tree/style.css'
import { fetchFsStat } from '#/features/workbench/resources/lib/api'
import { FileBrowserEntries } from '@openviking/react/components/file-browser-entries'
import '@openviking/react/components/file-browser-entries/style.css'
import styles from './context-explorer.module.css'
import { FileTypeIcon } from './file-type-icon'
import { useTranslation } from 'react-i18next'

import type { VikingFsEntry } from '#/shared/types/viking-fs'

import { ROOT_URI } from '#/features/workbench/filesystem/lib/constants'

export function ContextExplorerHeader({
  onAddResource,
}: {
  onAddResource?: () => void
}) {
  const { t, i18n } = useTranslation('filesystem')
  const { connection, identityScopeKey, serverMode } = useAppConnection()
  const locale = i18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'
  return (
    <div className={styles.header}>
      <FileBrowserEntries
        key={identityScopeKey}
        locale={locale}
        agentConnect={
          serverMode === 'trusted'
            ? undefined
            : { apiKey: connection.apiKey, baseUrl: connection.baseUrl }
        }
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
  useEffect(
    () => () => {
      navigationVersion.current += 1
    },
    [],
  )
  const tree = useContextTree(rootUri, expandedKeys)
  const expand = (uri: string, open: boolean) => {
    const key = normalizeDirUri(uri)
    const next = new Set([...expandedKeys].map(normalizeDirUri))
    if (open) next.add(key)
    else next.delete(key)
    onExpandedKeysChange(next)
  }
  const select = async (uri: string) => {
    const version = ++navigationVersion.current
    try {
      const entry = await fetchFsStat(uri, { throwOnError: true })
      if (navigationVersion.current !== version) return
      if (entry.isDir) onSelectDirectory(entry)
      else onSelectFile(entry)
    } catch (error) {
      if (navigationVersion.current === version)
        toast.error(getErrorMessage(error))
    }
  }
  const { refresh } = tree
  return (
    <FileTree
      locale={studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
      className={styles.tree}
      search={Boolean(onOpenSearch)}
      refresh={{ loading: isRefreshing }}
      rows={tree.rows}
      renderRowIcon={(row) => <FileTypeIcon name={row.name} />}
      selectedId={normalizeFileUri(selectedFileUri || currentUri)}
      isReady={tree.isReady}
      aria-label={t('explorer.title')}
      operations={{
        select: async (row) => {
          if (row.type === 'directory' && !row.expanded) expand(row.uri, true)
          await select(row.uri)
        },
        loadMore: async (row) => {
          if (row.parentId) await tree.loadMore(row.parentId)
        },
        retry: async (row) => {
          if ('parentId' in row && row.parentId) await tree.retry(row.parentId)
          else await refresh()
        },
        search: onOpenSearch
          ? async () => {
              onOpenSearch()
            }
          : undefined,
        refresh: onRefresh ?? refresh,
        toggleExpand: async (row) => expand(row.uri, !row.expanded),
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
