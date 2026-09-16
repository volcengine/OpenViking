import { FeishuConnect } from './feishu/connect'
import { ConnectionCredentials } from './feishu/connection-credentials'

// Each supported platform owns its setup and credential UI.
export const providers = {
  feishu: {
    label: 'feishu' as const,
    addLabel: 'addFeishu' as const,
    Setup: FeishuConnect,
    Credentials: ConnectionCredentials,
  },
}
export const upcomingProviders = ['Slack', 'DingTalk', 'Discord', 'Telegram']
export function getProvider(type?: string) {
  const key = type ?? 'feishu'
  return Object.hasOwn(providers, key)
    ? providers[key as keyof typeof providers]
    : undefined
}
