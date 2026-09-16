import { useDefaultConversationTitles } from '#/lib/sessions/use-default-conversation-titles'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import {
  CheckCircle2Icon,
  CircleAlertIcon,
  HistoryIcon,
  Loader2Icon,
  SparklesIcon,
  SquarePenIcon,
} from 'lucide-react'

import { DeleteConversation } from '#/components/sessions/delete-conversation'
import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { cn } from '#/lib/utils'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  createVikingBotWebSessionId,
  isVikingBotWebSession,
} from '#/lib/sessions/vikingbot-sessions'
import { useChat } from '#/lib/sessions/use-chat'
import {
  useBotHealth,
  useCreateSession,
  useSessionListByRecency,
  useSessionMessages,
} from '#/lib/sessions/use-sessions'
import { useSessionTitles } from '#/lib/sessions/use-session-titles'
import { Composer } from '#/routes/sessions/-components/composer'
import { MessageList } from '#/routes/sessions/-components/message-list'

import type { ResourceOpenHandler } from '../-lib/types'
import {
  getErrorMessage,
  readPlaygroundAgentSessionIds,
  registerPlaygroundAgentSessionId,
} from '../-lib/utils'

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
  const { t } = useTranslation('playground')
  const { identityScopeKey } = useAppConnection()
  const [sessionId, setSessionId] = useState(
    initialSessionId ?? createVikingBotWebSessionId(),
  )
  const [historyOpen, setHistoryOpen] = useState(false)
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
  const [playgroundSessionIds, setPlaygroundSessionIds] = useState<string[]>(
    () => readPlaygroundAgentSessionIds(identityScopeKey),
  )
  useDefaultConversationTitles(
    identityScopeKey,
    sessions
      .filter((session) => isVikingBotWebSession(session, playgroundSessionIds))
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
    chat.abort()
    chat.setMessages([])
    creationStartedRef.current = false
    setSessionError(null)
    setHistorySessionId(undefined)
    setPersistedSessionId(undefined)
    setSessionId(createVikingBotWebSessionId())
    onSessionChange('')
    setHistoryOpen(false)
  }, [chat, isCreatingSession, onSessionChange])

  const handleSwitchSession = useCallback(
    (nextSessionId: string) => {
      creationGenerationRef.current += 1
      chat.abort()
      creationStartedRef.current = false
      setSessionError(null)
      setIsCreatingSession(false)
      setPlaygroundSessionIds(
        registerPlaygroundAgentSessionId(nextSessionId, identityScopeKey),
      )
      setHistorySessionId(nextSessionId)
      setPersistedSessionId(nextSessionId)
      setSessionId(nextSessionId)
      onSessionChange(nextSessionId)
      setHistoryOpen(false)
    },
    [chat, identityScopeKey, onSessionChange],
  )

  // Only publish IDs that exist on the server; drafts must not survive in the URL.
  useEffect(() => {
    if (persistedSessionId) {
      setPlaygroundSessionIds(
        registerPlaygroundAgentSessionId(persistedSessionId, identityScopeKey),
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
      void chat.send(message)
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
      isVikingBotWebSession(session, playgroundSessionIds),
    )
  }, [sessions, playgroundSessionIds])

  return (
    <>
      <AgentToolbar
        container={toolbarContainer}
        historyLabel={t('agent.history')}
        isCreatingSession={isCreatingSession}
        newSessionLabel={t('agent.newSession')}
        onNewSession={() => void handleNewSession()}
        onOpenHistory={() => setHistoryOpen(true)}
        sessionTitle={displayedSessionTitle}
      />
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

      <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
        <DialogContent className="gap-4 sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('agent.historyTitle')}</DialogTitle>
            <DialogDescription>
              {t('agent.historyDescription')}
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[420px] overflow-y-auto pr-1">
            {isLoadingSessions ? (
              <div className="flex items-center gap-2 rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">
                <Loader2Icon className="size-4 animate-spin" />
                {t('agent.loadingSessions')}
              </div>
            ) : reversedSessions.length === 0 ? (
              <div className="rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">
                {t('agent.noSessions')}
              </div>
            ) : (
              <div className="grid gap-2">
                {reversedSessions.map((session) => {
                  const active = session.session_id === sessionId
                  const title = getTitle(session.session_id)

                  return (
                    <div
                      key={session.session_id}
                      className="group/conversation flex items-center gap-1"
                    >
                      <button
                        type="button"
                        className={cn(
                          'flex min-w-0 flex-1 items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors hover:border-primary/45 hover:bg-muted/45',
                          active
                            ? 'border-primary/60 bg-primary/10'
                            : 'border-border bg-background',
                        )}
                        onClick={() => handleSwitchSession(session.session_id)}
                      >
                        <HistoryIcon className="size-4 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-foreground">
                            {title}
                          </span>
                          <span className="block truncate font-mono text-[11px] text-muted-foreground">
                            {session.session_id}
                          </span>
                        </span>
                        {active ? (
                          <CheckCircle2Icon className="size-4 shrink-0 text-primary" />
                        ) : null}
                      </button>
                      <DeleteConversation
                        id={session.session_id}
                        title={title}
                        onDeleted={() => {
                          removeTitle(session.session_id)
                          setPlaygroundSessionIds((ids) =>
                            ids.filter((id) => id !== session.session_id),
                          )
                          if (session.session_id === sessionId) {
                            creationGenerationRef.current += 1
                            chat.abort()
                            chat.setMessages([])
                            creationStartedRef.current = false
                            setIsCreatingSession(false)
                            setSessionError(null)
                            setHistorySessionId(undefined)
                            setPersistedSessionId(undefined)
                            setSessionId(createVikingBotWebSessionId())
                            onSessionChange('')
                            setHistoryOpen(false)
                          }
                        }}
                      />
                    </div>
                  )
                })}
              </div>
            )}
          </div>
          <Button
            type="button"
            className="w-full"
            onClick={() => void handleNewSession()}
            disabled={isCreatingSession}
          >
            {isCreatingSession ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <SquarePenIcon className="size-4" />
            )}
            {t('agent.newSession')}
          </Button>
        </DialogContent>
      </Dialog>
    </>
  )
}

