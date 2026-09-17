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
  settings?: Record<string, unknown>
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
export function botAccountBase() {
  const { accountId } = ovClient.getConnection()
  if (!accountId) throw new Error('Select an account before managing bots')
  return `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/bot`
}
export function getCapabilities() {
  return getOvResult<{ enabled: boolean; can_manage: boolean }>(
    ovClient.client.get({ url: '/api/v1/admin/bot/capabilities' }),
  )
}
export function getConnections() {
  return getOvResult<Connection[]>(
    ovClient.client.get({ url: `${botAccountBase()}/connections` }),
  )
}
export function createConnection(body: {
  type: string
  credentials: Record<string, string>
  settings?: Record<string, unknown>
  user_id: string
}) {
  return getOvResult<Connection>(
    ovClient.client.post({ url: `${botAccountBase()}/connections`, body }),
  )
}
export function updateConnection(
  connection: Connection,
  action: 'pause' | 'resume',
) {
  return getOvResult<Connection>(
    ovClient.client.patch({
      url: `${botAccountBase()}/connections/${encodeURIComponent(connection.id)}`,
      body: { enabled: action === 'resume', revision: connection.revision },
    }),
  )
}
export function deleteConnection(connection: Connection) {
  return getOvResult<{ deleted: boolean }>(
    ovClient.client.delete({
      url: `${botAccountBase()}/connections/${encodeURIComponent(connection.id)}`,
      query: { revision: connection.revision },
    }),
  )
}
export function verifyConnection(connection: Connection) {
  return getOvResult<Connection>(
    ovClient.client.post({
      url: `${botAccountBase()}/connections/${encodeURIComponent(connection.id)}/verifications`,
      body: { revision: connection.revision },
    }),
  )
}
export function getConversations(id: string) {
  return getOvResult<
    Array<{
      conversation: string
      latest: number
      title: string
      group_name?: string
      preview?: string
      time?: string
    }>
  >(
    ovClient.client.get({
      url: `${botAccountBase()}/connections/${encodeURIComponent(id)}/conversations`,
    }),
  )
}
export function getMessages(id: string, conversation: string, before = 0) {
  return getOvResult<PlatformMessage[]>(
    ovClient.client.get({
      url: `${botAccountBase()}/connections/${encodeURIComponent(id)}/messages`,
      query: { conversation, before },
    }),
  )
}

export function rotateCredentials(
  connection: Connection,
  credentials: Record<string, string>,
  userId: string,
) {
  return getOvResult<Connection>(
    ovClient.client.post({
      url: `${botAccountBase()}/connections/${encodeURIComponent(connection.id)}/credentials`,
      body: {
        revision: connection.revision,
        credentials,
        user_id: userId,
      },
    }),
  )
}

export async function getBotUsers() {
  const { accountId } = ovClient.getConnection()
  if (!accountId) throw new Error('Select an account before managing bots')
  const users = await getOvResult<
    Array<{ user_id: string; api_key_available: boolean }>
  >(
    ovClient.client.get({
      url: `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/users`,
      query: { role: 'user', include_credentials: false },
    }),
  )
  return users.map((user) => ({
    user_id: user.user_id,
    available: user.api_key_available,
  }))
}

export function updateConnectionSettings(
  connection: Connection,
  settings: Record<string, unknown>,
) {
  return getOvResult<Connection>(
    ovClient.client.patch({
      url: `${botAccountBase()}/connections/${encodeURIComponent(connection.id)}`,
      body: { settings, revision: connection.revision },
    }),
  )
}
