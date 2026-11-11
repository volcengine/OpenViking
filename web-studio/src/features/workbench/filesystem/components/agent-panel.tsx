import { useDefaultConversationTitles } from '#/features/workbench/sessions/lib/use-default-conversation-titles'
import type { VikingBotSessions } from '@openviking/react/components/viking-bot'
import { VikingBot } from '@openviking/react/components/viking-bot'
import '@openviking/react/components/viking-bot/style.css'
import {
  CircleAlertIcon,
  HistoryIcon,
  Loader2Icon,
  SquarePenIcon,
  Trash2Icon,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { Composer } from '#/features/workbench/sessions/components/composer'
import { DeleteConversation } from '#/features/workbench/sessions/components/delete-conversation'
import { MessageList } from '#/features/workbench/sessions/components/message-list'
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
import { Popover, PopoverContent, PopoverTrigger } from '#/shared/ui/popover'

import type { ResourceOpenHandler } from '#/features/workbench/filesystem/lib/types'
import {
  getErrorMessage,
  readFilesystemAgentSessionIds,
  registerFilesystemAgentSessionId,
} from '#/features/workbench/filesystem/lib/utils'

export function AgentPanel({
  initialSessionId,
  onOpenResource,
  onSessionChange,
  toolbarContainer,
}: {
  initialSessionId?: string
  onOpenResource: ResourceOpenHandler
  onSessionChange: (sessionId: string) => void
  toolbarContainer: HTMLDivElement | null
}) {
  const { t } = useTranslation('filesystem')
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
  const scrollRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [
    chat.messages.length,
    chat.streamingContent,
    chat.streamingReasoning,
    chat.streamingToolCalls,
    chat.streamingInsertIndex,
  ])

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
  const sessionTitle = getTitle(sessionId)
  const displayedSessionTitle =
    sessionTitle === sessionId ? t('agent.newSessionTitle') : sessionTitle
  const reversedSessions = useMemo(() => {
    return sessions.filter((session) =>
      isVikingBotWebSession(session, filesystemSessionIds),
    )
  }, [sessions, filesystemSessionIds])

  return (
    <>
      <AgentToolbar
        container={toolbarContainer}
        historyLabel={t('agent.history')}
        isCreatingSession={isCreatingSession}
        newSessionLabel={t('agent.newSession')}
        onNewSession={() => void handleNewSession()}
        sessions={{
          items: reversedSessions.map((session) => ({
            id: session.session_id,
            title: getTitle(session.session_id),
          })),
          currentId: sessionId,
          isLoading: isLoadingSessions,
          onSelect: handleSwitchSession,
          renderAction: (session) => (
            <SessionDeleteAction
              title={session.title}
              onClick={() => setDeleteSessionId(session.id)}
            />
          ),
        }}
        sessionTitle={displayedSessionTitle}
        showNewSession={Boolean(
          persistedSessionId || chat.messages.length || sessionError,
        )}
      />
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
      <div className="flex min-h-0 flex-1 flex-col">
        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
        >
          {botHealth.isLoading ? (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2Icon className="size-4 animate-spin" />
              {t('agent.detectingBot')}
            </div>
          ) : botModeError ? (
            <BotModePrompt
              detail={botModeError}
              onRetry={() => void botHealth.refetch()}
            />
          ) : sessionError ? (
            <div className="grid gap-3 rounded-lg border border-destructive/25 bg-destructive/5 p-3 text-sm text-destructive">
              <div>{t('agent.createFailed', { error: sessionError })}</div>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="w-fit"
                onClick={() => {
                  setSessionError(null)
                  void handleNewSession()
                }}
              >
                {t('agent.retry')}
              </Button>
            </div>
          ) : chat.messages.length === 0 && !isStreaming ? (
            <AgentEmptyState onSend={send} />
          ) : (
            <MessageList
              layout="expanded"
              messages={chat.messages}
              onResourceClick={onOpenResource}
              streaming={
                isStreaming
                  ? {
                      iteration: chat.iteration,
                      parts: chat.streamingParts,
                      insertIndex: chat.streamingInsertIndex,
                    }
                  : undefined
              }
            />
          )}
          <div ref={bottomRef} />
        </div>

        {botModeError ? (
          <div className="border-t bg-background/80 px-4 py-3 text-center text-sm text-muted-foreground">
            {t('agent.botDisabledFooter')}
          </div>
        ) : (
          <div className="border-t bg-background/80">
            <Composer
              key={sessionId}
              variant="compact"
              isStreaming={isStreaming}
              onCancel={chat.abort}
              onSend={send}
            />
          </div>
        )}
      </div>
    </>
  )
}

export function AgentToolbar({
  sessions,
  container,
  historyLabel,
  isCreatingSession,
  newSessionLabel,
  onNewSession,
  onOpenHistory,
  sessionTitle,
  showNewSession,
}: {
  sessions?: Omit<VikingBotSessions, 'onNew'> & {
    renderAction?: (
      session: VikingBotSessions['items'][number],
    ) => React.ReactNode
  }
  container: HTMLDivElement | null
  historyLabel: string
  isCreatingSession: boolean
  newSessionLabel: string
  onNewSession: () => void
  onOpenHistory?: () => void
  sessionTitle: string
  showNewSession: boolean
}) {
  const [open, setOpen] = useState(false)
  if (!container) return null

  return createPortal(
    <>
      <span
        className="min-w-0 max-w-48 truncate px-1 text-sm font-medium"
        title={sessionTitle}
        data-agent-session-title
      >
        {sessionTitle}
      </span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={<Button variant="ghost" size="icon-sm" />}
          aria-label={historyLabel}
          title={historyLabel}
          onClick={onOpenHistory}
        >
          <HistoryIcon className="size-3.5" />
        </PopoverTrigger>
        <PopoverContent align="end" className="max-h-72 w-64 overflow-auto p-1">
          {sessions?.isLoading ? (
            <Loader2Icon className="mx-auto size-4 animate-spin" />
          ) : null}
          {sessions?.items.map((session) => (
            <div key={session.id} className="flex min-w-0 items-center gap-1">
              <Button
                variant="ghost"
                className="min-w-0 flex-1 justify-start"
                aria-current={session.id === sessions.currentId || undefined}
                onClick={() => {
                  sessions.onSelect(session.id)
                  setOpen(false)
                }}
              >
                <span className="truncate">{session.title}</span>
              </Button>
              {sessions.renderAction?.(session)}
            </div>
          ))}
        </PopoverContent>
      </Popover>
      {showNewSession ? (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={newSessionLabel}
          title={newSessionLabel}
          disabled={isCreatingSession}
          onClick={onNewSession}
        >
          <SquarePenIcon className="size-3.5" />
        </Button>
      ) : null}
    </>,
    container,
  )
}

export function BotModePrompt({
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

export function AgentEmptyState({
  onSend,
}: {
  onSend: (message: string) => void
}) {
  const { t } = useTranslation('filesystem')
  const prompts = t('agent.empty.prompts', {
    returnObjects: true,
  }) as string[]
  return (
    <VikingBot
      messages={[]}
      composer={null}
      style={{ minHeight: '100%', height: 'auto' }}
      labels={{
        welcomeTitle: t('agent.empty.heading'),
        welcomeDescription: t('agent.empty.body'),
      }}
      quickInputs={prompts.map((prompt) => ({
        id: prompt,
        label: prompt,
        prompt,
      }))}
      onQuickInputSelect={(input) => onSend(input.prompt)}
    />
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
