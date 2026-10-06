export const ACCOUNT_MODEL_PROVIDERS = [
  'volcengine',
  'openai',
  'azure',
  'kimi',
  'glm',
  'litellm',
  'openai-codex',
] as const

export type AccountModelProvider = (typeof ACCOUNT_MODEL_PROVIDERS)[number]
export type AccountModelSection = 'query_planner' | 'vlm'

export type AccountModelCredential = {
  api_base?: string
  api_key?: string
  api_version?: string
  extra_headers?: Record<string, string>
  extra_request_body?: Record<string, unknown>
  forward_api_key?: boolean
  id?: string
  keepalive_expiry?: number
  max_tokens?: number
  model?: string
  provider: string
  reasoning_effort?: string
}

export type AccountModelSectionConfig = {
  credentials: AccountModelCredential[]
  model: string
  timeout?: number
}

export type AccountModelSettings = Partial<
  Record<AccountModelSection, AccountModelSectionConfig>
>

export type AccountModelSettingsPatch = Partial<
  Record<AccountModelSection, AccountModelSectionConfig | null>
>

export type AccountModelConfiguration = {
  account_id: string
  settings: AccountModelSettings
}

export type AccountModelCredentialDraft = {
  apiBase: string
  apiKey: string
  apiVersion: string
  extraHeaders: string
  extraRequestBody: string
  forwardApiKey: '' | 'false' | 'true'
  id: string
  keepaliveExpiry: string
  maxTokens: string
  model: string
  provider: string
  reasoningEffort: string
}

export type AccountModelSectionDraft = {
  credentials: AccountModelCredentialDraft[]
  model: string
  timeout: string
}

export type AccountModelValidationCode =
  | 'credentialsRequired'
  | 'headersMustBeStrings'
  | 'invalidJsonObject'
  | 'keepaliveNonNegative'
  | 'maxTokensPositiveInteger'
  | 'modelRequired'
  | 'providerRequired'
  | 'providerUnsupported'
  | 'streamUnsupported'
  | 'timeoutPositive'

export class AccountModelValidationError extends Error {
  constructor(
    public readonly code: AccountModelValidationCode,
    public readonly field: string,
  ) {
    super(code)
  }
}

function optionalString(value: string): string | undefined {
  const normalized = value.trim()
  return normalized || undefined
}

function optionalSecret(value: string): string | undefined {
  return value.trim() ? value : undefined
}

function formatJsonObject(value: Record<string, unknown> | undefined): string {
  return value ? JSON.stringify(value, null, 2) : ''
}

function parseOptionalNumber(
  value: string,
  field: string,
  rule: 'non-negative' | 'positive' | 'positive-integer',
): number | undefined {
  if (!value.trim()) {
    return undefined
  }

  const parsed = Number(value)
  const valid =
    Number.isFinite(parsed) &&
    (rule === 'non-negative'
      ? parsed >= 0
      : rule === 'positive-integer'
        ? Number.isInteger(parsed) && parsed > 0
        : parsed > 0)
  if (!valid) {
    throw new AccountModelValidationError(
      rule === 'non-negative'
        ? 'keepaliveNonNegative'
        : rule === 'positive-integer'
          ? 'maxTokensPositiveInteger'
          : 'timeoutPositive',
      field,
    )
  }
  return parsed
}

function parseOptionalObject(
  value: string,
  field: string,
): Record<string, unknown> | undefined {
  if (!value.trim()) {
    return undefined
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new AccountModelValidationError('invalidJsonObject', field)
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new AccountModelValidationError('invalidJsonObject', field)
  }
  return parsed as Record<string, unknown>
}

export function createEmptyAccountModelCredentialDraft(): AccountModelCredentialDraft {
  return {
    apiBase: '',
    apiKey: '',
    apiVersion: '',
    extraHeaders: '',
    extraRequestBody: '',
    forwardApiKey: '',
    id: '',
    keepaliveExpiry: '',
    maxTokens: '',
    model: '',
    provider: 'openai',
    reasoningEffort: '',
  }
}

