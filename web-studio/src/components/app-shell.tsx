import { StudioHeader } from './studio-header'
import { StudioManagementNavigation } from './studio-management-navigation'
import { useStudioMode } from '#/hooks/use-studio-mode'
import {
  isManagementPath,
  isSharedStudioPath,
  isConnectionSettingsPath,
  connectionSettingsPath,
} from '#/lib/studio-mode'
import { Button } from '#/components/ui/button'
import studioI18n from '#/i18n'
import { StudioSetup } from './studio-setup'
import {
  needsStudioSetup,
  studioSetupDestination,
} from '#/lib/connection/onboarding'
import * as React from 'react'
import {
  Link,
  Navigate,
  useNavigate,
  useRouterState,
} from '@tanstack/react-router'
import {
  MessagesSquareIcon,
  BookOpenIcon,
  CableIcon,
  BracesIcon,
  BrainIcon,
  ChevronRightIcon,
  ListChecksIcon,
  HistoryIcon,
  HomeIcon,
  KeyRoundIcon,
  ActivityIcon,
  BotIcon,
  PanelsTopLeftIcon,
  ScrollTextIcon,
  SearchIcon,
  SparklesIcon,
  WorkflowIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '#/components/ui/collapsible'
import { CrossDeviceVerifyDialog } from '#/components/cross-device-verify-dialog'
import { GeneratedCredentialDialog } from '#/components/generated-credential-dialog'
import { ScrollArea } from '#/components/ui/scroll-area'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  SidebarTrigger,
} from '#/components/ui/sidebar'
import {
  AppConnectionProvider,
  useAppConnection,
} from '#/hooks/use-app-connection'
import type { ServerMode } from '#/lib/connection/health'
import { fetchServerHealth } from '#/lib/connection/health'
import { useStudioService } from '#/hooks/use-studio-service'
import { useStudioCapabilities } from '#/hooks/use-studio-capabilities'
import { featureForPath, isStudioFeatureVisible } from '#/lib/studio-service'
import { StudioFeatureBoundary } from './studio-feature-boundary'
import { canManageStudioGateway } from '#/lib/studio-permissions'

type NavItem = {
  icon: React.ComponentType
  id: string
  section: 'workspace' | 'operations'
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
    section: 'workspace',
    titleKey: 'navigation.home.title',
    to: '/home',
  },
  {
    icon: PanelsTopLeftIcon,
    id: 'filesystem',
    section: 'workspace',
    titleKey: 'navigation.filesystem.title',
    to: '/filesystem',
  },
  {
    icon: BotIcon,
    id: 'vikingbot',
    section: 'workspace',
    titleKey: 'vikingbot:title',
    to: '/vikingbot',
  },
  {
    icon: SearchIcon,
    id: 'retrieval',
    section: 'workspace',
    titleKey: 'navigation.retrieval.title',
    to: '/retrieval',
  },
  {
    icon: SparklesIcon,
    id: 'skills',
    section: 'workspace',
    titleKey: 'navigation.skills.title',
    to: '/skills',
  },

  {
    icon: WorkflowIcon,
    id: 'compile',
    section: 'workspace',
    titleKey: 'navigation.compile.title',
    to: '/compile',
  },
  {
    icon: MessagesSquareIcon,
    id: 'sessions',
    section: 'operations',
    titleKey: 'navigation.sessions.title',
    to: '/sessions',
  },
  {
    icon: BrainIcon,
    id: 'agentExperience',
    section: 'operations',
    titleKey: 'navigation.agentExperience.title',
    to: '/agent-experience',
  },
  {
    icon: ScrollTextIcon,
    id: 'requestLogs',
    section: 'operations',
    titleKey: 'navigation.requestLogs.title',
    to: '/request-logs',
  },
  {
    icon: ListChecksIcon,
    id: 'tasks',
    section: 'operations',
    titleKey: 'navigation.tasks.title',
    to: '/tasks',
  },
  {
    icon: HistoryIcon,
    id: 'watches',
    section: 'operations',
    titleKey: 'navigation.watches.title',
    to: '/watches',
  },
  {
    icon: ActivityIcon,
    id: 'monitoring',
    section: 'operations',
    titleKey: 'navigation.monitoring.title',
    to: '/monitoring',
  },
] as const

