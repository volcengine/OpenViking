import type { Protocol } from './api'
import { CLIENT_IDS } from './client-guides'
import type { ClientId } from './client-guides'
import { PROTOCOLS } from './upstream-schema'

/*
 * `validateSearch` parsers for the gateway pages. Each returns its key even
 * when the value is invalid: the router lays validated params over the raw
 * ones, so an omitted key would let `?filter=bogus` through unchanged.
 */

/** Route param value that opens an editor for a new upstream or profile. */
export const NEW_ID = 'new'

/** Request-log quick filters; absent means all requests. */
export type RequestsFilter = 'messages' | 'tools' | 'issues'

export type RequestsSearch = { filter?: RequestsFilter }

const REQUESTS_FILTERS: RequestsFilter[] = ['messages', 'tools', 'issues']

/** `/context-gateway/requests?filter=issues`. */
export function parseRequestsSearch(
  search: Record<string, unknown>,
): RequestsSearch {
  const { filter } = search
  return {
    filter: REQUESTS_FILTERS.includes(filter as RequestsFilter)
      ? (filter as RequestsFilter)
      : undefined,
  }
}

export type ProfileEditorSearch = { from?: string }

/** Profile editor; `?from=<id>` duplicates a profile. */
export function parseProfileEditorSearch(
  search: Record<string, unknown>,
): ProfileEditorSearch {
  const { from } = search
  return { from: typeof from === 'string' && from ? from : undefined }
}

export type ConnectSearch = { client?: ClientId; protocol?: Protocol }

/**
 * `/context-gateway/connect?client=pi&protocol=anthropic`. The page checks
 * that the client supports the protocol.
 */
export function parseConnectSearch(
  search: Record<string, unknown>,
): ConnectSearch {
  const { client, protocol } = search
  return {
    client: CLIENT_IDS.includes(client as ClientId)
      ? (client as ClientId)
      : undefined,
    protocol: PROTOCOLS.includes(protocol as Protocol)
      ? (protocol as Protocol)
      : undefined,
  }
}
