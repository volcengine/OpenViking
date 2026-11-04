import type { ServerAuthMode, ServiceSelection } from '../studio-contract'

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
