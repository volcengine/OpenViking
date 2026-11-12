import { useDefaultConversationTitles } from '#/features/workbench/sessions/lib/use-default-conversation-titles'
import type { VikingBotProps } from '@openviking/react/components/viking-bot'
import { CircleAlertIcon, Loader2Icon, Trash2Icon } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { Composer } from '#/features/workbench/sessions/components/composer'
import { DeleteConversation } from '#/features/workbench/sessions/components/delete-conversation'
import {
  sharedMessages,
  sharedPart,
} from '#/features/workbench/sessions/lib/shared-messages'
import { useChat } from '#/features/workbench/sessions/lib/use-chat'
import { useSessionTitles } from '#/features/workbench/sessions/lib/use-session-titles'
import {
  useBotHealth,
  useCreateSession,
  useSessionListByRecency,
  useSessionMessages,
} from '#/features/workbench/sessions/lib/use-sessions'
import {
  createVikingBotWebSessionId,
  isVikingBotWebSession,
} from '#/features/workbench/sessions/lib/vikingbot-sessions'
import { Button } from '#/shared/ui/button'
import { copyTextToClipboard } from '#/shared/lib/clipboard'

import {
  getErrorMessage,
  readFilesystemAgentSessionIds,
  registerFilesystemAgentSessionId,
} from '#/features/workbench/filesystem/lib/utils'

