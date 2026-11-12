import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import type { VikingBotProps } from '@openviking/react/components/viking-bot'
import { Loader2Icon, CircleAlertIcon, Trash2Icon } from 'lucide-react'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { DeleteConversation } from '#/features/workbench/sessions/components/delete-conversation'
import { useDefaultConversationTitles } from '#/features/workbench/sessions/lib/use-default-conversation-titles'
import { useSessionTitles } from '#/features/workbench/sessions/lib/use-session-titles'
import {
  useBotHealth,
  useSessionListByRecency,
} from '#/features/workbench/sessions/lib/use-sessions'
import { isVikingBotWebSession } from '#/features/workbench/sessions/lib/vikingbot-sessions'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import { Button } from '#/shared/ui/button'
import {
  getErrorMessage,
  readFilesystemAgentSessionIds,
  registerFilesystemAgentSessionId,
} from '../lib/utils'

// Only host metadata and navigation live here. Console owns chat and its editor.
export function AgentPanel({
  initialSessionId,
  onSessionChange,
  children,
}: {
  initialSessionId?: string
  onSessionChange: (id: string) => void
  children: (conversation: Partial<VikingBotProps>) => ReactNode
}) {
  const { t } = useTranslation(['filesystem', 'sessions'])
  const { identityScopeKey } = useAppConnection()
  const [sessionId, setSessionId] = useState(initialSessionId)
  const [deleteSessionId, setDeleteSessionId] = useState<string>()
  const [filesystemSessionIds, setFilesystemSessionIds] = useState(() =>
    readFilesystemAgentSessionIds(identityScopeKey),
  )
  const health = useBotHealth()
  const sessions = useSessionListByRecency()
  const { getTitle, removeTitle } = useSessionTitles(identityScopeKey)
  useEffect(() => {
    setSessionId(initialSessionId)
  }, [initialSessionId])
  const history = sessions.data.filter((item) =>
    isVikingBotWebSession(item, filesystemSessionIds),
  )
  useDefaultConversationTitles(
    identityScopeKey,
    history.map((item) => item.session_id),
  )
  const select = useCallback(
    (id: string) => {
      setSessionId(id)
      setFilesystemSessionIds(
        registerFilesystemAgentSessionId(id, identityScopeKey),
      )
      onSessionChange(id)
    },
    [identityScopeKey, onSessionChange],
  )
  const reset = useCallback(() => {
    setSessionId(undefined)
    onSessionChange('')
  }, [onSessionChange])
  const error = health.isError ? getErrorMessage(health.error) : ''
  const prompts = t('agent.empty.prompts', { returnObjects: true })
  const conversation: Partial<VikingBotProps> = {
    isAvailable: !error && !health.isLoading,
    unavailableMessage: error ? t('agent.botDisabledFooter') : undefined,
    labels: {
      welcomeTitle: t('agent.empty.heading'),
      welcomeDescription: t('agent.empty.body'),
      sessionHistory: t('agent.history'),
      newSession: t('agent.newSession'),
      thinking: t('sessions:chat.generating'),
      reasoning: t('sessions:chat.reasoning'),
      copy: t('sessions:chat.copy'),
    },
    quickInputs: Array.isArray(prompts)
      ? prompts
          .filter((item): item is string => typeof item === 'string')
          .map((prompt) => ({ id: prompt, label: prompt, prompt }))
      : [],
    onCopy: (text) => void copyTextToClipboard(text),
    sessions: {
      items: history.map((item) => ({
        id: item.session_id,
        title: getTitle(item.session_id),
      })),
      currentId: sessionId,
      isLoading: sessions.isLoading,
      onSelect: select,
      onNew: reset,
    },
    headerActions: sessionId ? (
      <SessionDeleteAction
        title={getTitle(sessionId)}
        onClick={() => setDeleteSessionId(sessionId)}
      />
    ) : undefined,
    messageList: health.isLoading ? (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" />
        {t('agent.detectingBot')}
      </div>
    ) : error ? (
      <BotModePrompt detail={error} onRetry={() => void health.refetch()} />
    ) : undefined,
    composer: error ? false : undefined,
  }
  return (
    <>
      {children(conversation)}
      {deleteSessionId ? (
        <DeleteConversation
          defaultOpen
          hideTrigger
          id={deleteSessionId}
          title={getTitle(deleteSessionId)}
          onOpenChange={(open) => {
            if (!open) setDeleteSessionId(undefined)
          }}
          onDeleted={() => {
            setDeleteSessionId(undefined)
            removeTitle(deleteSessionId)
            setFilesystemSessionIds((ids) =>
              ids.filter((id) => id !== deleteSessionId),
            )
            if (deleteSessionId === sessionId) reset()
          }}
        />
      ) : null}
    </>
  )
}

function BotModePrompt({
  detail,
  onRetry,
}: {
  detail: string
  onRetry: () => void
}) {
  const { t } = useTranslation('filesystem')
  return (
    <div className="flex h-full items-center justify-center">
      <div className="grid max-w-md gap-4 rounded-xl border border-primary/25 bg-primary/5 p-5 text-sm">
        <div className="flex items-start gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <CircleAlertIcon className="size-4" />
          </div>
          <div className="min-w-0">
            <div className="text-base font-semibold text-foreground">
              {t('agent.botPrompt.title')}
            </div>
            <p className="mt-1 leading-6 text-muted-foreground">
              {t('agent.botPrompt.description')}
            </p>
          </div>
        </div>
        <code className="rounded-lg border bg-background px-3 py-2 font-mono text-xs text-foreground">
          {t('agent.botPrompt.command')}
        </code>
        {detail ? (
          <div className="rounded-lg bg-muted/50 px-3 py-2 text-xs leading-5 text-muted-foreground">
            {detail}
          </div>
        ) : null}
        <Button
          type="button"
          variant="outline"
          className="w-fit"
          onClick={onRetry}
        >
          {t('agent.botPrompt.retry')}
        </Button>
      </div>
    </div>
  )
}

function SessionDeleteAction({
  title,
  onClick,
}: {
  title: string
  onClick: () => void
}) {
  const { t } = useTranslation('sessions')
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={t('threadList.deleteSession', { title })}
      onClick={onClick}
    >
      <Trash2Icon className="size-4" />
    </Button>
  )
}
