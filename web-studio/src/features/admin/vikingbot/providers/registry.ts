import type { Connection } from '#/features/admin/vikingbot/api'
import { FeishuConnect } from '#/features/admin/vikingbot/providers/feishu/connect'
import { ConnectionCredentials } from '#/features/admin/vikingbot/providers/feishu/connection-credentials'
import { ReplySettings } from '#/features/admin/vikingbot/providers/feishu/reply-settings'

// Each supported platform owns its setup and credential UI.
export const providers = {
  feishu: {
    label: 'feishu' as const,
    addLabel: 'addFeishu' as const,
    Setup: FeishuConnect,
    Credentials: ConnectionCredentials,
    Settings: ReplySettings,
    summary: (connection: Connection) =>
      connection.settings?.thread_require_mention === false
        ? ('withoutMention' as const)
        : ('onlyMention' as const),
  },
}
export const upcomingProviders = ['Slack', 'DingTalk', 'Discord', 'Telegram']
export function getProvider(type?: string) {
  const key = type ?? 'feishu'
  return Object.hasOwn(providers, key)
    ? providers[key as keyof typeof providers]
    : undefined
}
