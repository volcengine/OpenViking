import { describe, expect, it } from 'vitest'
import {
  createVikingBotWebSessionId,
  isVikingBotWebSession,
} from '#/lib/sessions/vikingbot-sessions'

describe('VikingBot web session ownership', () => {
  it.each([
    'cx-01a098bf-0500-7ee2-9178-460fa176331f',
    'chatcrew-group-f23e11e8036f79be',
    'cli__default__7b3e8760',
    '__openviking_resource_reason__',
    '01a098bf-0500-7ee2-9178-460fa176331f',
    'vikingbot-web-unknown',
  ])('does not relabel external or unclassified session %s', (session_id) => {
    expect(isVikingBotWebSession({ session_id })).toBe(false)
  })

  it('recognizes persisted ownership independently of browser state', () => {
    expect(
      isVikingBotWebSession({
        session_id: 'vikingbot-web-63507b10-0000-4000-8000-000000000001',
      }),
    ).toBe(true)
    const first = createVikingBotWebSessionId()
    expect(isVikingBotWebSession({ session_id: first })).toBe(true)
    expect(createVikingBotWebSessionId()).not.toBe(first)
  })
})

it('includes only explicitly registered legacy playground sessions', () => {
  const legacy = 'f5216b16-cd10-4e9a-b542-a29576f2260c'
  expect(isVikingBotWebSession({ session_id: legacy }, [legacy])).toBe(true)
  expect(isVikingBotWebSession({ session_id: legacy }, [])).toBe(false)
  expect(isVikingBotWebSession({ session_id: 'cx-other' }, [legacy])).toBe(
    false,
  )
})
