import { describe, expect, it } from 'vitest'

import {
  accountModelDraftToSection,
  accountModelSectionToDraft,
  createEmptyAccountModelSectionDraft,
} from './account-model-config'

describe('account model configuration drafts', () => {
  it('round-trips the complete credential array without mask placeholders', () => {
    const draft = accountModelSectionToDraft({
      model: 'vision-default',
      timeout: 45,
      credentials: [
        {
          id: 'primary',
          provider: 'openai',
          model: 'vision-primary',
          api_key: 'secret-primary',
          api_base: 'https://primary.example/v1',
          extra_headers: { 'X-Tenant': 'acme' },
        },
        {
          id: 'backup',
          provider: 'litellm',
          api_key: 'secret-backup',
          forward_api_key: false,
          keepalive_expiry: 0,
          max_tokens: 4096,
        },
      ],
    })

    draft.model = 'vision-updated'
    const result = accountModelDraftToSection(draft)

    expect(result).toEqual({
      model: 'vision-updated',
      timeout: 45,
      credentials: [
        {
          id: 'primary',
          provider: 'openai',
          model: 'vision-primary',
          api_key: 'secret-primary',
          api_base: 'https://primary.example/v1',
          api_version: undefined,
          extra_headers: { 'X-Tenant': 'acme' },
          extra_request_body: undefined,
          forward_api_key: undefined,
          keepalive_expiry: undefined,
          max_tokens: undefined,
          reasoning_effort: undefined,
        },
        {
          id: 'backup',
          provider: 'litellm',
          model: undefined,
          api_key: 'secret-backup',
          api_base: undefined,
          api_version: undefined,
          extra_headers: undefined,
          extra_request_body: undefined,
          forward_api_key: false,
          keepalive_expiry: 0,
          max_tokens: 4096,
          reasoning_effort: undefined,
        },
      ],
    })
    expect(JSON.stringify(result)).not.toContain('••')
  })

  it.each([
    ['modelRequired', { model: '' }],
    ['timeoutPositive', { timeout: '0' }],
  ] as const)('rejects %s before submission', (code, change) => {
    const draft = {
      ...createEmptyAccountModelSectionDraft(),
      model: 'vision-default',
      ...change,
    }

    expect(() => accountModelDraftToSection(draft)).toThrowError(
      expect.objectContaining({ code }),
    )
  })

  it('rejects invalid provider extension JSON before submission', () => {
    const draft = createEmptyAccountModelSectionDraft()
    draft.model = 'vision-default'
    draft.credentials[0].extraRequestBody = '{not-json}'

    expect(() => accountModelDraftToSection(draft)).toThrowError(
      expect.objectContaining({
        code: 'invalidJsonObject',
        field: 'credentials.0.extra_request_body',
      }),
    )
  })
})
