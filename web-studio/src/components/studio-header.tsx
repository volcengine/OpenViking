import { Link, useRouterState } from '@tanstack/react-router'
import { hasStudioAccountScope } from '#/lib/studio-mode'
import { useTranslation } from 'react-i18next'
import { StudioScopeSwitcher } from './studio-scope-switcher'
import { OpenVikingMark } from './openviking-mark'
import { StudioSettingsMenu } from './studio-settings-menu'
import { StudioModeSwitch } from './studio-mode-switch'
import { StudioApiKeyMenu } from './studio-api-key-menu'
import { SidebarTrigger } from '#/components/ui/sidebar'
import { useAppConnection } from '#/hooks/use-app-connection'

export function StudioHeader({
  managementMode,
  canManage,
  isGatewayHost,
  settingsPath,
}: {
  managementMode: boolean
  canManage: boolean
  isGatewayHost: boolean
  settingsPath: '/settings' | '/admin/settings'
}) {
  const { t } = useTranslation('appShell')
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const showAccountScope = hasStudioAccountScope(pathname)
  const { connection, identityScopeKey } = useAppConnection()
  return (
    <header className="z-20 flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border/70 bg-background px-3 sm:gap-4 sm:px-4">
      <div className="flex min-w-0 items-center gap-2 sm:gap-4">
        <SidebarTrigger className="shrink-0 md:hidden" />
        <Link
          to={managementMode ? '/admin' : '/'}
          aria-label={t('studioSettings.brand')}
          className="flex shrink-0 items-center gap-1.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <OpenVikingMark className="size-8 sm:size-9" />
          <span className="hidden text-lg font-semibold tracking-tight lg:inline">
            {t('studioSettings.brand')}
          </span>
        </Link>
        <div className="hidden h-5 w-px bg-border/70 sm:block" />
        <StudioScopeSwitcher
          key={`${identityScopeKey}:${managementMode}:${showAccountScope}`}
          settingsPath={settingsPath}
          managementMode={managementMode}
          showAccountScope={showAccountScope}
        />
      </div>
      <div className="flex shrink-0 items-center gap-1.5 sm:gap-3">
        {managementMode ? (
          <span className="hidden rounded-md bg-muted/60 px-2 py-1 text-xs text-muted-foreground xl:inline">
            {t('modes.management')}
          </span>
        ) : null}
        {canManage && !isGatewayHost ? (
          <StudioModeSwitch
            key={`${identityScopeKey}:${managementMode}`}
            managementMode={managementMode}
          />
        ) : null}
        <StudioApiKeyMenu
          managementMode={managementMode}
          isGatewayHost={isGatewayHost}
        />
        <div aria-hidden="true" className="mx-0.5 h-5 w-px bg-border/70" />
        <StudioSettingsMenu
          key={`${connection.baseUrl}:${connection.accountId}`}
          canManage={canManage && !isGatewayHost}
          managementMode={managementMode}
        />
      </div>
    </header>
  )
}