export function createEmptyAccountModelSectionDraft(): AccountModelSectionDraft {
  return {
    credentials: [createEmptyAccountModelCredentialDraft()],
    model: '',
    timeout: '',
  }
}

export function accountModelSectionToDraft(
  value: AccountModelSectionConfig,
): AccountModelSectionDraft {
  return {
    model: value.model,
    timeout: value.timeout === undefined ? '' : String(value.timeout),
    credentials: value.credentials.map((credential) => ({
      apiBase: credential.api_base ?? '',
      apiKey: credential.api_key ?? '',
      apiVersion: credential.api_version ?? '',
      extraHeaders: formatJsonObject(credential.extra_headers),
      extraRequestBody: formatJsonObject(credential.extra_request_body),
      forwardApiKey:
        credential.forward_api_key === undefined
          ? ''
          : String(credential.forward_api_key),
      id: credential.id ?? '',
      keepaliveExpiry:
        credential.keepalive_expiry === undefined
          ? ''
          : String(credential.keepalive_expiry),
      maxTokens:
        credential.max_tokens === undefined
          ? ''
          : String(credential.max_tokens),
      model: credential.model ?? '',
      provider: credential.provider,
      reasoningEffort: credential.reasoning_effort ?? '',
    })),
  }
}

export function accountModelDraftToSection(
  draft: AccountModelSectionDraft,
): AccountModelSectionConfig {
  const model = draft.model.trim()
  if (!model) {
    throw new AccountModelValidationError('modelRequired', 'model')
  }
  if (draft.credentials.length === 0) {
    throw new AccountModelValidationError('credentialsRequired', 'credentials')
  }

  const credentials = draft.credentials.map((credential, index) => {
    const fieldPrefix = `credentials.${index}`
    const provider = credential.provider.trim().toLowerCase()
    if (!provider) {
      throw new AccountModelValidationError(
        'providerRequired',
        `${fieldPrefix}.provider`,
      )
    }
    if (!ACCOUNT_MODEL_PROVIDERS.includes(provider as AccountModelProvider)) {
      throw new AccountModelValidationError(
        'providerUnsupported',
        `${fieldPrefix}.provider`,
      )
    }

    const extraHeaders = parseOptionalObject(
      credential.extraHeaders,
      `${fieldPrefix}.extra_headers`,
    )
    if (
      extraHeaders &&
      Object.values(extraHeaders).some((value) => typeof value !== 'string')
    ) {
      throw new AccountModelValidationError(
        'headersMustBeStrings',
        `${fieldPrefix}.extra_headers`,
      )
    }
    const extraRequestBody = parseOptionalObject(
      credential.extraRequestBody,
      `${fieldPrefix}.extra_request_body`,
    )
    if (extraRequestBody && 'stream' in extraRequestBody) {
      throw new AccountModelValidationError(
        'streamUnsupported',
        `${fieldPrefix}.extra_request_body`,
      )
    }

    return {
      id: optionalString(credential.id),
      provider,
      model: optionalString(credential.model),
      api_key: optionalSecret(credential.apiKey),
      api_base: optionalString(credential.apiBase),
      api_version: optionalString(credential.apiVersion),
      forward_api_key:
        credential.forwardApiKey === ''
          ? undefined
          : credential.forwardApiKey === 'true',
      extra_headers: extraHeaders as Record<string, string> | undefined,
      extra_request_body: extraRequestBody,
      reasoning_effort: optionalString(credential.reasoningEffort),
      keepalive_expiry: parseOptionalNumber(
        credential.keepaliveExpiry,
        `${fieldPrefix}.keepalive_expiry`,
        'non-negative',
      ),
      max_tokens: parseOptionalNumber(
        credential.maxTokens,
        `${fieldPrefix}.max_tokens`,
        'positive-integer',
      ),
    }
  })

  return {
    model,
    credentials,
    timeout: parseOptionalNumber(draft.timeout, 'timeout', 'positive'),
  }
}
