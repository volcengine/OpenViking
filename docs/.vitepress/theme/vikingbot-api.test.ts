import assert from 'node:assert/strict'
import test from 'node:test'
import { getVikingBotUserId, normalizeVikingBotQuery, VikingBotApiError } from './vikingbot-api.ts'

test('normalizes VikingBot queries and enforces the UI limit', () => {
  assert.equal(normalizeVikingBotQuery('  How does memory work?  '), 'How does memory work?')

  assert.throws(
    () => normalizeVikingBotQuery('   '),
    (error) => error instanceof VikingBotApiError && error.message === 'empty_query'
  )
  assert.throws(
    () => normalizeVikingBotQuery('x'.repeat(501)),
    (error) => error instanceof VikingBotApiError && error.message === 'query_too_long'
  )
})

test('reuses the stable VikingBot user id from storage', () => {
  const values = new Map<string, string>([['client_id', 'existing-user']])
  const storage = {
    getItem(key: string) {
      return values.get(key) ?? null
    },
    setItem(key: string, value: string) {
      values.set(key, value)
    }
  }

  assert.equal(getVikingBotUserId(storage), 'existing-user')
  assert.equal(values.size, 1)
})
