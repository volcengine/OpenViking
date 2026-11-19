import { StudioFeatureBoundary } from '#/app/layouts/studio-feature-boundary'
import { StudioLayout } from '#/app/layouts/studio-layout'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { createConnectionRoleProbeKey } from '#/features/connection/lib/connection-runtime'
import type { ServerMode } from '#/features/connection/lib/health'
import {
  connectionSettingsPath,
  isConnectionSettingsPath,
} from '#/features/connection/lib/studio-mode'
import studioI18n from '#/i18n'
import { Button } from '#/shared/ui/button'
import { Link, useRouterState } from '@tanstack/react-router'
import * as React from 'react'

export function IdentityScopedAppShell({
  children,
}: {
  children: React.ReactNode
}) {
  const {
    identityScopeKey,
    activeProfileId,
    serverMode,
    studioMode,
    connectionRole,
    connection,
  } = useAppConnection()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const isSettings = isConnectionSettingsPath(pathname)
  const rootManagement =
    studioMode === 'management' && connectionRole === 'root'
  const shellKey = rootManagement
    ? activeProfileId +
      createConnectionRoleProbeKey(
        { ...connection, accountId: '', userId: '', apiKey: '' },
        serverMode,
      )
    : identityScopeKey
  const keepAccountFilters =
    rootManagement &&
    ['/admin/dashboard', '/admin/request-logs', '/admin/monitoring'].includes(
      pathname.replace(/\/$/, ''),
    )
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
    <StudioLayout key={isSettings ? `settings:${activeProfileId}` : shellKey}>
      {isSettings ? (
        children
      ) : (
        <ConnectionScopedRouteContent
          key={keepAccountFilters ? 'management-filters' : identityScopeKey}
          serverMode={serverMode}
        >
          <StudioFeatureBoundary>{children}</StudioFeatureBoundary>
        </ConnectionScopedRouteContent>
      )}
    </StudioLayout>
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