export function AgentPanel({
  initialSessionId,
  onSessionChange,
  children,
}: {
  initialSessionId?: string
  onSessionChange: (sessionId: string) => void
  children: (conversation: Partial<VikingBotProps>) => ReactNode
}) {
  const { t } = useTranslation(['filesystem', 'sessions'])
  const { identityScopeKey } = useAppConnection()
  const [deleteSessionId, setDeleteSessionId] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState(
    initialSessionId ?? createVikingBotWebSessionId(),
  )
  const [sessionError, setSessionError] = useState<string | null>(null)
  const [isCreatingSession, setIsCreatingSession] = useState(false)
  const [historySessionId, setHistorySessionId] = useState(initialSessionId)
  const [persistedSessionId, setPersistedSessionId] = useState(initialSessionId)
  const creationStartedRef = useRef(false)
  const creationGenerationRef = useRef(0)
  useEffect(
    () => () => {
      creationGenerationRef.current += 1
    },
    [],
  )
  const botHealth = useBotHealth()
  const createSession = useCreateSession()
  const { data: sessions, isLoading: isLoadingSessions } =
    useSessionListByRecency()
  const { getTitle, removeTitle } = useSessionTitles(identityScopeKey)
  const [filesystemSessionIds, setFilesystemSessionIds] = useState<string[]>(
    () => readFilesystemAgentSessionIds(identityScopeKey),
  )
  useDefaultConversationTitles(
    identityScopeKey,
    sessions
      .filter((session) => isVikingBotWebSession(session, filesystemSessionIds))
      .map((session) => session.session_id),
  )
  const { data: historyMessages } = useSessionMessages(historySessionId)
  const chat = useChat({
    identityScopeKey,
    initialMessages: historyMessages,
    persistMessages: true,
    sessionId,
  })

  const handleNewSession = useCallback(() => {
    if (isCreatingSession) return
    creationGenerationRef.current += 1
    chat.abort({ closeLocalStream: true, discardPartial: true })
    chat.setMessages([])
    creationStartedRef.current = false
    setSessionError(null)
    setHistorySessionId(undefined)
    setPersistedSessionId(undefined)
    setSessionId(createVikingBotWebSessionId())
    onSessionChange('')
  }, [chat, isCreatingSession, onSessionChange])

  const handleSwitchSession = useCallback(
    (nextSessionId: string) => {
      creationGenerationRef.current += 1
      chat.abort({ closeLocalStream: true, discardPartial: true })
      creationStartedRef.current = false
      setSessionError(null)
      setIsCreatingSession(false)
      setFilesystemSessionIds(
        registerFilesystemAgentSessionId(nextSessionId, identityScopeKey),
      )
      setHistorySessionId(nextSessionId)
      setPersistedSessionId(nextSessionId)
      setSessionId(nextSessionId)
      onSessionChange(nextSessionId)
    },
    [chat, identityScopeKey, onSessionChange],
  )

  // Only publish IDs that exist on the server; drafts must not survive in the URL.
  useEffect(() => {
    if (persistedSessionId) {
      setFilesystemSessionIds(
        registerFilesystemAgentSessionId(persistedSessionId, identityScopeKey),
      )
    }
  }, [identityScopeKey, persistedSessionId])

  const send = useCallback(
    async (message: string) => {
      if (creationStartedRef.current) return false
      if (!persistedSessionId) {
        const generation = ++creationGenerationRef.current
        creationStartedRef.current = true
        setIsCreatingSession(true)
        setSessionError(null)
        try {
          const result = await createSession.mutateAsync(sessionId)
          if (generation !== creationGenerationRef.current) return false
          setPersistedSessionId(result.session_id)
          onSessionChange(result.session_id)
        } catch (error) {
          if (generation === creationGenerationRef.current) {
            setSessionError(getErrorMessage(error))
          }
          return false
        } finally {
          if (generation === creationGenerationRef.current) {
            creationStartedRef.current = false
            setIsCreatingSession(false)
          }
        }
      }
      const sending = chat.send(message)
      if (chat.status === 'streaming') {
        await sending
      }
      return true
    },
    [chat, createSession, onSessionChange, persistedSessionId, sessionId],
  )

  const isStreaming = chat.status === 'streaming'
  const botModeError = botHealth.isError ? getErrorMessage(botHealth.error) : ''
  const history = sessions.filter((session) =>
    isVikingBotWebSession(session, filesystemSessionIds),
  )
  const prompts = t('agent.empty.prompts', { returnObjects: true })
  const insertIndex = Math.max(
    0,
    Math.min(
      chat.streamingInsertIndex ?? chat.messages.length,
      chat.messages.length,
    ),
  )
  const before = sharedMessages(chat.messages.slice(0, insertIndex))
  const after = sharedMessages(chat.messages.slice(insertIndex))
  const conversation: Partial<VikingBotProps> = {
    messages: [...before, ...after],
    streaming: isStreaming
      ? {
          iteration: chat.iteration,
          parts: chat.streamingParts.map(sharedPart),
          insertIndex: before.length,
        }
      : undefined,
    listKey: sessionId,
    isSending: isStreaming,
    isAvailable: !botModeError,
    unavailableMessage: botModeError ? t('agent.botDisabledFooter') : undefined,
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
          .filter((prompt): prompt is string => typeof prompt === 'string')
          .map((prompt) => ({ id: prompt, label: prompt, prompt }))
      : [],
    onQuickInputSelect: (input) => void send(input.prompt),
    onCopy: (text) => void copyTextToClipboard(text),
    sessions: {
      items: history.map((session) => ({
        id: session.session_id,
        title: getTitle(session.session_id),
      })),
      currentId: persistedSessionId,
      isLoading: isLoadingSessions,
      onSelect: handleSwitchSession,
      onNew: handleNewSession,
    },
    headerActions: persistedSessionId ? (
      <SessionDeleteAction
        title={getTitle(persistedSessionId)}
        onClick={() => setDeleteSessionId(persistedSessionId)}
      />
    ) : undefined,
    messageList: botHealth.isLoading ? (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" />
        {t('agent.detectingBot')}
      </div>
    ) : botModeError ? (
      <BotModePrompt
        detail={botModeError}
        onRetry={() => void botHealth.refetch()}
      />
    ) : undefined,
    footer: sessionError ? (
      <div
        role="alert"
        className="flex items-center justify-between gap-2 px-4 py-2 text-sm text-destructive"
      >
        {t('agent.createFailed', { error: sessionError })}
        <Button size="sm" variant="outline" onClick={handleNewSession}>
          {t('agent.retry')}
        </Button>
      </div>
    ) : chat.error && !botModeError ? (
      <div role="alert" className="px-4 py-2 text-sm text-destructive">
        {t('sessions:chat.sendFailed', { error: chat.error })}
      </div>
    ) : undefined,
    composer: botModeError ? (
      false
    ) : (
      <Composer
        key={sessionId}
        variant="compact"
        isStreaming={isStreaming}
        onCancel={chat.abort}
        onSend={send}
      />
    ),
  }

  return (
    <>
      {children(conversation)}
      {deleteSessionId ? (
        <DeleteConversation
          defaultOpen
          hideTrigger
          onOpenChange={(open) => {
            if (!open) setDeleteSessionId(null)
          }}
          id={deleteSessionId}
          title={getTitle(deleteSessionId)}
          onDeleted={() => {
            setDeleteSessionId(null)
            removeTitle(deleteSessionId)
            setFilesystemSessionIds((ids) =>
              ids.filter((id) => id !== deleteSessionId),
            )
            if (deleteSessionId === sessionId) {
              creationGenerationRef.current += 1
              chat.abort({
                closeLocalStream: true,
                discardPartial: true,
              })
              chat.setMessages([])
              creationStartedRef.current = false
              setIsCreatingSession(false)
              setSessionError(null)
              setHistorySessionId(undefined)
              setPersistedSessionId(undefined)
              setSessionId(createVikingBotWebSessionId())
              onSessionChange('')
            }
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