const NAV_SECTIONS = [
  {
    id: 'workspace',
    titleKey: 'sidebar.groups.workspace',
  },
  {
    id: 'operations',
    titleKey: 'sidebar.groups.operations',
  },
] as const

function resolveLanguage(value: string | undefined): 'zh-CN' | 'en' {
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

export function AppShell({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const [needsSetup, setNeedsSetup] = React.useState(needsStudioSetup)
  const [checkingHost, setCheckingHost] = React.useState(
    () => needsSetup && window.location.pathname.startsWith('/studio'),
  )
  React.useEffect(() => {
    if (!checkingHost) return
    let cancelled = false
    void fetchServerHealth(window.location.origin)
      .then((health) => {
        if (!cancelled && health.service === 'openviking-gateway')
          setNeedsSetup(false)
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setCheckingHost(false)
      })
    return () => {
      cancelled = true
    }
  }, [checkingHost])
  if (checkingHost)
    return (
      <div
        role="status"
        className="flex min-h-svh items-center justify-center bg-background text-sm text-muted-foreground"
      >
        {studioI18n.t('studio:loading')}
      </div>
    )
  if (pathname === '/config')
    return <Navigate replace to={needsSetup ? '/onboarding' : '/settings'} />
  if (pathname === '/onboarding' && !needsSetup)
    return <Navigate replace to="/settings" />
  if (pathname === '/onboarding')
    return (
      <StudioSetup
        canReturnToWorkspace={!needsSetup}
        onCancel={() => {
          if (needsSetup) {
            window.location.assign('https://docs.openviking.ai/')
          } else {
            void navigate({ to: '/', replace: true })
          }
        }}
        onComplete={(connection) => {
          setNeedsSetup(false)
          void navigate({
            to: studioSetupDestination(connection),
          })
        }}
      />
    )
  if (needsSetup) return <Navigate replace to="/onboarding" />
  return (
    <AppConnectionProvider>
      <IdentityScopedAppShell>{children}</IdentityScopedAppShell>
    </AppConnectionProvider>
  )
}

export function IdentityScopedAppShell({
  children,
}: {
  children: React.ReactNode
}) {
  const { identityScopeKey, activeProfileId, serverMode, studioMode } =
    useAppConnection()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const isSettings = isConnectionSettingsPath(pathname)
  // Keep the settings tree stable across health transitions, but reset it when
  // an actual profile changes. Business routes retain identity isolation.
  if (serverMode === 'checking' && !isSettings)
    return (
      <div
        role="status"
        className="flex min-h-svh flex-col items-center justify-center gap-4 bg-background text-sm text-muted-foreground"
      >
        {studioI18n.t('studio:loading')}
        <Button
          nativeButton={false}
          variant="outline"
          render={<Link to={connectionSettingsPath(pathname, studioMode)} />}
        >
          {studioI18n.t('studio:connectionSettings')}
        </Button>
      </div>
    )
  return (
    <AppShellInner
      key={isSettings ? `settings:${activeProfileId}` : identityScopeKey}
    >
      {isSettings ? (
        children
      ) : (
        <ConnectionScopedRouteContent serverMode={serverMode}>
          <StudioFeatureBoundary>{children}</StudioFeatureBoundary>
        </ConnectionScopedRouteContent>
      )}
    </AppShellInner>
  )
}

export function ConnectionScopedRouteContent({
  children,
  serverMode,
}: {
  children: React.ReactNode
  serverMode: ServerMode
}) {
  return serverMode === 'checking' ? null : children
}

/**
 * Every signed-in user of an OpenViking server sees the gateway entry, even
 * while the gateway is off: the page explains how to turn it on.
 */
export function shouldShowGatewayNavigation({
  isGatewayHost,
  serverMode,
  allowed,
}: {
  isGatewayHost: boolean
  serverMode: ServerMode
  /** Gateway administration is available for the connected service. */
  allowed: boolean
}): boolean {
  return (
    isGatewayHost ||
    (serverMode !== 'checking' && serverMode !== 'offline' && allowed)
  )
}

function AppShellInner({ children }: { children: React.ReactNode }) {
  const { i18n, t } = useTranslation(['appShell', 'common'])
  const navigate = useNavigate()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const currentLanguage = resolveLanguage(
    i18n.resolvedLanguage ?? i18n.language,
  )
  const agentIntegrationsHref = `https://docs.openviking.ai/${
    currentLanguage === 'zh-CN' ? 'zh' : 'en'
  }/agent-integrations/01-overview`
  const opensourceApiHref = `https://docs.openviking.ai/${
    currentLanguage === 'zh-CN' ? 'zh' : 'en'
  }/api/01-overview`
  const [crossDeviceVerifyOpen, setCrossDeviceVerifyOpen] =
    React.useState(false)
  const {
    connection,
    connectionRole,
    isConnectionRoleLoading,
    isGatewayHost,
    serverMode,
    studioMode,
  } = useAppConnection()
  const service = useStudioService()
  const { provider } = service
  const sdkApiHref =
    provider === 'volcengine'
      ? 'https://docs.volcengine.com/docs/84313/2381937'
      : opensourceApiHref
  const { canManage } = useStudioMode()
  const managementMode =
    isManagementPath(pathname) ||
    (isSharedStudioPath(pathname) && canManage && studioMode === 'management')
  const settingsPath = connectionSettingsPath(
    pathname,
    managementMode ? 'management' : 'workbench',
  )
  const permissionInput = {
    serviceProvider: provider,
    hasControlCredential: Boolean(connection.adminApiKey.trim()),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  }
  const canManageGateway =
    service.ready && canManageStudioGateway(permissionInput)
  const showGateway = shouldShowGatewayNavigation({
    isGatewayHost,
    serverMode,
    allowed: canManageGateway,
  })
  const navSections = isGatewayHost ? [] : NAV_SECTIONS
  const crossDeviceVerifyActive =
    pathname === '/oauth/verify' ||
    pathname.startsWith('/oauth/verify/') ||
    pathname === '/admin/oauth/verify'
  const capabilities = useStudioCapabilities()
  const visibleNavItems = NAV_ITEMS.filter((item) => {
    const feature = featureForPath(item.to)
    return (
      !feature ||
      (service.ready &&
        isStudioFeatureVisible(
          provider,
          feature,
          capabilities[feature.id]?.data,
        ))
    )
  })

  function openCrossDeviceVerify(): void {
    if (crossDeviceVerifyActive) {
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
      setCrossDeviceVerifyOpen(true)
    } else {
      void navigate({
        to: managementMode ? '/admin/oauth/verify' : '/oauth/verify',
      })
    }
  }

  return (
    <SidebarProvider
      defaultOpen
      className="flex h-svh min-h-0 flex-col overflow-hidden bg-sidebar"
    >
      <StudioHeader
        managementMode={managementMode}
        canManage={canManage}
        isGatewayHost={isGatewayHost}
        settingsPath={settingsPath}
      />
      <div className="flex min-h-0 flex-1">
        <Sidebar
          variant="sidebar"
          collapsible="icon"
          className="top-14 h-[calc(100svh-3.5rem)]"
        >
          <SidebarContent className="gap-0 py-1">
            {managementMode && (canManage || isGatewayHost) ? (
              <StudioManagementNavigation
                pathname={pathname}
                isRoot={connectionRole === 'root' && !isGatewayHost}
                showGateway={showGateway}
                gatewayOnly={isGatewayHost}
              />
            ) : null}
            {!managementMode &&
              navSections
                .filter((section) =>
                  visibleNavItems.some((item) => item.section === section.id),
                )
                .map((section) => (
                  <SidebarGroup key={section.id} className="pb-1">
                    <SidebarGroupLabel className="h-7 px-2 pt-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-sidebar-foreground/45">
                      {t(section.titleKey, { ns: 'appShell' })}
                    </SidebarGroupLabel>
                    <SidebarGroupContent>
                      <SidebarMenu>
                        {visibleNavItems
                          .filter((item) => item.section === section.id)
                          .map((item) => {
                            const isActive =
                              pathname === item.to ||
                              pathname.startsWith(`${item.to}/`)
                            const title = t(item.titleKey, { ns: 'appShell' })

                            if (item.children) {
                              return (
                                <NavGroupItem
                                  key={item.id}
                                  item={
                                    item as NavItem & {
                                      children: readonly NavSubItem[]
                                    }
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
                                  className="h-9"
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
                ))}

            <SidebarGroup className="pb-1">
              <SidebarGroupLabel className="h-7 px-2 pt-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-sidebar-foreground/45">
                {t('sidebar.groups.settings', { ns: 'appShell' })}
              </SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  <SidebarMenuItem>
                    <SidebarMenuButton
                      render={<Link to={settingsPath} />}
                      isActive={isConnectionSettingsPath(pathname)}
                      tooltip={t('footer.connection', { ns: 'appShell' })}
                      className="h-9"
                    >
                      <CableIcon />
                      <span>{t('footer.connection', { ns: 'appShell' })}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>

            <SidebarGroup
              className="pb-1"
              hidden={provider !== 'opensource' || isGatewayHost}
            >
              <SidebarGroupLabel className="h-7 px-2 pt-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-sidebar-foreground/45">
                {t('sidebar.groups.connections', { ns: 'appShell' })}
              </SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {provider === 'opensource' && !isGatewayHost ? (
                    <SidebarMenuItem>
                      <SidebarMenuButton
                        onClick={openCrossDeviceVerify}
                        isActive={crossDeviceVerifyActive}
                        tooltip={t('navigation.crossDeviceVerify.title', {
                          ns: 'appShell',
                        })}
                        className="h-9"
                      >
                        <KeyRoundIcon />
                        <span>
                          {t('navigation.crossDeviceVerify.title', {
                            ns: 'appShell',
                          })}
                        </span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ) : null}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>

            <SidebarGroup className="pb-1" hidden={isGatewayHost}>
              <SidebarGroupLabel className="h-7 px-2 pt-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-sidebar-foreground/45">
                {t('sidebar.groups.resources', { ns: 'appShell' })}
              </SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  <SidebarMenuItem>
                    <SidebarMenuButton
                      render={
                        <a
                          href={
                            provider === 'volcengine'
                              ? sdkApiHref
                              : 'https://docs.openviking.ai/'
                          }
                          target="_blank"
                          rel="noreferrer"
                        />
                      }
                      tooltip={t('footer.docs', { ns: 'appShell' })}
                      className="h-9"
                    >
                      <BookOpenIcon />
                      <span>{t('footer.docs', { ns: 'appShell' })}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                  <SidebarMenuItem>
                    <SidebarMenuButton
                      render={
                        <a href={sdkApiHref} target="_blank" rel="noreferrer" />
                      }
                      tooltip={t('footer.sdkApi', { ns: 'appShell' })}
                      className="h-9"
                    >
                      <BracesIcon />
                      <span>{t('footer.sdkApi', { ns: 'appShell' })}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                  {provider === 'opensource' && !isGatewayHost ? (
                    <SidebarMenuItem>
                      <SidebarMenuButton
                        render={
                          <a
                            href={agentIntegrationsHref}
                            target="_blank"
                            rel="noreferrer"
                          />
                        }
                        tooltip={t('footer.agentIntegrations', {
                          ns: 'appShell',
                        })}
                        className="h-9"
                      >
                        <BotIcon />
                        <span>
                          {t('footer.agentIntegrations', { ns: 'appShell' })}
                        </span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ) : null}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          </SidebarContent>
          <SidebarFooter className="hidden items-end border-t border-sidebar-border/70 px-2 py-1 group-data-[collapsible=icon]:items-center md:flex">
            <SidebarTrigger className="hidden shrink-0 md:inline-flex" />
          </SidebarFooter>
        </Sidebar>

        <SidebarInset className="min-h-0 flex-1 overflow-hidden rounded-none border-0 bg-background shadow-none ring-0 md:m-0 md:ml-0">
          <ScrollArea className="min-h-0 flex-1">
            <div className="flex w-full flex-col gap-6 px-4 py-6 md:px-6">
              {children}
            </div>
          </ScrollArea>
        </SidebarInset>
      </div>

      {provider === 'opensource' && !isGatewayHost ? (
        <CrossDeviceVerifyDialog
          open={crossDeviceVerifyOpen}
          onOpenChange={setCrossDeviceVerifyOpen}
        />
      ) : null}
      <GeneratedCredentialDialog />
    </SidebarProvider>
  )
}
