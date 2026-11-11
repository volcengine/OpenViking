import {
  JsonlPreview,
  StudioMarkdownPreview,
  memoryFieldsDisplayContent,
} from '#/features/workbench/resources/components/structured-file-content'
import { FilePreview } from '@openviking/react/components/file-preview'
import type {
  FilePreviewActions,
  FilePreviewEditing,
  FilePreviewTab,
} from '@openviking/react/components/file-preview'
import '@openviking/react/components/file-preview/style.css'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import styles from '../workbench.module.css'
import { isSystemUri } from '../lib/document-kind'
import { getAncestorUris, getErrorMessage } from '../lib/utils'
import { useCustomPreviewEditing } from '../lib/use-custom-preview-editing'
import { CopyIcon, FileTextIcon, PencilIcon } from 'lucide-react'
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
import {
  detectFileType,
  fileNameFromUri,
  parentUri,
} from '#/shared/lib/viking-fs'
import type { VikingFsEntry } from '#/shared/types/viking-fs'
import { Button } from '#/shared/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '#/shared/ui/tabs'
import { Textarea } from '#/shared/ui/textarea'

const DIRECTORY_LEVELS = [
  { key: 'l0', label: 'L0', sidecar: '.abstract.md' },
  { key: 'l1', label: 'L1', sidecar: '.overview.md' },
] as const

type PreviewMenuActions = Pick<
  FilePreviewActions,
  'busy' | 'canCopyContent' | 'copyContent'
>
type PreviewToolbarActions = Pick<
  FilePreviewActions,
  | 'viewMode'
  | 'setViewMode'
  | 'canSwitchView'
  | 'editing'
  | 'canEdit'
  | 'startEditing'
  | 'saveEditing'
  | 'cancelEditing'
> &
  PreviewMenuActions

