import { describe, expect, it } from 'vitest'

import { getUserInitial } from './current-user-menu'

describe('getUserInitial', () => {
  it('uses the first character of the normalized user id', () => {
    expect(getUserInitial(' yufeng201 ')).toBe('Y')
  })

  it('supports non-Latin user ids', () => {
    expect(getUserInitial('用户')).toBe('用')
  })

  it('returns a fallback when the user id is empty', () => {
    expect(getUserInitial('  ')).toBe('?')
  })
})
