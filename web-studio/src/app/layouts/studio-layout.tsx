import { StudioHeaderActionsContext } from './studio-header-actions'
import { StudioModeSwitch } from '#/features/connection/components/studio-mode-switch'
import { StudioHeader } from '#/app/layouts/studio-header'
import { StudioManagementNavigation } from '#/app/navigation/studio-management-navigation'
import { shouldShowGatewayNavigation } from '#/app/navigation/visibility'
import { GeneratedCredentialDialog } from '#/features/connection/components/generated-credential-dialog'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioMode } from '#/features/connection/hooks/use-studio-mode'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import {
  connectionSettingsPath,
  isManagementPath,
  isSharedStudioPath,
} from '#/features/connection/lib/studio-mode'
import { canManageStudioGateway } from '#/features/connection/lib/studio-permissions'
import { ScrollArea } from '#/shared/ui/scroll-area'
import {
  Sidebar,
  SidebarContent,
  SidebarInset,
  SidebarProvider,
  useSidebar,
} from '#/shared/ui/sidebar'
import { useRouterState } from '@tanstack/react-router'
import * as React from 'react'

export function resolveLanguage(value: string | undefined): 'zh-CN' | 'en' {
  if (value?.toLowerCase().startsWith('zh')) {
    return 'zh-CN'
  }

  return 'en'
}

export function StudioLayout({ children }: { children: React.ReactNode }) {
  const [headerActions, setHeaderActions] =
    React.useState<HTMLDivElement | null>(null)
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
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
  const surface = pathname.replace(/\/$/, '')
  const filesystemMode = surface === '/filesystem'
  const tasksMode = surface === '/tasks'
  const workbenchChrome = filesystemMode || tasksMode
  const fillContent =
    filesystemMode || surface === '/sessions' || surface === '/vikingbot'

  return (
    <StudioHeaderActionsContext.Provider value={headerActions}>
      <SidebarProvider
        defaultOpen
        desktopToggleEnabled={false}
        data-management={managementMode}
        data-workbench={workbenchChrome}
        style={
          managementMode
            ? ({ '--sidebar-width': '200px' } as React.CSSProperties)
            : undefined
        }
        open={managementMode}
        className="studio-shell flex h-dvh min-h-0 flex-col overflow-hidden bg-[#f3f3fa] dark:bg-sidebar"
      >
        <CloseMobileNavigation pathname={pathname} />
        <StudioHeader
          actionsRef={setHeaderActions}
          managementMode={managementMode}
          workbenchChrome={workbenchChrome}
          tasksMode={tasksMode}
          canManage={canManage}
          isGatewayHost={isGatewayHost}
          settingsPath={settingsPath}
        />
        <div className="studio-layout-body flex min-h-0 flex-1">
          {managementMode ? (
            <Sidebar variant="sidebar">
              <SidebarContent className="gap-0 py-1">
                {!isGatewayHost ? (
                  <div className="px-3 pb-4">
                    <StudioModeSwitch managementMode navigation />
                  </div>
                ) : null}
                <StudioManagementNavigation
                  pathname={pathname}
                  isRoot={connectionRole === 'root' && !isGatewayHost}
                  showRootConfiguration={
                    connectionRole === 'root' &&
                    !isConnectionRoleLoading &&
                    Boolean(connection.adminApiKey.trim())
                  }
                  showGateway={showGateway}
                  gatewayOnly={isGatewayHost}
                  developmentMode={serverMode === 'dev'}
                />
              </SidebarContent>
            </Sidebar>
          ) : null}

          <SidebarInset
            className="min-h-0 min-w-0 flex-1 overflow-hidden rounded-none border-0 bg-background shadow-none ring-0 data-[filesystem=true]:bg-transparent md:m-0 md:ml-0"
            data-filesystem={filesystemMode}
          >
            <ScrollArea className="min-h-0 flex-1">
              <div
                className={
                  fillContent
                    ? 'flex h-full w-full min-w-0 flex-col'
                    : managementMode
                      ? 'studio-management-content flex w-full min-w-0 flex-col gap-10'
                      : 'flex w-full min-w-0 flex-col gap-6 px-4 py-4 sm:py-6 md:px-8 lg:px-12'
                }
              >
                {children}
              </div>
            </ScrollArea>
          </SidebarInset>
        </div>

        <GeneratedCredentialDialog />
      </SidebarProvider>
    </StudioHeaderActionsContext.Provider>
  )
}

function CloseMobileNavigation({ pathname }: { pathname: string }) {
  const { isMobile, setOpenMobile } = useSidebar()
  React.useEffect(() => {
    setOpenMobile(false)
  }, [isMobile, pathname, setOpenMobile])
  return null
}