interface Props {
  file: VikingFsEntry
  onNavigate?: (uri: string) => void
  onDirtyChange: (dirty: boolean) => void
  onSaved: (uri: string) => void
  extraActions?: ReactNode | ((actions: PreviewMenuActions | null) => ReactNode)
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
  const actionsRef = useRef<PreviewToolbarActions | null>(null)
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
  const copy = async (value: string | undefined) => {
    if (!value) return false
    try {
      await copyTextToClipboard(value)
      return true
    } catch {
      return false
    }
  }
  const editing: FilePreviewEditing | undefined =
    !file.isDir && !readOnly && !binary && query.data !== undefined
      ? {
          loadContent: async () => {
            const response = await getContentDownload({
              query: { uri: file.uri },
              responseType: 'text',
              throwOnError: true,
            })
            if (scopeRef.current !== currentScope) return undefined
            if (
              typeof response.data !== 'string' ||
              new Blob([response.data]).size > 2 * 1024 * 1024
            )
              throw new Error(t('filePreview.editFailed'))
            return response.data
          },
          save: async (value) => {
            await saveFileContent(file.uri, value)
          },
          onSave: () => {
            if (scopeRef.current !== currentScope) return
            onDirtyChange(false)
            void invalidatePreview(file.uri)
            onSaved(file.uri)
          },
          onCancel: () => onDirtyChange(false),
        }
      : undefined
  const customEdit = useCustomPreviewEditing(
    currentScope,
    { fileName: file.name, path: file.uri },
    customFilePreview ? editing : undefined,
    onDirtyChange,
  )
  const customToolbar: PreviewToolbarActions = {
    ...customEdit,
    viewMode: sourceMode ? 'source' : 'preview',
    setViewMode: (mode) =>
      setSourceScope(mode === 'source' ? currentScope : null),
    canSwitchView: customFilePreview,
    canCopyContent: Boolean(displayText),
    copyContent: () => copy(displayText),
  }
  const renderActions = (actions: PreviewToolbarActions) => {
    actionsRef.current = actions
    const showingSource = actions.viewMode === 'source'
    return (
      <div className={styles.previewActions}>
        {actions.canSwitchView && !actions.busy && !actions.editing ? (
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
              onClick={() => actions.setViewMode('preview')}
            >
              {t('filePreview.markdownPreview')}
            </Button>
            <Button
              variant="ghost"
              className={styles.previewModeButton}
              data-active={showingSource || undefined}
              aria-pressed={showingSource}
              size="sm"
              onClick={() => actions.setViewMode('source')}
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
              disabled={actions.busy}
              onClick={actions.startEditing}
            >
              <PencilIcon className="size-3.5" />
            </Button>
          )
        ) : null}
        {typeof extraActions === 'function'
          ? extraActions(actions)
          : extraActions}
      </div>
    )
  }
  const common = {
    key: file.uri,
    title: file.name,
    icon: <FileTextIcon className="size-3.5" />,
  }
  const header = (actions: ReactNode) => (
    <div className="flex min-h-11 shrink-0 items-center justify-between gap-2 border-b px-2 py-1">
      <div className="flex min-w-0 items-center gap-1 font-mono text-xs text-muted-foreground">
        {onNavigate && (
          <div className="flex min-w-0 items-center overflow-hidden whitespace-nowrap">
            {getAncestorUris(file.isDir ? file.uri : parentUri(file.uri))
              .filter(
                (uri) => uri.replace(/\/$/, '') !== file.uri.replace(/\/$/, ''),
              )
              .map((uri) => (
                <button
                  type="button"
                  key={uri}
                  className="shrink-0 hover:underline"
                  onClick={() => onNavigate(uri)}
                >
                  {uri === 'viking://' ? uri : `${fileNameFromUri(uri)}/`}
                </button>
              ))}
          </div>
        )}
        <span className="truncate text-foreground" title={file.uri}>
          {onNavigate ? file.name : file.uri}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t('filesystem:copyUri')}
          onClick={() => void copy(file.uri)}
        >
          <CopyIcon className="size-3.5" />
        </Button>
      </div>
      {actions}
    </div>
  )
  const renderContent = (body: ReactNode, actions: ReactNode) => (
    <div className="flex min-h-0 flex-1 flex-col">
      {header(actions)}
      <div className="min-h-0 flex-1 overflow-auto">{body}</div>
    </div>
  )
  const nativeSource = {
    fileName: file.name,
    path: file.uri,
    ...(query.data && 'blob' in query.data ? { file: query.data.blob } : {}),
    content: displayText,
    truncated:
      query.data && 'truncated' in query.data
        ? query.data.truncated
        : undefined,
  }
  let tab: FilePreviewTab
  if (file.isDir) {
    const summary = level === 'l1' ? summaries?.overview : summaries?.abstract
    const summaryActions =
      typeof extraActions === 'function'
        ? extraActions({
            busy: false,
            canCopyContent: Boolean(summary),
            copyContent: () => copy(summary),
          })
        : extraActions
    tab = {
      ...common,
      kind: 'content',
      content: renderContent(
        summaries ? (
          <Tabs
            value={level}
            onValueChange={(key) =>
              setDirectoryLevel({ scope: currentScope, key })
            }
          >
            <TabsList variant="line" className="mx-4 mt-1">
              {DIRECTORY_LEVELS.map(({ key, label }) => (
                <TabsTrigger key={key} value={key}>
                  {label}
                </TabsTrigger>
              ))}
            </TabsList>
            {DIRECTORY_LEVELS.map(({ key, sidecar }) => (
              <TabsContent key={key} value={key}>
                <StudioMarkdownPreview
                  content={
                    key === 'l1'
                      ? (summaries.overview ?? '')
                      : (summaries.abstract ?? '')
                  }
                  uri={`${file.uri.replace(/\/$/, '')}/${sidecar}`}
                  onNavigate={onNavigate}
                  directory
                />
              </TabsContent>
            ))}
          </Tabs>
        ) : (
          <p role="status" className="p-4 text-sm">
            {t('filePreview.loadingContent')}
          </p>
        ),
        summaryActions,
      ),
    }
  } else if (customFilePreview) {
    const customBody =
      customEdit.draft !== undefined ? (
        <Textarea
          aria-label={t('filePreview.edit')}
          value={customEdit.draft}
          readOnly={customEdit.busy}
          spellCheck={false}
          className="min-h-full resize-none rounded-none border-0 bg-background p-4 font-mono"
          onChange={(event) => customEdit.changeDraft(event.target.value)}
        />
      ) : customEdit.busy ? (
        <p role="status" className="p-4 text-sm">
          {t('filePreview.loadingEditor')}
        </p>
      ) : sourceMode ? (
        <pre className="min-w-max whitespace-pre px-4 py-3 font-mono text-xs leading-5">
          {displayText}
        </pre>
      ) : (
        (content ??
        (kind === 'jsonl' ? (
          <JsonlPreview content={fileText ?? ''} />
        ) : (
          <StudioMarkdownPreview
            content={fileText ?? ''}
            uri={file.uri}
            onNavigate={onNavigate}
          />
        )))
      )
    tab = {
      ...common,
      kind: 'content',
      content: renderContent(
        <>
          {customEdit.error ? (
            <p role="alert" className="p-4 text-sm text-destructive">
              {customEdit.error === 'editFailed'
                ? t('filePreview.editFailed')
                : t('filePreview.saveFailed')}
            </p>
          ) : null}
          {customBody}
          {nativeSource.truncated && !customEdit.editing && !customEdit.busy ? (
            <p className="px-4 py-1 text-xs text-muted-foreground">
              {t('filePreview.truncated')}
            </p>
          ) : null}
        </>,
        renderActions(customToolbar),
      ),
    }
  } else {
    tab = {
      ...common,
      kind: 'file',
      operations,
      slots: { actions: renderActions },
      loading: query.isPending,
      source: nativeSource,
      editing: editing
        ? {
            ...editing,
            loadContent: async (source) => {
              const draft = await editing.loadContent?.(source)
              if (draft !== undefined) onDirtyChange(true)
              return draft
            },
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
  if (query.isError && query.data === undefined) {
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
          content: renderContent(
            customContent,
            typeof extraActions === 'function'
              ? extraActions(null)
              : extraActions,
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
