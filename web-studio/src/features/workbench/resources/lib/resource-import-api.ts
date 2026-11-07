import type { ResourceImportRequest } from '#/features/workbench/resources/lib/resource-import-types'
import type { OvClientAdapter } from '#/shared/api/ov-client/index'
import { postResources } from '#/shared/api/ov-client/index'

type GeneratedResourceBody = Parameters<typeof postResources>[0]['body']

/**
 * The checked-in generated client can lag behind Web Studio resource options.
 * Isolate that compatibility boundary here instead of weakening every caller.
 */
export function postResourceImport(
  body: ResourceImportRequest,
  options: { client: OvClientAdapter['client']; signal: AbortSignal },
) {
  return postResources({
    body: body as GeneratedResourceBody,
    ...options,
  })
}
