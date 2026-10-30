import { IdentityScopedAppShell } from '#/app/layouts/connection-boundary'
import { StudioSetup } from '#/features/connection/components/studio-setup'
import { AppConnectionProvider } from '#/features/connection/hooks/use-app-connection'
import { fetchServerHealth } from '#/features/connection/lib/health'
import {
  needsStudioSetup,
  studioSetupDestination,
} from '#/features/connection/lib/onboarding'
import studioI18n from '#/i18n'
import { Navigate, useNavigate, useRouterState } from '@tanstack/react-router'
import * as React from 'react'

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
