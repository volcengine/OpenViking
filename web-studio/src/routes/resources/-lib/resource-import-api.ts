import { postResources } from '#/lib/ov-client'
import type { ResourceImportRequest } from './resource-import-types'

type GeneratedResourceBody = Parameters<typeof postResources>[0]['body']

/**
 * The checked-in generated client can lag behind Web Studio resource options.
 * Isolate that compatibility boundary here instead of weakening every caller.
 */
export function postResourceImport(
  body: ResourceImportRequest,
  signal?: AbortSignal,
) {
  return postResources({
    body: body as GeneratedResourceBody,
    ...(signal ? { signal } : {}),
  })
}
