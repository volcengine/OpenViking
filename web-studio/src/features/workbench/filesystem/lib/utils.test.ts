// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from 'vitest'

import {
  createIdentityStorageKey,
  readFilesystemAgentSessionIds,
  registerFilesystemAgentSessionId,
} from '#/features/workbench/filesystem/lib/utils'

beforeEach(() => {
  localStorage.clear()
})

describe('createIdentityStorageKey', () => {
  it('isolates persisted Filesystem state by identity scope', () => {
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

it('preserves legacy Agent history without sharing it across identities', () => {
  const legacyKey = createIdentityStorageKey(
    'openviking.playground.agentSessions',
    'account-a\u0000alice',
  )
  localStorage.setItem(legacyKey, JSON.stringify(['existing-session']))

  expect(readFilesystemAgentSessionIds('account-a\u0000alice')).toEqual([
    'existing-session',
  ])
  registerFilesystemAgentSessionId('new-session', 'account-a\u0000alice')
  expect(JSON.parse(localStorage.getItem(legacyKey)!)).toEqual([
    'new-session',
    'existing-session',
  ])
  expect(readFilesystemAgentSessionIds('account-a\u0000bob')).toEqual([])
})
