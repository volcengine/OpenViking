import * as React from 'react'
import { Link, useNavigate, useRouterState } from '@tanstack/react-router'
import {
  BlocksIcon,
  BookOpenIcon,
  ChevronRightIcon,
  FolderTreeIcon,
  HomeIcon,
  GithubIcon,
  KeyRoundIcon,
  LoaderIcon,
  MessageSquareIcon,
  MoonIcon,
  PlusIcon,
  PlugZapIcon,
  ScrollTextIcon,
  SearchIcon,
  SunIcon,
  TrashIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from 'next-themes'

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '#/components/ui/collapsible'
import { ConnectionDialog } from '#/components/connection-dialog'
import { OAuthSetupDialog } from '#/components/oauth-setup-dialog'
import { ScrollArea } from '#/components/ui/scroll-area'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  SidebarTrigger,
} from '#/components/ui/sidebar'
import { AppConnectionProvider } from '#/hooks/use-app-connection'
import { cn } from '#/lib/utils'
import {
  useSessionList,
  useCreateSession,
  useDeleteSession,
} from '#/lib/sessions/use-sessions'
import {
  useSessionTitles,
  setSessionTitle,
  removeSessionTitle,
} from '#/lib/sessions/use-session-titles'

type NavItem = {
  icon: React.ComponentType
  id: string
  titleKey: string
  to: string
  children?: readonly NavSubItem[]
}

type NavSubItem = {
  icon: React.ComponentType
  id: string
  titleKey: string
  to: string
}

type NavGroupItemProps = {
  item: NavItem & { children: readonly NavSubItem[] }
  pathname: string
  title: string
  t: ReturnType<typeof useTranslation>['t']
}

const NAV_ITEMS: readonly NavItem[] = [
  {
    icon: HomeIcon,
    id: 'home',
    titleKey: 'navigation.home.title',
    to: '/home',
  },
  {
    icon: PlugZapIcon,
    id: 'studio',
    titleKey: 'navigation.studio.title',
    to: '/studio',
  },
  {
    icon: FolderTreeIcon,
    id: 'resources',
    titleKey: 'navigation.resources.title',
    to: '/resources',
  },
  {
    icon: SearchIcon,
    id: 'retrieval',
    titleKey: 'navigation.retrieval.title',
    to: '/retrieval',
  },
  {
    icon: ScrollTextIcon,
    id: 'requestLogs',
    titleKey: 'navigation.requestLogs.title',
    to: '/request-logs',
  },
  {
    icon: BlocksIcon,
    id: 'sessions',
    titleKey: 'navigation.sessions.title',
    to: '/sessions',
  },
] as const

const LANGUAGE_OPTIONS = [
  {
    shortLabel: '中',
    title: '中文',
    value: 'zh-CN',
  },
  {
    shortLabel: 'EN',
    title: 'English',
    value: 'en',
  },
] as const

const HEADER_ICON_BUTTON_CLASS =
  'relative inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-border/80 bg-muted/60 text-muted-foreground shadow-xs transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2'

function resolveLanguage(
  value: string | undefined,
): (typeof LANGUAGE_OPTIONS)[number]['value'] {
  if (value?.toLowerCase().startsWith('zh')) {
    return 'zh-CN'
  }

  return 'en'
}

function NavGroupItem({ item, pathname, title, t }: NavGroupItemProps) {
  const Icon = item.icon
  const isActive = pathname === item.to || pathname.startsWith(`${item.to}/`)
  const [open, setOpen] = React.useState(isActive)

  React.useEffect(() => {
    if (isActive) {
      setOpen(true)
    }
  }, [isActive])

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="group/collapsible"
    >
      <SidebarMenuItem>
        <CollapsibleTrigger
          render={
            <SidebarMenuButton tooltip={title}>
              <Icon />
              <span>{title}</span>
              <ChevronRightIcon className="ml-auto transition-transform duration-200 group-data-[open]/collapsible:rotate-90" />
            </SidebarMenuButton>
          }
        />
        <CollapsibleContent>
          <SidebarMenuSub>
            {item.children.map((child) => {
              const ChildIcon = child.icon
              const childActive =
                pathname === child.to ||
                (child.to !== item.to && pathname.startsWith(`${child.to}/`))
              const childTitle = t(child.titleKey, { ns: 'appShell' })

              return (
                <SidebarMenuSubItem key={child.id}>
                  <SidebarMenuSubButton
                    render={<Link to={child.to} />}
                    isActive={childActive}
                  >
                    <ChildIcon />
                    <span>{childTitle}</span>
                  </SidebarMenuSubButton>
                </SidebarMenuSubItem>
              )
            })}
          </SidebarMenuSub>
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
  )
}

