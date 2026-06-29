import { describe, expect, it } from 'vitest'

import { createPlaygroundAgentSessionId } from './utils'

describe('createPlaygroundAgentSessionId', () => {
  it('creates vikingbot-style session ids for Studio Agent sessions', () => {
    const sessionId = createPlaygroundAgentSessionId()

    expect(sessionId).toMatch(
      /^bot-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    )
  })
})
