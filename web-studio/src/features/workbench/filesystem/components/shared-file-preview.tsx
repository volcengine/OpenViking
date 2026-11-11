import {
  JsonlPreview,
  StudioMarkdownPreview,
  memoryFieldsDisplayContent,
} from '#/features/workbench/resources/components/structured-file-content'
import { FilePreview } from '@openviking/react/components/file-preview'
import type {
  FilePreviewActions,
  FilePreviewTab,
} from '@openviking/react/components/file-preview'
import '@openviking/react/components/file-preview/style.css'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import styles from '../workbench.module.css'
import { isSystemUri } from '../lib/document-kind'
import { getErrorMessage } from '../lib/utils'
import { FileTextIcon, PencilIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import {
  fetchDirectoryLevelContent,
  fetchFileContent,
  saveFileContent,
} from '#/features/workbench/resources/lib/api'
import { useInvalidateVikingFs } from '#/features/workbench/resources/hooks/viking-fm'
import { getContentDownload } from '#/shared/api/ov-client'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import { detectFileType } from '#/shared/lib/viking-fs'
import type { VikingFsEntry } from '#/shared/types/viking-fs'
import { Button } from '#/shared/ui/button'

interface Props {
  file: VikingFsEntry
  onNavigate?: (uri: string) => void
  onDirtyChange: (dirty: boolean) => void
  onSaved: (uri: string) => void
  extraActions?: ReactNode | ((actions: FilePreviewActions | null) => ReactNode)
  content?: ReactNode
  readOnly?: boolean
  defaultReadLimit?: number
}

export function SharedFilePreview({
  file,
  onNavigate,
  onDirtyChange,
  onSaved,
  extraActions,
  content,
  readOnly = false,
  defaultReadLimit = -1,
}: Props) {
  const { t, i18n } = useTranslation(['resources', 'filesystem'])
  const { identityScopeKey } = useAppConnection()
  const { invalidatePreview } = useInvalidateVikingFs()
  const currentScope = `${identityScopeKey}:${file.uri}`
  const scopeRef = useRef(currentScope)
  scopeRef.current = currentScope
  const actionsRef = useRef<FilePreviewActions | null>(null)
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const actions = actionsRef.current
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === 's' &&
        actions?.editing
      ) {
        event.preventDefault()
        if (!actions.busy) void actions.saveEditing()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])
  const [sourceScope, setSourceScope] = useState<string | null>(null)
  const sourceMode = sourceScope === currentScope
  const [directoryLevel, setDirectoryLevel] = useState<{
    scope: string
    key: string
  }>()
  const [allowedUri, setAllowedUri] = useState<string | null>(null)
  const needsLoad =
    !file.isDir &&
    (file.sizeBytes ?? 0) > 2 * 1024 * 1024 &&
    allowedUri !== file.uri
  const kind = detectFileType(file.uri)
  const binary = kind === 'image' || kind === 'binary'
  const directoryListOnly =
    file.isDir && Boolean(content) && isSystemUri(file.uri)
  const query = useQuery({
    enabled: !needsLoad && !directoryListOnly,
    queryKey: [
      'studio-shared-preview',
      identityScopeKey,
      file.uri,
      file.modTime,
      defaultReadLimit,
    ],
    queryFn: async () => {
      if (file.isDir) {
        const [abstract, overview] = await Promise.all([
          fetchDirectoryLevelContent(file.uri, 'abstract'),
          fetchDirectoryLevelContent(file.uri, 'overview'),
        ])
        return { abstract, overview }
      }
      if (binary) {
        const response = await getContentDownload({
          query: { uri: file.uri },
          responseType: 'blob',
          throwOnError: true,
        })
        return { blob: response.data as Blob }
      }
      return fetchFileContent(file.uri, { limit: defaultReadLimit, raw: true })
    },
  })
  // Reset the host navigation guard when this resource view is removed.
  useEffect(() => {
    scopeRef.current = currentScope
    return () => {
      scopeRef.current = ''
      onDirtyChange(false)
    }
  }, [currentScope, onDirtyChange])
  const operations = {
    openParent: async (uri: string) => {
      onNavigate?.(uri)
    },
    openUri: async (uri: string) => {
      if (uri.startsWith('viking://')) onNavigate?.(uri)
      else if (/^https?:\/\//i.test(uri))
        window.open(uri, '_blank', 'noopener,noreferrer')
      else if (!/^[a-z][a-z\d+.-]*:/i.test(uri) && !uri.startsWith('#')) {
        const base = new URL(file.uri.replace('viking://', 'https://viking/'))
        const next = new URL(uri, base)
        onNavigate?.(`viking://${next.pathname.replace(/^\//, '')}`)
      }
    },
  }
  const summaries =
    query.data && 'abstract' in query.data ? query.data : undefined
  const level =
    directoryLevel?.scope === currentScope
      ? directoryLevel.key
      : summaries && !summaries.abstract
        ? 'l1'
        : 'l0'
  const summary = level === 'l1' ? summaries?.overview : summaries?.abstract
  const fileText =
    query.data && 'content' in query.data ? query.data.content : undefined
  const displayText =
    fileText !== undefined && kind === 'markdown'
      ? memoryFieldsDisplayContent(fileText)
      : fileText
  const customFilePreview =
    !file.isDir &&
    Boolean(
      content ||
      (fileText !== undefined && (kind === 'jsonl' || kind === 'markdown')),
    )
  const customBody = file.isDir ? (
    summary !== undefined ? (
      <StudioMarkdownPreview
        content={summary}
        uri={`${file.uri.replace(/\/$/, '')}/${level === 'l1' ? '.overview.md' : '.abstract.md'}`}
        onNavigate={onNavigate}
        directory
      />
    ) : undefined
  ) : sourceMode && customFilePreview ? (
    <pre className="min-w-max whitespace-pre px-4 py-3 font-mono text-xs leading-5">
      {displayText}
    </pre>
  ) : (
    (content ??
    (fileText !== undefined ? (
      kind === 'jsonl' ? (
        <JsonlPreview content={fileText} />
      ) : kind === 'markdown' ? (
        <StudioMarkdownPreview
          content={fileText}
          uri={file.uri}
          onNavigate={onNavigate}
        />
      ) : undefined
    ) : undefined))
  )
  const renderedBody = customBody ? (
    <div className={styles.studioPreviewBody}>{customBody}</div>
  ) : undefined
  const renderActions = (actions: FilePreviewActions) => {
    actionsRef.current = actions
    const nativeBody = actions.busy || actions.editing
    const showingSource = customFilePreview
      ? sourceMode
      : actions.viewMode === 'source'
    const copyText = file.isDir ? summary : displayText
    const menuActions =
      renderedBody && copyText !== undefined
        ? {
            ...actions,
            canCopyContent: Boolean(copyText),
            copyContent: async () => {
              if (!copyText) return false
              try {
                await copyTextToClipboard(copyText)
                return true
              } catch {
                return false
              }
            },
          }
        : actions
    return (
      <div
        className={styles.previewActions}
        data-native-body={nativeBody || undefined}
      >
        {(customFilePreview || actions.canSwitchView) && !nativeBody ? (
          <div
            className={styles.previewModeSwitch}
            role="group"
            aria-label={t('filePreview.viewMode')}
          >
            <Button
              variant="ghost"
              className={styles.previewModeButton}
              data-active={!showingSource || undefined}
              aria-pressed={!showingSource}
              size="sm"
              onClick={() =>
                customFilePreview
                  ? setSourceScope(null)
                  : actions.setViewMode('preview')
              }
            >
              {t('filePreview.markdownPreview')}
            </Button>
            <Button
              variant="ghost"
              className={styles.previewModeButton}
              data-active={showingSource || undefined}
              aria-pressed={showingSource}
              size="sm"
              onClick={() =>
                customFilePreview
                  ? setSourceScope(currentScope)
                  : actions.setViewMode('source')
              }
            >
              {t('filePreview.markdownSource')}
            </Button>
          </div>
        ) : null}
        {actions.canEdit || actions.editing ? (
          actions.editing ? (
            <>
              <Button
                size="sm"
                disabled={actions.busy}
                onClick={() => {
                  void actions.saveEditing()
                }}
              >
                {t('filePreview.save')}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={actions.busy}
                onClick={actions.cancelEditing}
              >
                {t('filePreview.cancel')}
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              size="icon-sm"
              className={styles.previewIconAction}
              title={t('filePreview.edit')}
              aria-label={t('filePreview.edit')}
              onClick={actions.startEditing}
            >
              <PencilIcon className="size-3.5" />
            </Button>
          )
        ) : null}
        {typeof extraActions === 'function'
          ? extraActions(menuActions)
          : extraActions}
      </div>
    )
  }
  const common = {
    key: file.uri,
    title: file.name,
    icon: <FileTextIcon className="size-3.5" />,
    operations,
    slots: { actions: renderActions, footer: renderedBody },
  }
  let tab: FilePreviewTab
  if (file.isDir) {
    tab = {
      ...common,
      kind: 'directory',
      loading: query.isPending,
      activeLevelKey: level,
      onActiveLevelChange: (key) =>
        setDirectoryLevel({ scope: currentScope, key }),
      levels: [
        {
          key: 'l0',
          label: 'L0',
          source: {
            fileName: `${file.name}.md`,
            path: file.uri,
            content: '',
          },
        },
        {
          key: 'l1',
          label: 'L1',
          source: {
            fileName: `${file.name}.md`,
            path: file.uri,
            content: '',
          },
        },
      ],
    }
  } else {
    tab = {
      ...common,
      kind: 'file',
      loading: query.isPending && !content,
      source: {
        fileName: file.name,
        path: file.uri,
        ...(query.data && 'blob' in query.data
          ? { file: query.data.blob }
          : {}),
        content: renderedBody ? '' : displayText,
        truncated:
          query.data && 'truncated' in query.data
            ? query.data.truncated
            : undefined,
      },
      editing:
        !readOnly && !binary && query.isSuccess
          ? {
              loadContent: async () => {
                const response = await getContentDownload({
                  query: { uri: file.uri },
                  responseType: 'text',
                  throwOnError: true,
                })
                if (typeof response.data !== 'string')
                  throw new Error(t('filePreview.editFailed'))
                if (scopeRef.current !== currentScope) return undefined
                if (new Blob([response.data]).size > 2 * 1024 * 1024)
                  throw new Error(t('filePreview.editFailed'))
                onDirtyChange(true)
                return response.data
              },
              save: async (value) => {
                await saveFileContent(file.uri, value)
              },
              onSave: () => {
                if (scopeRef.current !== currentScope) return
                onDirtyChange(false)
                invalidatePreview(file.uri)
                void query.refetch()
                onSaved(file.uri)
              },
              onCancel: () => onDirtyChange(false),
            }
          : undefined,
    }
  }
  if (needsLoad)
    return (
      <div className="p-6 text-sm text-muted-foreground">
        {t('filePreview.largeFileSkipped')}
        <Button variant="outline" onClick={() => setAllowedUri(file.uri)}>
          {t('filePreview.loadFile')}
        </Button>
      </div>
    )
  if (query.isError) {
    tab = {
      ...common,
      kind: 'content',
      content: (
        <div role="alert" className="p-4 text-sm text-destructive">
          {getErrorMessage(query.error)}
          <Button
            variant="outline"
            onClick={() => {
              void query.refetch()
            }}
          >
            {t('filesystem:agent.retry')}
          </Button>
        </div>
      ),
    }
  }
  const customContent = file.isDir ? content : undefined
  const tabs: [FilePreviewTab, ...FilePreviewTab[]] = customContent
    ? [
        {
          key: `${file.uri}:content`,
          kind: 'content',
          title: file.name,
          content: (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="flex min-h-11 items-center justify-between gap-2 border-b px-3 py-1">
                <span className="truncate text-xs text-muted-foreground">
                  {file.uri}
                </span>
                {typeof extraActions === 'function'
                  ? extraActions(null)
                  : extraActions}
              </div>
              <div className="min-h-0 flex-1 overflow-auto">
                {customContent}
              </div>
            </div>
          ),
        },
        ...(directoryListOnly
          ? []
          : [{ ...tab, title: t('filePreview.markdownSource') }]),
      ]
    : [tab]
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <FilePreview
        key={`${identityScopeKey}:${file.uri}`}
        tabs={tabs}
        locale={i18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
        labels={{
          copyPath: t('filesystem:copyUri'),
          editor: t('filePreview.edit'),
          retry: t('filesystem:agent.retry'),
          saveFailed: t('filePreview.saveFailed'),
          loading: t('filePreview.loadingEditor'),
        }}
      />
      {[file.size, file.modTime].filter(Boolean).length ? (
        <footer
          data-file-metadata
          className="shrink-0 border-t px-4 py-2 text-xs text-muted-foreground"
        >
          {[file.size, file.modTime].filter(Boolean).join(' · ')}
        </footer>
      ) : null}
    </div>
  )
}
