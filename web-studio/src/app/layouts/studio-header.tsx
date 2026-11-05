import type { Ref } from 'react'
import { StudioApiKeyMenu } from '#/features/connection/components/studio-api-key-menu'
import { StudioModeSwitch } from '#/features/connection/components/studio-mode-switch'
import { StudioScopeSwitcher } from '#/features/connection/components/studio-scope-switcher'
import { StudioSettingsMenu } from '#/features/connection/components/studio-settings-menu'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { hasStudioAccountScope } from '#/features/connection/lib/studio-mode'
import onboardingBrand from '#/assets/onboarding/brand.svg'
import brandLockupDark from '#/assets/brand-lockup-dark.svg'
import brandLockupLight from '#/assets/brand-lockup-light.svg'
import { OpenVikingMark } from '#/shared/components/openviking-mark'
import { resolveConfiguredServiceProvider } from '#/shared/lib/studio-service'
import { SidebarTrigger } from '#/shared/ui/sidebar'
import { Link, useRouterState } from '@tanstack/react-router'
import { CircleHelpIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export function StudioHeader({
  actionsRef,
  filesystemMode = false,
  managementMode,
  canManage,
  isGatewayHost,
  settingsPath,
}: {
  actionsRef?: Ref<HTMLDivElement>
  managementMode: boolean
  filesystemMode?: boolean
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
  const showManagementEntry =
    !isGatewayHost &&
    (canManage ||
      resolveConfiguredServiceProvider(
        connection.baseUrl,
        connection.serviceSelection,
      ) === 'opensource')
  return (
    <header
      data-admin-header={managementMode}
      className="z-20 flex h-14 shrink-0 items-center justify-between gap-2 bg-transparent px-3 sm:gap-4 sm:px-4"
    >
      <div className="flex min-w-0 items-center gap-2 sm:gap-4">
        <SidebarTrigger
          className={managementMode ? 'shrink-0 md:hidden' : 'hidden'}
        />
        <Link
          to={managementMode ? '/admin' : '/'}
          aria-label={t('studioSettings.brand')}
          className="flex shrink-0 items-center gap-1.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <OpenVikingMark className="size-8 sm:size-9 lg:hidden" />
          <img
            src={managementMode ? onboardingBrand : brandLockupLight}
            alt=""
            aria-hidden="true"
            className={
              managementMode
                ? 'hidden h-8 w-[142px] lg:block dark:lg:hidden'
                : 'hidden h-7 w-auto lg:block dark:lg:hidden'
            }
          />
          <img
            src={brandLockupDark}
            alt=""
            aria-hidden="true"
            className="hidden h-7 w-auto dark:lg:block"
          />
        </Link>
        <StudioScopeSwitcher
          key={`${identityScopeKey}:${managementMode}:${showAccountScope}`}
          settingsPath={settingsPath}
          managementMode={managementMode}
          showAccountScope={showAccountScope && !managementMode}
        />
      </div>
      <div className="flex shrink-0 items-center gap-1.5 sm:gap-3">
        {filesystemMode ? (
          <div
            ref={actionsRef}
            data-slot="studio-header-actions"
            className="flex items-center gap-2"
          />
        ) : null}
        {showManagementEntry ? (
          <StudioModeSwitch
            key={`${identityScopeKey}:${managementMode}`}
            managementMode={managementMode}
            canManage={canManage}
            compact={managementMode}
          />
        ) : null}
        {managementMode ? (
          <a
            href="https://docs.openviking.ai/"
            target="_blank"
            rel="noreferrer"
            aria-label={t('footer.docs')}
            title={t('footer.docs')}
            className="flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent"
          >
            <CircleHelpIcon className="size-4" />
          </a>
        ) : null}
        <StudioApiKeyMenu
          managementMode={managementMode}
          isGatewayHost={isGatewayHost}
        />
        {!managementMode ? (
          <div aria-hidden="true" className="mx-0.5 h-5 w-px bg-border/70" />
        ) : null}
        <StudioSettingsMenu
          key={`${connection.baseUrl}:${connection.accountId}`}
          managementMode={managementMode}
        />
      </div>
    </header>
  )
}
