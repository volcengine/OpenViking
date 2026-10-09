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

it('keeps known clients and protocols and drops anything else', () => {
  expect(parseConnectSearch({ client: 'codex' })).toStrictEqual({
    client: 'codex',
    protocol: undefined,
  })
  expect(
    parseConnectSearch({ client: 'pi', protocol: 'anthropic' }),
  ).toStrictEqual({ client: 'pi', protocol: 'anthropic' })
  expect(
    parseConnectSearch({ client: 'cursor', protocol: 'grpc' }),
  ).toStrictEqual({ client: undefined, protocol: undefined })
  expect(parseConnectSearch({ client: 3 })).toStrictEqual({
    client: undefined,
    protocol: undefined,
  })
  expect(parseConnectSearch({})).toStrictEqual({
    client: undefined,
    protocol: undefined,
  })
})
