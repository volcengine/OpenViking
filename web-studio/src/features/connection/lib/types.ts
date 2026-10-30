import type { ServiceSelection } from '#/shared/lib/studio-service'
import type { ServerAuthMode } from '#/shared/types/auth'

export type ConnectionRole = 'admin' | 'root' | 'unknown' | 'user'

export type ConnectionDraft = {
  /** Last verified mode is an entry hint, never runtime authentication authority. */
  authMode?: ServerAuthMode
  serviceSelection?: ServiceSelection
  accountId: string
  adminApiKey: string
  apiKey: string
  baseUrl: string
  userId: string
}
