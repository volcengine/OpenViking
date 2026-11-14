import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useSessionTitles } from '#/features/workbench/sessions/lib/use-session-titles'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChevronRightIcon,
  FileTextIcon,
  FolderIcon,
  LoaderCircleIcon,
} from 'lucide-react'
import { AgentExperienceList } from '#/features/workbench/agent-experience'
import { Thread } from '#/features/workbench/sessions/components/thread'
import { useVikingFsList } from '#/features/workbench/resources/hooks/viking-fm'
import { Button } from '#/shared/ui/button'
import { fileNameFromUri } from '#/shared/lib/viking-fs'
import {
  isExperienceDirectory,
  sessionIdFromDirectory,
} from '../lib/document-kind'
import { sortTreeEntries, visibleContextEntries } from '../lib/utils'
import styles from '../workbench.module.css'

export function WorkbenchDirectory({
  uri,
  onNavigate,
}: {
  uri: string
  onNavigate: (uri: string) => void
}) {
  const { t } = useTranslation('filesystem')
  const { connection, identityScopeKey } = useAppConnection()
  const { getTitle } = useSessionTitles(identityScopeKey)
  const ownSessionsRoot = `viking://user/${encodeURIComponent(connection.userId)}/sessions`
  const isSessionsRoot = uri.replace(/\/$/, '') === ownSessionsRoot
  const [showFiles, setShowFiles] = useState(false)
  const sessionId = uri.startsWith(`${ownSessionsRoot}/`)
    ? sessionIdFromDirectory(uri)
    : null
  const query = useVikingFsList(uri, {
    output: 'agent',
    showAllHidden: true,
    nodeLimit: 500,
  })
  const entries = sortTreeEntries(
    visibleContextEntries(query.data?.entries ?? []),
  )
  if (
    isExperienceDirectory(uri) &&
    query.isSuccess &&
    !entries.some((entry) => entry.isDir)
  )
    return (
      <div className={styles.directory}>
        <AgentExperienceList
          key={uri}
          directoryUri={uri.replace(/\/$/, '')}
          onOpenFile={onNavigate}
        />
      </div>
    )
  if (sessionId && !showFiles)
    return (
      <div className="flex h-full min-h-0 flex-col bg-background">
        <div className="flex justify-end border-b px-4 py-2">
          <Button size="sm" variant="ghost" onClick={() => setShowFiles(true)}>
            {t('ide.sessionFiles')}
          </Button>
        </div>
        <div className="min-h-0 flex-1">
          <Thread key={sessionId} sessionId={sessionId} />
        </div>
      </div>
    )
  return (
    <div className={styles.directory}>
      <div className={styles.directoryHeading}>
        <h1>
          {uri === 'viking://'
            ? t('ide.workspace')
            : isSessionsRoot
              ? t('ide.sessions')
              : fileNameFromUri(uri.replace(/\/$/, ''))}
        </h1>
        {sessionId ? (
          <Button size="sm" variant="ghost" onClick={() => setShowFiles(false)}>
            {t('ide.sessionPreview')}
          </Button>
        ) : null}
      </div>
      {query.isLoading ? (
        <div
          role="status"
          className="flex items-center gap-2 py-8 text-sm text-muted-foreground"
        >
          <LoaderCircleIcon className="size-4 animate-spin" />
          {t('explorer.loading')}
        </div>
      ) : query.isError ? (
        <div role="alert" className="py-8 text-sm text-destructive">
          {t('ide.loadFailed')}{' '}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void query.refetch()}
          >
            {t('agent.retry')}
          </Button>
        </div>
      ) : entries.length === 0 ? (
        <p className="py-8 text-sm text-muted-foreground">
          {t('ide.emptyDirectory')}
        </p>
      ) : (
        <div className={styles.directoryList}>
          {entries.map((entry) => (
            <button
              key={entry.uri}
              type="button"
              onClick={() => onNavigate(entry.uri)}
              className={styles.directoryEntry}
            >
              {entry.isDir ? <FolderIcon /> : <FileTextIcon />}
              <span>
                <strong>
                  {isSessionsRoot ? getTitle(entry.name) : entry.name}
                </strong>
                {entry.abstract &&
                !entry.abstract.includes(
                  '[Directory abstract is not ready]',
                ) ? (
                  <small>{entry.abstract}</small>
                ) : null}
              </span>
              <ChevronRightIcon className="ml-auto" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
