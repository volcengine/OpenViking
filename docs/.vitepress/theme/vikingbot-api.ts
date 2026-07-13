const DEFAULT_API_BASE_URL =
  'https://sd7aepg2caqr3a7a3g870.apigateway-cn-shanghai.volceapi.com/api/v1'
const VIKINGBOT_API_KEY = 'kjWSxIHxa0hRk9C/0gSFvA=='
const REQUEST_TIMEOUT_MS = 60_000
const CLIENT_ID_KEY = 'client_id'

type ApiResponse<T> = {
  status: string
  err_code: string
  err_msg: string
  result: T
}

export type VikingBotChatResult = {
  text: string
}

export class VikingBotApiError extends Error {
  readonly code: string
  readonly httpStatus?: number

  constructor(
    message: string,
    code = '',
    httpStatus?: number
  ) {
    super(message)
    this.name = 'VikingBotApiError'
    this.code = code
    this.httpStatus = httpStatus
  }
}

export function getVikingBotUserId(storage: Pick<Storage, 'getItem' | 'setItem'> = localStorage) {
  let id = storage.getItem(CLIENT_ID_KEY)
  if (!id) {
    id = crypto.randomUUID()
    storage.setItem(CLIENT_ID_KEY, id)
  }
  return id
}

export function normalizeVikingBotQuery(query: string) {
  const normalized = query.trim()
  if (!normalized) throw new VikingBotApiError('empty_query')
  if (normalized.length > 500) throw new VikingBotApiError('query_too_long')
  return normalized
}

export async function chatWithVikingBot(query: string): Promise<VikingBotChatResult> {
  const normalizedQuery = normalizeVikingBotQuery(query)
  const apiBaseUrl = import.meta.env.VITE_VIKINGBOT_API_BASE_URL || DEFAULT_API_BASE_URL

  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)

  try {
    const response = await fetch(`${apiBaseUrl}/bot/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-OpenViking-Bot-Key': VIKINGBOT_API_KEY
      },
      body: JSON.stringify({
        user_id: getVikingBotUserId(),
        query: normalizedQuery
      }),
      signal: controller.signal
    })

    let data: ApiResponse<VikingBotChatResult>
    try {
      data = (await response.json()) as ApiResponse<VikingBotChatResult>
    } catch {
      throw new VikingBotApiError(
        response.ok ? 'invalid_response' : `HTTP ${response.status}`,
        '',
        response.status
      )
    }

    if (!response.ok) {
      throw new VikingBotApiError(
        data.err_msg || `HTTP ${response.status}`,
        data.err_code,
        response.status
      )
    }

    if (data.status !== 'ok') {
      throw new VikingBotApiError(
        data.err_msg || data.err_code || 'api_error',
        data.err_code,
        response.status
      )
    }

    if (!data.result || typeof data.result.text !== 'string') {
      throw new VikingBotApiError('invalid_response', '', response.status)
    }

    return data.result
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new VikingBotApiError('request_timeout')
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}
