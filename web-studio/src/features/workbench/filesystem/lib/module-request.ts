import { OV_PATHS } from '@openviking/react/client'
import { getOvClient, isOvClientError } from '#/shared/api/ov-client/index'
import { normalizeReadContent } from '#/shared/lib/viking-fs'

const textPaths = new Set<string>([
  OV_PATHS.contentRead,
  OV_PATHS.contentAbstract,
  OV_PATHS.contentOverview,
])

export const requestWorkbenchModule: typeof fetch = async (input, init) => {
  // Use the same scoped Axios client as the rest of Studio, including its
  // credential, gateway and request logging behavior.
  const url = new URL(String(input), window.location.origin)
  const binary = url.pathname === OV_PATHS.contentDownload
  try {
    const { status, data } = await getOvClient().instance.request({
      url: url.pathname + url.search,
      method: init?.method ?? 'GET',
      data: init?.body,
      headers: Object.fromEntries(new Headers(init?.headers)),
      signal: init?.signal ?? undefined,
      responseType: binary ? 'blob' : undefined,
    })
    if (status === 204 || status === 205) return new Response(null, { status })
    if (binary) return new Response(data as Blob, { status })
    const body =
      textPaths.has(url.pathname) &&
      data &&
      typeof data === 'object' &&
      'result' in data &&
      typeof data.result !== 'string'
        ? { ...data, result: normalizeReadContent(data.result) }
        : data
    return new Response(JSON.stringify(body), { status })
  } catch (error) {
    if (!isOvClientError(error) || !error.statusCode) throw error
    return new Response(
      JSON.stringify({
        status: 'error',
        error: {
          code: error.code,
          message: error.message,
          details: error.details,
        },
      }),
      { status: error.statusCode >= 400 ? error.statusCode : 400 },
    )
  }
}