function NavSessionsItem({
  pathname,
  title,
}: {
  pathname: string
  title: string
}) {
  const { t } = useTranslation(['appShell', 'sessions'])
  const navigate = useNavigate()
  const isActive = pathname === '/sessions' || pathname.startsWith('/sessions/')
  const [open, setOpen] = React.useState(isActive)

  const { data: sessions, isLoading } = useSessionList()
  const { getTitle } = useSessionTitles()
  const createSession = useCreateSession()
  const deleteSession = useDeleteSession()

  const activeSessionId = useRouterState({
    select: (s) => {
      const search = s.location.search as { s?: string }
      return search.s ?? null
    },
  })

  React.useEffect(() => {
    if (isActive) setOpen(true)
  }, [isActive])

  const handleNewSession = React.useCallback(async () => {
    const result = await createSession.mutateAsync(undefined)
    setSessionTitle(
      result.session_id,
      t('threadList.newSession', { ns: 'sessions' }),
    )
    void navigate({ to: '/sessions', search: { s: result.session_id } })
  }, [createSession, navigate, t])

  const handleDeleteSession = React.useCallback(
    async (e: React.MouseEvent, id: string) => {
      e.stopPropagation()
      e.preventDefault()
      await deleteSession.mutateAsync(id)
      removeSessionTitle(id)
      if (activeSessionId === id) {
        void navigate({
          to: '/sessions',
          search: { s: undefined } as { s?: string },
        })
      }
    },
    [deleteSession, activeSessionId, navigate],
  )

  const reversedSessions = React.useMemo(
    () => (sessions ?? []).slice().reverse(),
    [sessions],
  )

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="group/collapsible"
    >
      <SidebarMenuItem>
        <CollapsibleTrigger
          render={
            <SidebarMenuButton tooltip={title} className="text-base">
              <BlocksIcon />
              <span>{title}</span>
              <ChevronRightIcon className="ml-auto transition-transform duration-200 group-data-[open]/collapsible:rotate-90" />
            </SidebarMenuButton>
          }
        />
        <SidebarMenuAction
          onClick={handleNewSession}
          title={t('threadList.newSession', { ns: 'sessions' })}
        >
          <PlusIcon className="size-4" />
        </SidebarMenuAction>
        <CollapsibleContent>
          <SidebarMenuSub>
            {isLoading ? (
              <SidebarMenuSubItem>
                <div className="flex items-center gap-2 px-2 py-1.5 text-xs text-muted-foreground">
                  <LoaderIcon className="size-3 animate-spin" />
                  <span>
                    {t('sidebar.loadingSessions', { ns: 'appShell' })}
                  </span>
                </div>
              </SidebarMenuSubItem>
            ) : reversedSessions.length === 0 ? (
              <SidebarMenuSubItem>
                <div className="px-2 py-1.5 text-xs text-muted-foreground">
                  {t('sidebar.noSessions', { ns: 'appShell' })}
                </div>
              </SidebarMenuSubItem>
            ) : (
              reversedSessions.map((s) => {
                const sessionTitle = getTitle(s.session_id)
                const isSessionActive = activeSessionId === s.session_id

                return (
                  <SidebarMenuSubItem
                    key={s.session_id}
                    className="group/session"
                  >
                    <SidebarMenuSubButton
                      render={
                        <Link to="/sessions" search={{ s: s.session_id }} />
                      }
                      isActive={isSessionActive}
                    >
                      <MessageSquareIcon className="size-3.5 shrink-0 opacity-60" />
                      <span className="truncate">{sessionTitle}</span>
                    </SidebarMenuSubButton>
                    <button
                      type="button"
                      onClick={(e) => handleDeleteSession(e, s.session_id)}
                      className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover/session:opacity-100"
                    >
                      <TrashIcon className="size-3" />
                    </button>
                  </SidebarMenuSubItem>
                )
              })
            )}
          </SidebarMenuSub>
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
  )
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AppConnectionProvider>
      <AppShellInner>{children}</AppShellInner>
    </AppConnectionProvider>
  )
}

