import { describe, expect, it } from 'vitest'

import type { Profile, ProfileSettings } from './api'
import {
  PROFILE_DEFAULTS,
  duplicateProfile,
  offeredTools,
  toProfileSettings,
  validateProfile,
} from './profile-schema'

const profile = (patch: Partial<ProfileSettings> = {}): ProfileSettings => ({
  ...PROFILE_DEFAULTS,
  ...patch,
})

describe('toProfileSettings', () => {
  it('keeps known fields only while preserving false and zero values', () => {
    const stored: Profile = {
      ...profile({ recall: false, score_threshold: 0, session_max_tokens: 0 }),
      id: 'p1',
      revision: 4,
    }
    const settings = toProfileSettings({
      ...stored,
      future_setting: 'discard',
    } as Profile)
    expect(settings).not.toHaveProperty('id')
    expect(settings).not.toHaveProperty('revision')
    expect(settings).not.toHaveProperty('future_setting')
    expect(settings).toMatchObject({
      recall: false,
      score_threshold: 0,
      session_max_tokens: 0,
    })
  })

  it('round-trips every field', () => {
    const stored = profile({
      quotas: { resources: 200 },
      context_window: 128000,
    })
    expect(toProfileSettings(stored)).toEqual(stored)
  })

  it('duplicates under a new name', () => {
    expect(
      duplicateProfile({ ...profile(), id: 'p', revision: 1 }, 'Copy'),
    ).toEqual({ ...PROFILE_DEFAULTS, name: 'Copy' })
  })
})

describe('validateProfile', () => {
  it('accepts the recommended settings', () => {
    expect(validateProfile(PROFILE_DEFAULTS)).toEqual({})
  })

  it('checks every limit, including hidden sections', () => {
    const errors = validateProfile(
      profile({
        name: ' ',
        max_tokens: 63,
        recall_timeout: 0,
        query_max_chars: Number.NaN,
        compaction_threshold: 0.99,
        summary_max_tokens: 500,
        context_window: 1000,
        window_hard_ratio: 0.3,
        tool_result_bytes: 1.5,
      }),
    )
    expect(Object.keys(errors).sort()).toEqual([
      'compaction_threshold',
      'context_window',
      'max_tokens',
      'name',
      'query_max_chars',
      'recall_timeout',
      'summary_max_tokens',
      'tool_result_bytes',
      'window_hard_ratio',
    ])
    expect(errors.recall_timeout?.key).toBe('validation.rangeExclusive')
    expect(errors.query_max_chars?.key).toBe('validation.required')
    expect(errors.tool_result_bytes?.key).toBe('validation.integer')
  })

  it('allows an empty context window', () => {
    expect(validateProfile(profile({ context_window: null }))).toEqual({})
  })

  it('needs the soft reminder below the hard one, even while agent windows are off', () => {
    for (const soft of [0.85, 0.9]) {
      expect(
        validateProfile(profile({ window_soft_ratio: soft })).window_soft_ratio,
      ).toEqual({ key: 'validation.softBelowHard' })
    }
    expect(
      validateProfile(profile({ agent_windows: true, window_soft_ratio: 0.5 })),
    ).toEqual({})
    // An out-of-range ratio reports its range instead.
    expect(
      validateProfile(profile({ window_soft_ratio: 0.96 })).window_soft_ratio
        ?.key,
    ).toBe('validation.range')
  })

  it('needs a source while recall is on', () => {
    expect(validateProfile(profile({ context_types: [] }))).toHaveProperty(
      'context_types',
    )
    expect(
      validateProfile(profile({ context_types: [], recall: false })),
    ).toEqual({})
  })

  it('checks category limits', () => {
    const errors = validateProfile(
      profile({
        quotas: { events: -1, unknown: 2 } as ProfileSettings['quotas'],
      }),
    )
    expect(errors['quotas.events']?.key).toBe('validation.min')
    expect(errors['quotas.unknown']).toEqual({
      key: 'validation.unknownCategory',
      values: { name: 'unknown' },
    })
    expect(
      validateProfile(profile({ quotas: { events: 0, skills: 0 } })).quotas,
    ).toEqual({ key: 'validation.quotasAllZero' })
    expect(validateProfile(profile({ quotas: { skills: 3 } }))).toEqual({})
  })

  it('allows any exclusions, including tools absent from the current catalog', () => {
    expect(
      validateProfile(
        profile({
          gateway_tools: true,
          disabled_tools: ['read', 'write', 'unknown'],
        }),
      ),
    ).toEqual({})
  })
})

describe('tool access', () => {
  const tools = ['read', 'write', 'future_tool'].map((name) => ({
    name,
    description: name,
  }))

  it('offers only read-only and future tools by default', () => {
    expect(PROFILE_DEFAULTS.gateway_tools).toBe(true)
    expect(
      offeredTools(PROFILE_DEFAULTS, tools).map((tool) => tool.name),
    ).toEqual(['read', 'future_tool'])
    expect(offeredTools(profile({ disabled_tools: [] }), tools)).toEqual(tools)
  })

  it('excludes only raw MCP names and enables new tools automatically', () => {
    const settings = profile({
      gateway_tools: true,
      disabled_tools: ['write', 'removed_tool'],
    })
    expect(offeredTools(settings, tools).map((tool) => tool.name)).toEqual([
      'read',
      'future_tool',
    ])
    expect(offeredTools({ ...settings, gateway_tools: false }, tools)).toEqual(
      [],
    )
    expect(
      offeredTools(
        { ...settings, disabled_tools: tools.map((tool) => tool.name) },
        tools,
      ),
    ).toEqual([])
  })
})
