import type { ServiceSelection } from '../studio-service'

export type ConnectionRole = 'admin' | 'root' | 'unknown' | 'user'

export type ConnectionDraft = {
  serviceSelection?: ServiceSelection
  accountId: string
  adminApiKey: string
  apiKey: string
  baseUrl: string
  userId: string
}