function AppShellInner({ children }: { children: React.ReactNode }) {
  const { i18n, t } = useTranslation(['appShell', 'common'])
  const navigate = useNavigate()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const { setTheme, resolvedTheme } = useTheme()
  const currentLanguage = resolveLanguage(
    i18n.resolvedLanguage ?? i18n.language,
  )
  const [oauthSetupOpen, setOauthSetupOpen] = React.useState(false)
  const settingsActive = pathname === '/settings'
  const oauthSetupActive =
    pathname === '/oauth/setup' || pathname.startsWith('/oauth/setup/')

  function openOAuthSetup(): void {
    if (oauthSetupActive) {
      return
    }
    // Desktop: open the dialog so the user keeps the current page underneath.
    // Phone / narrow tablets: navigate to the dedicated page since fullscreen
    // dialogs are awkward to dismiss on mobile.
    const useDialog =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(min-width: 768px)').matches
    if (useDialog) {
      setOauthSetupOpen(true)
    } else {
      void navigate({ to: '/oauth/setup' })
    }
  }

  return (
    <SidebarProvider
      defaultOpen
      className="flex h-svh overflow-hidden bg-sidebar"
    >
      <Sidebar variant="sidebar" collapsible="icon" className="!border-r-0">
        <SidebarHeader className="border-b border-sidebar-border/70 p-2">
          <div className="flex items-center justify-between gap-2 group-data-[collapsible=icon]:justify-center">
            <span className="truncate px-2 text-base font-semibold group-data-[collapsible=icon]:hidden">
              {t('sidebar.workspaceGroupLabel', { ns: 'appShell' })}
            </span>
            <SidebarTrigger className="hidden shrink-0 md:inline-flex" />
          </div>
        </SidebarHeader>

        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupContent>
              <SidebarMenu>
                {NAV_ITEMS.map((item) => {
                  const isActive =
                    pathname === item.to || pathname.startsWith(`${item.to}/`)
                  const title = t(item.titleKey, { ns: 'appShell' })

                  if (item.id === 'sessions') {
                    return (
                      <NavSessionsItem
                        key={item.id}
                        pathname={pathname}
                        title={title}
                      />
                    )
                  }

                  if (item.children) {
                    return (
                      <NavGroupItem
                        key={item.id}
                        item={
                          item as NavItem & { children: readonly NavSubItem[] }
                        }
                        pathname={pathname}
                        title={title}
                        t={t}
                      />
                    )
                  }

                  const Icon = item.icon

                  return (
                    <SidebarMenuItem key={item.id}>
                      <SidebarMenuButton
                        render={<Link to={item.to} />}
                        isActive={isActive}
                        tooltip={title}
                        className="text-base"
                      >
                        <Icon />
                        <span>{title}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  )
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>

        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton
                render={<Link to="/settings" />}
                isActive={settingsActive}
                tooltip={t('footer.connection', { ns: 'appShell' })}
                className="text-base"
              >
                <PlugZapIcon className="size-5" />
                <span>{t('footer.connection', { ns: 'appShell' })}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                onClick={openOAuthSetup}
                isActive={oauthSetupActive}
                tooltip={t('navigation.oauthSetup.title', { ns: 'appShell' })}
                className="text-base"
              >
                <KeyRoundIcon className="size-5" />
                <span>
                  {t('navigation.oauthSetup.title', { ns: 'appShell' })}
                </span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                render={
                  <a
                    href="https://github.com/volcengine/OpenViking"
                    target="_blank"
                    rel="noreferrer"
                  />
                }
                tooltip={t('footer.github', { ns: 'appShell' })}
                className="text-base"
              >
                <GithubIcon className="size-5" />
                <span>{t('footer.github', { ns: 'appShell' })}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                render={
                  <a
                    href="https://docs.openviking.ai/"
                    target="_blank"
                    rel="noreferrer"
                  />
                }
                tooltip={t('footer.docs', { ns: 'appShell' })}
                className="text-base"
              >
                <BookOpenIcon className="size-5" />
                <span>{t('footer.docs', { ns: 'appShell' })}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      </Sidebar>

      <SidebarInset className="min-h-0 flex-1 overflow-hidden rounded-none border-0 bg-background shadow-none ring-0 md:m-0 md:ml-0">
        <header className="flex h-12 shrink-0 items-center justify-end border-b border-border/70 bg-background px-4 backdrop-blur-md md:px-6">
          <SidebarTrigger className="mr-auto shrink-0 md:hidden" />
          <div className="flex items-center gap-3">
            <div
              aria-label={t('language.label', { ns: 'common' })}
              className="flex h-10 items-center rounded-2xl border border-border/80 bg-muted/60 p-1 text-xs shadow-xs"
              role="group"
            >
              {LANGUAGE_OPTIONS.map((item) => {
                const isActive = item.value === currentLanguage

                return (
                  <button
                    key={item.value}
                    type="button"
                    aria-pressed={isActive}
                    className={cn(
                      'h-8 min-w-10 rounded-xl px-2 text-xs font-semibold text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                      isActive && 'bg-foreground text-background shadow-sm',
                    )}
                    onClick={() => {
                      if (!isActive) {
                        void i18n.changeLanguage(item.value)
                      }
                    }}
                  >
                    {item.shortLabel}
                  </button>
                )
              })}
            </div>

            <button
              type="button"
              aria-label={t('theme.toggle', { ns: 'common' })}
              className={HEADER_ICON_BUTTON_CLASS}
              onClick={() =>
                setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')
              }
            >
              <MoonIcon className="size-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
              <SunIcon className="absolute size-5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
            </button>

            <a
              href="https://github.com/volcengine/OpenViking"
              target="_blank"
              rel="noreferrer"
              aria-label={t('footer.github', { ns: 'appShell' })}
              className={HEADER_ICON_BUTTON_CLASS}
            >
              <GithubIcon className="size-5" />
            </a>
          </div>
        </header>

        <ScrollArea className="min-h-0 flex-1">
          <div className="flex w-full flex-col gap-6 px-4 py-6 md:px-6">
            {children}
          </div>
        </ScrollArea>
      </SidebarInset>

      <ConnectionDialog />
      <OAuthSetupDialog
        open={oauthSetupOpen}
        onOpenChange={setOauthSetupOpen}
      />
    </SidebarProvider>
  )
}
