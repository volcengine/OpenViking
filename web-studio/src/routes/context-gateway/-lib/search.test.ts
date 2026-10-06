import { expect, it } from 'vitest'

import {
  parseConnectSearch,
  parseProfileEditorSearch,
  parseRequestsSearch,
} from './search'

// `toStrictEqual`: an invalid value must come back as an explicit undefined,
// or the router keeps the raw one.

it('keeps only known request filters', () => {
  expect(parseRequestsSearch({ filter: 'issues' })).toStrictEqual({
    filter: 'issues',
  })
  expect(parseRequestsSearch({ filter: 'everything' })).toStrictEqual({
    filter: undefined,
  })
  expect(parseRequestsSearch({})).toStrictEqual({ filter: undefined })
})

it('reads the profile to duplicate', () => {
  expect(parseProfileEditorSearch({ from: 'p1' })).toStrictEqual({
    from: 'p1',
  })
  expect(parseProfileEditorSearch({ from: '' })).toStrictEqual({
    from: undefined,
  })
  expect(parseProfileEditorSearch({ from: 3 })).toStrictEqual({
    from: undefined,
  })
})

it('keeps known clients and drops anything else', () => {
  expect(parseConnectSearch({ client: 'codex' })).toStrictEqual({
    client: 'codex',
  })
  expect(parseConnectSearch({ client: 'cursor' })).toStrictEqual({
    client: undefined,
  })
  expect(parseConnectSearch({ client: 3 })).toStrictEqual({
    client: undefined,
  })
  expect(parseConnectSearch({})).toStrictEqual({ client: undefined })
})
