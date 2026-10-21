import { describe, expect, it } from 'vitest'

import {
  buildMemoryPolicy,
  identifyMemoryPreset,
  memoryPresets,
} from './user-memory-policy'

describe('working memory opt-in', () => {
  it.each(memoryPresets)(
    '%s defaults off and survives preset identification',
    (preset) => {
      const policy = buildMemoryPolicy(preset)
      expect(policy.working_memory?.enabled).toBe(false)
      expect(identifyMemoryPreset(policy)).toBe(preset)
      expect(
        identifyMemoryPreset({ ...policy, working_memory: { enabled: true } }),
      ).toBe('custom')
    },
  )

  it('treats a legacy missing WM field as disabled', () => {
    expect(identifyMemoryPreset({})).toBe('general')
  })
})
