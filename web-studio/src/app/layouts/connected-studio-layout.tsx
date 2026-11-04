import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { Navigate, useRouterState } from '@tanstack/react-router'
import { StudioEntry, isManagementPath } from '#/platform/entry/paths'
import { managementUnavailableDestination } from '#/platform/deployment/experience'
import { shouldDiscoverGatewayHost } from '#/features/connection/lib/connection-policy'
import { needsStudioSetup } from '#/features/connection/lib/profile-bootstrap'
import { fetchServerHealth } from '#/features/connection/lib/health'
import { AppConnectionProvider } from '#/features/connection/hooks/use-app-connection'
import { IdentityScopedAppShell } from './connection-boundary'
import i18n from '#/i18n'

export function ConnectedStudioLayout({ children }: { children: ReactNode }) {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const [needsSetup, setNeedsSetup] = useState(needsStudioSetup)
  const [checkingHost, setCheckingHost] = useState(
    () => needsSetup && shouldDiscoverGatewayHost(window.location.pathname),
  )
  useEffect(() => {
    if (!checkingHost) return
    const controller = new AbortController()
    void fetchServerHealth(window.location.origin, {}, controller.signal)
      .then((health) => {
        if (
          !controller.signal.aborted &&
          health.service === 'openviking-gateway'
        )
          setNeedsSetup(false)
      })
      .catch(() => {})
      .finally(() => {
        if (!controller.signal.aborted) setCheckingHost(false)
      })
    return () => controller.abort()
  }, [checkingHost])
  if (checkingHost)
    return (
      <div
        role="status"
        className="flex min-h-svh items-center justify-center bg-background text-sm text-muted-foreground"
      >
        {i18n.t('studio:loading')}
      </div>
    )
  if (needsSetup) return <Navigate replace to={StudioEntry.Login} />
  const managementRedirect = managementUnavailableDestination()
  if (managementRedirect && isManagementPath(pathname))
    return <Navigate replace to={managementRedirect} />
  return (
    <AppConnectionProvider>
      <IdentityScopedAppShell>{children}</IdentityScopedAppShell>
    </AppConnectionProvider>
  )
}
