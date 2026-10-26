import { getOvResult, ovClient } from '#/lib/ov-client'
import { botAccountBase } from '../../-api'

export type OnboardingRun = {
  id: string
  type: string
  state:
    | 'initializing'
    | 'waiting_for_scan'
    | 'scanned'
    | 'creating'
    | 'configuring'
    | 'publishing'
    | 'awaiting_approval'
    | 'ready'
    | 'failed'
    | 'expired'
    | 'interrupted'
    | 'cancelled'
  name: string
  user_id: string
  qr?: string
  expires_at?: number
  error?: string
  can_retry: boolean
  app_id?: string
  connection_id?: string
  owner?: { user_name: string; tenant_name: string }
}
const base = () => `${botAccountBase()}/onboarding-runs`
export function currentOnboarding() {
  return getOvResult<OnboardingRun | null>(
    ovClient.client.get({
      url: `${base()}/current`,
      query: { type: 'feishu' },
    }),
  )
}
export function getOnboarding(id: string) {
  return getOvResult<OnboardingRun>(
    ovClient.client.get({ url: `${base()}/${encodeURIComponent(id)}` }),
  )
}
export function startOnboarding(body: {
  user_id: string
  name: string
  request_id: string
  settings: { thread_require_mention: boolean }
}) {
  return getOvResult<OnboardingRun>(
    ovClient.client.post({ url: base(), body: { ...body, type: 'feishu' } }),
  )
}
export function updateOnboarding(
  id: string,
  action: 'retry' | 'cancel' | 'manual',
) {
  return getOvResult<OnboardingRun>(
    ovClient.client.post({
      url: `${base()}/${encodeURIComponent(id)}/actions`,
      body: { action },
    }),
  )
}