export function AgentToolbar({
  container,
  historyLabel,
  isCreatingSession,
  newSessionLabel,
  onNewSession,
  onOpenHistory,
  sessionTitle,
}: {
  container: HTMLDivElement | null
  historyLabel: string
  isCreatingSession: boolean
  newSessionLabel: string
  onNewSession: () => void
  onOpenHistory: () => void
  sessionTitle: string
}) {
  if (!container) return null

  return createPortal(
    <>
      <span
        className="min-w-0 max-w-48 truncate px-1 text-sm font-medium text-foreground"
        title={sessionTitle}
      >
        {sessionTitle}
      </span>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="size-7 shrink-0"
        title={historyLabel}
        onClick={onOpenHistory}
      >
        <HistoryIcon className="size-3.5" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="size-7 shrink-0"
        title={newSessionLabel}
        disabled={isCreatingSession}
        onClick={onNewSession}
      >
        {isCreatingSession ? (
          <Loader2Icon className="size-3.5 animate-spin" />
        ) : (
          <SquarePenIcon className="size-3.5" />
        )}
      </Button>
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
  const { t } = useTranslation('playground')
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
  const { t } = useTranslation('playground')
  const prompts = t('agent.empty.prompts', {
    returnObjects: true,
  }) as string[]
  return (
    <div className="flex h-full flex-col justify-end gap-4 pb-4">
      <div className="rounded-xl border bg-background p-4">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
          <SparklesIcon className="size-4 text-primary" />
          {t('agent.empty.heading')}
        </div>
        <p className="text-sm leading-6 text-muted-foreground">
          {t('agent.empty.body')}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {prompts.map((prompt) => (
          <button
            key={prompt}
            type="button"
            className="rounded-lg border bg-background px-3 py-2 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
            onClick={() => onSend(prompt)}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  )
}
