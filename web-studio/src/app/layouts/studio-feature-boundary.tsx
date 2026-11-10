import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioCapabilities } from '#/features/connection/hooks/use-studio-capabilities'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import {
  connectionSettingsPath,
  isConnectionSettingsPath,
  isManagementPath,
  isSharedStudioPath,
} from '#/features/connection/lib/studio-mode'
import {
  canManageStudioGateway,
  resolveStudioManagementCapabilities,
} from '#/features/connection/lib/studio-permissions'
import { featureForPath } from '#/shared/lib/studio-service'
import { Button } from '#/shared/ui/button'
import { Link, useRouterState } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

export function StudioFeatureBoundary({ children }: { children: ReactNode }) {
  const { t } = useTranslation('studio')
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const {
    connection,
    connectionRole,
    isConnectionRoleLoading,
    isGatewayHost,
    serverMode,
    studioMode,
  } = useAppConnection()
  const service = useStudioService()
  const feature = featureForPath(pathname)
  const probe = feature && 'probe' in feature ? feature.probe : undefined
  const cloudProbe =
    service.ready && service.provider === 'volcengine' && Boolean(probe)
  const capabilities = useStudioCapabilities()
  const capability = feature ? capabilities[feature.id] : undefined
  if (
    isConnectionSettingsPath(pathname) ||
    pathname === '/' ||
    (isGatewayHost && feature?.id === 'gateway')
  )
    return children
  let reason: string | undefined
  if (serverMode === 'trusted' && !connection.adminApiKey.trim())
    reason = 'trustedCredentialRequired'
  else if (!service.ready)
    reason = service.isChecking ? 'checking' : 'connectionFailed'
  else if (service.provider === 'unknown') reason = 'unknownProvider'
  else if (feature?.domain === 'management' && isConnectionRoleLoading)
    reason = 'checking'
  else if (feature?.domain === 'management') {
    const permissions = resolveStudioManagementCapabilities({
      serviceProvider: service.provider,
      hasControlCredential: Boolean(connection.adminApiKey.trim()),
      isRoleLoading: isConnectionRoleLoading,
      role: connectionRole,
      serverMode,
    })
    if (
      feature.id === 'gateway' &&
      service.provider === 'opensource' &&
      serverMode !== 'api_key' &&
      serverMode !== 'dev' &&
      serverMode !== 'trusted'
    )
      reason = 'gatewayAuthMode'
    else if (
      serverMode === 'dev' &&
      (feature.id === 'configuration' || feature.id === 'botManagement')
    ) {
      if (isConnectionRoleLoading || connectionRole !== 'root')
        reason = 'managementRequired'
    } else if (
      feature.id === 'gateway'
        ? !canManageStudioGateway({
            serviceProvider: service.provider,
            hasControlCredential: Boolean(connection.adminApiKey.trim()),
            isRoleLoading: isConnectionRoleLoading,
            role: connectionRole,
            serverMode,
          })
        : feature.id === 'botManagement'
          ? !permissions.canManageAccounts
          : !permissions.canManageUsers
    )
      reason = 'managementRequired'
  } else if (pathname.startsWith('/oauth') && service.provider !== 'opensource')
    reason = 'unavailable'
  else if (
    feature?.domain === 'extensions' &&
    service.provider !== 'opensource'
  ) {
    if (!probe) reason = 'unavailable'
    else if (capability?.isPending) reason = 'checking'
    else if (capability?.data !== 'supported')
      reason = capability?.data ?? 'unknown'
  }
  if (
    !reason &&
    !isManagementPath(pathname) &&
    !isSharedStudioPath(pathname) &&
    serverMode === 'api_key' &&
    !connection.apiKey &&
    connection.adminApiKey
  )
    reason = 'managementRequired'
  if (!reason) return children
  return (
    <section
      className="mx-auto grid w-full max-w-xl gap-4 rounded-xl border bg-card p-6"
      aria-live="polite"
    >
      <h1 className="text-lg font-medium">{t(`states.${reason}.title`)}</h1>
      <p className="text-sm text-muted-foreground">
        {t(`states.${reason}.description`)}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          nativeButton={false}
          render={<Link to={connectionSettingsPath(pathname, studioMode)} />}
        >
          {t('connectionSettings')}
        </Button>
        <Button
          nativeButton={false}
          variant="outline"
          render={<Link to="/filesystem" />}
        >
          {t('directory')}
        </Button>
        {cloudProbe && reason !== 'checking' ? (
          <Button variant="outline" onClick={() => void capability?.refetch()}>
            {t('retry')}
          </Button>
        ) : null}
      </div>
    </section>
  )
}
