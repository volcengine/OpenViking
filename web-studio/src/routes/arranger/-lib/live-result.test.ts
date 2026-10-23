import { describe, expect, it } from 'vitest'

import { formatLiveContextResult } from './live-result'

describe('live context result formatting', () => {
  it('extracts MCP text content', () => {
    expect(
      formatLiveContextResult({
        content: [
          { type: 'text', text: 'first' },
          { type: 'text', text: 'second' },
        ],
        isError: false,
      }),
    ).toEqual({ isError: false, text: 'first\n\nsecond' })
  })

  it('preserves structured results and MCP error state', () => {
    expect(
      formatLiveContextResult({
        isError: true,
        structuredContent: { code: 500, message: 'failed' },
      }),
    ).toEqual({
      isError: true,
      text: '{\n  "code": 500,\n  "message": "failed"\n}',
    })
  })
})
