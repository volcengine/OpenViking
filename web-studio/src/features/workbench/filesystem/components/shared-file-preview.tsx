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
import { FileTextIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import {
  fetchDirectoryLevelContent,
  fetchFileContent,
  saveFileContent,
} from '#/features/workbench/resources/lib/api'
import { useInvalidateVikingFs } from '#/features/workbench/resources/hooks/viking-fm'
import { getContentDownload } from '#/shared/api/ov-client'
import { detectFileType } from '#/shared/lib/viking-fs'
import type { VikingFsEntry } from '#/shared/types/viking-fs'
import { Button } from '#/shared/ui/button'

interface Props {
  file: VikingFsEntry
  onNavigate?: (uri: string) => void
  onDirtyChange: (dirty: boolean) => void
  onSaved: (uri: string) => void
  extraActions?: ReactNode
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
  const [allowedUri, setAllowedUri] = useState<string | null>(null)
  const needsLoad =
    !file.isDir &&
    (file.sizeBytes ?? 0) > 2 * 1024 * 1024 &&
    allowedUri !== file.uri
  const kind = detectFileType(file.uri)
  const binary = kind === 'image' || kind === 'binary'
  const query = useQuery({
    enabled: !needsLoad,
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
  const renderActions = (actions: FilePreviewActions) => {
    actionsRef.current = actions
    return (
      <div className="flex items-center gap-1">
        {actions.canSwitchView && !actions.editing ? (
          <>
            <Button
              variant={actions.viewMode === 'preview' ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => actions.setViewMode('preview')}
            >
              {t('filePreview.markdownPreview')}
            </Button>
            <Button
              variant={actions.viewMode === 'source' ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => actions.setViewMode('source')}
            >
              {t('filePreview.markdownSource')}
            </Button>
          </>
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
            <Button variant="ghost" size="sm" onClick={actions.startEditing}>
              {t('filePreview.edit')}
            </Button>
          )
        ) : null}
        {extraActions}
      </div>
    )
  }
  const common = {
    key: file.uri,
    title: file.name,
    icon: <FileTextIcon className="size-3.5" />,
    operations,
    slots: { actions: renderActions },
  }
  let tab: FilePreviewTab
  if (file.isDir) {
    tab = {
      ...common,
      kind: 'directory',
      loading: query.isPending,
      defaultLevelKey:
        query.data && 'abstract' in query.data && !query.data.abstract
          ? 'l1'
          : 'l0',
      levels: [
        {
          key: 'l0',
          label: 'L0',
          preview:
            query.data && 'abstract' in query.data ? (
              <StudioMarkdownPreview
                content={query.data.abstract ?? ''}
                uri={`${file.uri.replace(/\/$/, '')}/.abstract.md`}
                onNavigate={onNavigate}
                directory
              />
            ) : undefined,
          source: {
            fileName: `${file.name}.md`,
            path: file.uri,
            content:
              query.data && 'abstract' in query.data ? query.data.abstract : '',
          },
        },
        {
          key: 'l1',
          label: 'L1',
          preview:
            query.data && 'overview' in query.data ? (
              <StudioMarkdownPreview
                content={query.data.overview ?? ''}
                uri={`${file.uri.replace(/\/$/, '')}/.overview.md`}
                onNavigate={onNavigate}
                directory
              />
            ) : undefined,
          source: {
            fileName: `${file.name}.md`,
            path: file.uri,
            content:
              query.data && 'overview' in query.data ? query.data.overview : '',
          },
        },
      ],
    }
  } else {
    tab = {
      ...common,
      kind: 'file',
      preview:
        content ??
        (query.data && 'content' in query.data ? (
          kind === 'jsonl' ? (
            <JsonlPreview content={query.data.content} />
          ) : kind === 'markdown' ? (
            <StudioMarkdownPreview
              content={query.data.content}
              uri={file.uri}
              onNavigate={onNavigate}
            />
          ) : undefined
        ) : undefined),
      loading: query.isPending && !content,
      source: {
        fileName: file.name,
        path: file.uri,
        ...(query.data && 'blob' in query.data
          ? { file: query.data.blob }
          : {}),
        ...(query.data && 'content' in query.data
          ? {
              content:
                kind === 'markdown'
                  ? memoryFieldsDisplayContent(query.data.content)
                  : query.data.content,
              truncated: query.data.truncated,
            }
          : {}),
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
          {query.error instanceof Error
            ? query.error.message
            : t('filePreview.editFailed')}
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
                {extraActions}
              </div>
              <div className="min-h-0 flex-1 overflow-auto">
                {customContent}
              </div>
            </div>
          ),
        },
        { ...tab, title: t('filePreview.markdownSource') },
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
