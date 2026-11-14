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
import { CircleHelpIcon, GalleryVerticalEndIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export function StudioHeader({
  actionsRef,
  workbenchChrome,
  tasksMode,
  managementMode,
  canManage,
  isGatewayHost,
  settingsPath,
}: {
  actionsRef?: Ref<HTMLDivElement>
  managementMode: boolean
  workbenchChrome: boolean
  tasksMode: boolean
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
      className="relative z-20 grid shrink-0 grid-cols-[auto_auto_1fr] items-center gap-x-2 gap-y-1.5 bg-transparent px-3 py-2 md:flex md:h-14 md:justify-between md:gap-4 md:px-4 md:py-0"
    >
      <div className="contents md:flex md:min-w-0 md:items-center md:gap-4">
        <SidebarTrigger
          className={
            managementMode
              ? 'col-start-1 row-start-1 size-10 md:hidden'
              : 'hidden'
          }
        />
        <Link
          to={managementMode ? '/admin' : '/'}
          aria-label={t('studioSettings.brand')}
          className={`row-start-1 flex shrink-0 items-center gap-1.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring ${managementMode ? 'col-start-2' : 'col-start-1'}`}
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
        <div className="studio-header-scope col-span-full row-start-2 flex min-w-0">
          <StudioScopeSwitcher
            key={`${identityScopeKey}:${managementMode}:${showAccountScope}`}
            settingsPath={settingsPath}
            managementMode={managementMode}
            showAccountScope={showAccountScope && !managementMode}
          />
        </div>
      </div>
      <div
        className={`col-start-3 row-start-1 flex shrink-0 items-center justify-self-end gap-1.5 ${managementMode ? 'sm:gap-3' : 'sm:gap-2'}`}
      >
        {workbenchChrome ? (
          <div className="flex h-8 min-w-8 shrink-0 items-center justify-center">
            {tasksMode ? (
              <span
                aria-current="page"
                className="flex size-8 items-center justify-center rounded-full bg-accent text-foreground/80"
              >
                <GalleryVerticalEndIcon
                  aria-hidden="true"
                  className="size-[18px]"
                />
                <span className="sr-only">{t('tasksPage:title')}</span>
              </span>
            ) : (
              <div
                ref={actionsRef}
                data-slot="studio-header-actions"
                className="flex h-8 items-center gap-1"
              />
            )}
          </div>
        ) : null}
        {workbenchChrome && !managementMode ? (
          <div
            aria-hidden="true"
            className="mx-1 h-3.5 w-px bg-foreground/20"
          />
        ) : null}
        <a
          href="https://docs.openviking.ai/"
          target="_blank"
          rel="noreferrer"
          aria-label={t('footer.docs')}
          title={t('footer.docs')}
          className={
            managementMode
              ? 'flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent'
              : 'flex size-8 items-center justify-center rounded-full text-foreground/80 outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring sm:mx-1'
          }
        >
          <CircleHelpIcon
            className={managementMode ? 'size-4' : 'size-[18px]'}
          />
        </a>
        <StudioApiKeyMenu
          managementMode={managementMode}
          isGatewayHost={isGatewayHost}
        />
        {!managementMode && showManagementEntry ? (
          <StudioModeSwitch
            key={`${identityScopeKey}:${managementMode}`}
            managementMode={managementMode}
            canManage={canManage}
          />
        ) : null}
        <StudioSettingsMenu
          key={`${connection.baseUrl}:${connection.accountId}`}
          managementMode={managementMode}
        />
      </div>
    </header>
  )
}
