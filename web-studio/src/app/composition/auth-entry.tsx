import type { ConnectionLoginResult } from '#/features/connection/login'
import type { AuthPage } from '#/features/auth/lib/auth-routes'
import { useNavigate } from '@tanstack/react-router'
import { PublicAuthPage } from '#/features/auth/components/public-auth-page'
import { getStudioExperience } from '#/platform/deployment/experience'
import { ENV_BASE_URL } from '#/features/connection/lib/connection-runtime'

export function AuthEntry({
  page,
  onComplete,
}: {
  page?: AuthPage
  onComplete?: (result: ConnectionLoginResult) => void
}) {
  const navigate = useNavigate()
  const { auth } = getStudioExperience()
  const initialAddress =
    ENV_BASE_URL ||
    (window.location.pathname.startsWith('/studio')
      ? window.location.origin
      : '')
  return (
    <PublicAuthPage
      page={page ?? auth.defaultMethod}
      apiKeyConfig={auth.apiKey}
      initialAddress={initialAddress}
      onComplete={
        onComplete ??
        ((result) => {
          void navigate({ to: result.destination, replace: true })
        })
      }
    />
  )
}
