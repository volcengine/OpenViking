import type { ReactNode } from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { useAppConnection } from '#/hooks/use-app-connection'
import { useStudioService } from '#/hooks/use-studio-service'
import { featureForPath } from '#/lib/studio-service'
import { useStudioCapabilities } from '#/hooks/use-studio-capabilities'
import {
  canManageStudioGateway,
  resolveStudioManagementCapabilities,
} from '#/lib/studio-permissions'

export function StudioFeatureBoundary({ children }: { children: ReactNode }) {
  const { t } = useTranslation('studio')
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const {
    connection,
    connectionRole,
    isConnectionRoleLoading,
    isGatewayHost,
    serverMode,
  } = useAppConnection()
  const service = useStudioService()
  const feature = featureForPath(pathname)
  const probe = feature && 'probe' in feature ? feature.probe : undefined
  const cloudProbe =
    service.ready && service.provider === 'volcengine' && Boolean(probe)
  const capabilities = useStudioCapabilities()
  const capability = feature ? capabilities[feature.id] : undefined
  if (
    pathname === '/settings' ||
    pathname === '/' ||
    (isGatewayHost && feature?.id === 'gateway')
  )
    return children
  let reason: string | undefined
  if (!service.ready)
    reason = service.isChecking ? 'checking' : 'connectionFailed'
  else if (service.provider === 'unknown') reason = 'unknownProvider'
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
      feature.id === 'gateway'
        ? !canManageStudioGateway({
            serviceProvider: service.provider,
            hasControlCredential: Boolean(connection.adminApiKey.trim()),
            isRoleLoading: isConnectionRoleLoading,
            role: connectionRole,
            serverMode,
          })
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
        <Button nativeButton={false} render={<Link to="/settings" />}>
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
