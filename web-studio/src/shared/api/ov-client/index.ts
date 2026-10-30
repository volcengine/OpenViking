export * from '#/gen/ov-client'

export { createOvClient, ovClient } from '#/shared/api/ov-client/client'
export {
  OvClientError,
  getOvResult,
  isOvClientError,
  normalizeOvClientError,
  unwrapOvResponse,
} from '#/shared/api/ov-client/errors'
export {
  DEFAULT_API_KEY_STORAGE_KEY,
  type OvClientAdapter,
  type OvClientErrorOptions,
  type OvClientOptions,
  type OvConnectionState,
  type OvErrorEnvelope,
  type OvResponse,
  type OvSuccessEnvelope,
} from '#/shared/api/ov-client/types'
