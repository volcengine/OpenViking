import { getOvResult, ovClient } from '#/lib/ov-client'

export type Connection = {
  id: string
  type?: string
  setup_mode?: string
  onboarding_id?: string
  app_id: string
  bot_name: string
  enabled: boolean
  step: number
  revision: number
  identity_user: string
  status: {
    state: string
    last_received?: string
    last_sent?: string
    verification?: {
      code: string
      expires_at: number
      received: boolean
      sent: boolean
      conversation?: string
    }
  }
}
export type PlatformMessage = {
  id: number
  role: string
  content: string
  sender: string
  time: string
  status: string
  conversation: string
}
const base = '/bot/v1/studio'
export function getCapabilities() {
  return getOvResult<{ enabled: boolean; can_manage: boolean }>(
    ovClient.client.get({ url: `${base}/capabilities` }),
  )
}
export function getConnections() {
  return getOvResult<Connection[]>(
    ovClient.client.get({ url: `${base}/connections` }),
  )
}
export function createConnection(body: {
  type?: string
  app_id: string
  app_secret: string
  user_id: string
}) {
  return getOvResult<Connection>(
    ovClient.client.post({ url: `${base}/connections`, body }),
  )
}
export function updateConnection(
  connection: Connection,
  action: string,
) {
  return getOvResult<Connection>(
    ovClient.client.patch({
      url: `${base}/connections/${connection.id}`,
      body: { action, revision: connection.revision },
    }),
  )
}
export function getConversations(id: string) {
  return getOvResult<
    Array<{
      conversation: string
      latest: number
      title: string
      preview?: string
      time?: string
    }>
  >(ovClient.client.get({ url: `${base}/connections/${id}/conversations` }))
}
export function getMessages(id: string, conversation: string, before = 0) {
  return getOvResult<PlatformMessage[]>(
    ovClient.client.get({
      url: `${base}/connections/${id}/messages`,
      query: { conversation, before },
    }),
  )
}

export function rotateCredentials(
  connection: Connection,
  appSecret: string,
  userId: string,
) {
  return getOvResult<Connection>(
    ovClient.client.patch({
      url: `${base}/connections/${connection.id}`,
      body: {
        action: 'credentials',
        revision: connection.revision,
        app_secret: appSecret,
        user_id: userId,
      },
    }),
  )
}

export function getBotUsers() {
  return getOvResult<Array<{ user_id: string; available: boolean }>>(
    ovClient.client.get({ url: `${base}/users` }),
  )
}
