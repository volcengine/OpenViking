// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from 'vitest'

import {
  createIdentityStorageKey,
  readPlaygroundExpandedUris,
  writePlaygroundExpandedUris,
} from './utils'

beforeEach(() => {
  localStorage.clear()
})

describe('createIdentityStorageKey', () => {
  it('isolates persisted Playground state by identity scope', () => {
    const baseKey = 'openviking.playground.terminalEntryHistory'

    expect(createIdentityStorageKey(baseKey, 'account-a\u0000alice')).not.toBe(
      createIdentityStorageKey(baseKey, 'account-b\u0000bob'),
    )
  })

  it('keeps the storage key browser-safe', () => {
    const key = createIdentityStorageKey(
      'openviking.playground.agentSessions',
      'http://localhost:1933\u0000api_key\u0000account-a\u0000alice',
    )

    expect(key).not.toContain('\u0000')
  })
})

describe('Playground expanded directory persistence', () => {
  it('restores normalized expanded URIs for the same identity', () => {
    writePlaygroundExpandedUris('account-a\u0000alice', [
      'viking://user/default',
      'viking://resources/',
    ])

    expect(readPlaygroundExpandedUris('account-a\u0000alice')).toEqual([
      'viking://user/default/',
      'viking://resources/',
    ])
    expect(readPlaygroundExpandedUris('account-a\u0000bob')).toEqual([])
  })
})
