import { describe, expect, it } from 'vitest'

import { stripVersionPrefix } from '#/features/workbench/monitoring/lib/version'

describe('stripVersionPrefix', () => {
  it('removes a leading version prefix', () => {
    expect(stripVersionPrefix('v0.4.16')).toBe('0.4.16')
    expect(stripVersionPrefix('V0.4.16')).toBe('0.4.16')
  })

  it('preserves versions without a prefix', () => {
    expect(stripVersionPrefix('0.4.16')).toBe('0.4.16')
  })
})
