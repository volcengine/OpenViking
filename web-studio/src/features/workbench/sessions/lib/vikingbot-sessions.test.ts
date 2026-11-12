import { describe, expect, it } from 'vitest'

import { filterDuplicateVikingBotSessions } from './vikingbot-sessions'

const webId = 'vikingbot-web-005c50b7-8ebf-4e32-b980-cd49dbc41f73'
const legacyId = 'vikingbot-web-73435d4e-2a19b5d9-f8c3-4397-83fb-a7c20f06b714'
const storagePrefix = 'cli__default__abb324e50b61684e80a3806e%3A'
const storageSuffix = '~e87fe9fa5f2568d9'

function visibleIds(...ids: string[]) {
  return filterDuplicateVikingBotSessions(
    ids.map((session_id) => ({ session_id })),
  ).map((session) => session.session_id)
}

describe('Studio session list', () => {
  it('hides paired storage entries for current and older Studio web sessions', () => {
    expect(
      visibleIds(
        storagePrefix + webId + storageSuffix,
        'ordinary-session',
        storagePrefix + legacyId + storageSuffix,
        legacyId,
        webId,
      ),
    ).toEqual(['ordinary-session', legacyId, webId])
  })

  it('recognizes storage keys with an OpenAPI session generation', () => {
    const generation = '%3A00112233445566778899aabbccddeeff'
    const storageId = storagePrefix + webId + generation + storageSuffix
    expect(visibleIds(storageId, webId)).toEqual([webId])
  })

  it('keeps unpaired storage and unrelated bot sessions visible', () => {
    const unpaired = storagePrefix + legacyId + storageSuffix
    const other = storagePrefix + 'external-chat' + storageSuffix
    const differentChannel =
      storagePrefix.replace('default', 'other') + webId + storageSuffix
    expect(visibleIds(unpaired, other, differentChannel, webId)).toEqual([
      unpaired,
      other,
      differentChannel,
      webId,
    ])
  })
})
