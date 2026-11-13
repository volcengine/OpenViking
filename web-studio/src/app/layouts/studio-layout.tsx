import type { NavItem, NavSubItem } from '#/app/navigation/workbench-navigation'
import { StudioHeader } from '#/app/layouts/studio-header'
import { StudioManagementNavigation } from '#/app/navigation/studio-management-navigation'
import { shouldShowGatewayNavigation } from '#/app/navigation/visibility'
import {
  NAV_ITEMS,
  NAV_SECTIONS,
  NavGroupItem,
} from '#/app/navigation/workbench-navigation'
import { GeneratedCredentialDialog } from '#/features/connection/components/generated-credential-dialog'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioCapabilities } from '#/features/connection/hooks/use-studio-capabilities'
import { useStudioMode } from '#/features/connection/hooks/use-studio-mode'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import {
  connectionSettingsPath,
  isManagementPath,
  isSharedStudioPath,
} from '#/features/connection/lib/studio-mode'
import { canManageStudioGateway } from '#/features/connection/lib/studio-permissions'
import { CrossDeviceVerifyDialog } from '#/features/oauth/components/cross-device-verify-dialog'
import {
  featureForPath,
  isStudioFeatureVisible,
} from '#/shared/lib/studio-service'
import { ScrollArea } from '#/shared/ui/scroll-area'
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
  SidebarProvider,
  SidebarTrigger,
} from '#/shared/ui/sidebar'
import { Link, useNavigate, useRouterState } from '@tanstack/react-router'
import { BookOpenIcon, BotIcon, BracesIcon, KeyRoundIcon } from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

export function resolveLanguage(value: string | undefined): 'zh-CN' | 'en' {
  if (value?.toLowerCase().startsWith('zh')) {
    return 'zh-CN'
  }

  return 'en'
}

export function StudioLayout({ children }: { children: React.ReactNode }) {
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
                developmentMode={serverMode === 'dev'}
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

            <SidebarGroup
              className="pb-1"
              hidden={provider !== 'opensource' || isGatewayHost}
            >
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
