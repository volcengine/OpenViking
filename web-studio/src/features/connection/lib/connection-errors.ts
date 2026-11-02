import { normalizeOvClientError } from '#/shared/api/ov-client/index'
import type { ConnectionDraft } from './types'

export class StudioSetupError extends Error {
  constructor(
    public readonly code:
      | 'invalidService'
      | 'keyRequired'
      | 'directoryIdentityMismatch',
  ) {
    super(code)
  }
}

export class StudioSetupRequestError extends Error {
  readonly stage: 'health' | 'data'
  readonly code: string
  readonly statusCode?: number

  constructor(
    stage: 'health' | 'data',
    cause: unknown,
    connection: ConnectionDraft,
  ) {
    const error = normalizeOvClientError(cause)
    let message = error.message
    for (const secret of [connection.apiKey, connection.adminApiKey].flatMap(
      (key) => [key, key.trim()],
    )) {
      if (secret) message = message.replaceAll(secret, '[redacted]')
    }
    super(`${error.statusCode ? `HTTP ${error.statusCode}: ` : ''}${message}`, {
      cause,
    })
    this.stage = stage
    this.code = error.code
    this.statusCode = error.statusCode
  }
}
