import { describe, expect, it } from 'vitest'

import type {
  GatewayAccess,
  GatewayKey,
  Profile,
  Upstream,
} from '#/features/admin/gateway/lib/api'
import { PROFILE_DEFAULTS } from '#/features/admin/gateway/lib/profile-schema'
import type { QuickInputs } from '#/features/admin/gateway/lib/quick-start'
import {
  isQuickUsable,
  quickPlan,
  quickProfile,
} from '#/features/admin/gateway/lib/quick-start'
import { UPSTREAM_DEFAULTS } from '#/features/admin/gateway/lib/upstream-schema'

function upstream(overrides: Partial<Upstream> = {}): Upstream {
  return {
    ...UPSTREAM_DEFAULTS,
    id: 'vlm-ark',
    revision: 1,
    name: 'vlm-ark',
    base_url: 'https://ark.example.com/api/v3',
    models: ['doubao-seed-1-6'],
    has_api_key: true,
    ...overrides,
  }
}

function profile(id: string, name: string): Profile {
  return { ...PROFILE_DEFAULTS, id, name, revision: 1 }
}

const access = (overrides: Partial<GatewayAccess> = {}): GatewayAccess => ({
  managed: false,
  can_write_objects: true,
  can_import_vlm: true,
  can_issue_for_others: false,
  role: 'user',
  account_id: 'default',
  user_id: 'default',
  warnings: [],
  ...overrides,
})

const key: GatewayKey = {
  id: 'k1',
  revision: 1,
  name: 'Laptop',
  policy_id: 'default',
  upstream_ids: ['vlm-ark'],
  models: [],
  user_id: 'default',
  prefix: 'ovgw_Ab3dE9x',
  created_at: 1_700_000_000,
}

const usable = upstream()
const recommended = profile('default', 'Default')
const locked = access({
  managed: true,
  can_write_objects: false,
  can_import_vlm: false,
})

function inputs(overrides: Partial<QuickInputs> = {}): QuickInputs {
  return {
    keys: [],
    upstreams: [],
    profiles: [],
    access: access(),
    readOnly: false,
    gatewayHost: false,
    ...overrides,
  }
}

describe('isQuickUsable', () => {
  it('accepts an enabled managed upstream with a key and models', () => {
    expect(isQuickUsable(usable)).toBe(true)
    expect(
      isQuickUsable(upstream({ models: [], aliases: { fast: 'x' } })),
    ).toBe(true)
    expect(
      isQuickUsable(upstream({ coding_plan: true, allow_coding_plan: true })),
    ).toBe(true)
  })

  it.each([
    ['disabled', { enabled: false }],
    ['passthrough', { auth_mode: 'passthrough' as const }],
    ['without an API key', { has_api_key: false }],
    ['an unconfirmed Coding Plan', { coding_plan: true }],
    ['without model names', { models: [] }],
  ])('rejects an upstream %s', (_label, overrides) => {
    expect(isQuickUsable(upstream(overrides))).toBe(false)
  })
})

describe('quickProfile', () => {
  it('prefers the default profile, else the first by name', () => {
    const b = profile('p2', 'Beta')
    const a = profile('p1', 'Alpha')
    expect(quickProfile([b, recommended, a])).toBe(recommended)
    expect(quickProfile([b, a])).toBe(a)
    expect(quickProfile([])).toBeUndefined()
  })
})

describe('quickPlan', () => {
  it.each<
    [string, Partial<QuickInputs>, { shown: boolean; runnable: boolean }]
  >([
    [
      'dev mode after seeding',
      { upstreams: [usable], profiles: [recommended] },
      { shown: true, runnable: true },
    ],
    ['an empty gateway that can import', {}, { shown: true, runnable: true }],
    [
      'once a key exists',
      { keys: [key], upstreams: [usable], profiles: [recommended] },
      { shown: false, runnable: true },
    ],
    ['while keys load', { keys: undefined }, { shown: false, runnable: true }],
    [
      'locked with an upstream and a profile',
      {
        access: locked,
        readOnly: true,
        upstreams: [usable],
        profiles: [recommended],
      },
      { shown: true, runnable: true },
    ],
    [
      'locked without a profile on an embedded gateway',
      { access: locked, readOnly: true, upstreams: [usable] },
      { shown: true, runnable: false },
    ],
    [
      'locked without an upstream on an embedded gateway',
      { access: locked, readOnly: true, profiles: [recommended] },
      { shown: true, runnable: false },
    ],
    [
      'locked without an upstream on a standalone gateway',
      { access: locked, readOnly: true, gatewayHost: true },
      { shown: false, runnable: false },
    ],
    [
      'locked with an upstream but no profile on a standalone gateway',
      {
        access: locked,
        readOnly: true,
        gatewayHost: true,
        upstreams: [usable],
      },
      { shown: true, runnable: false },
    ],
    [
      'a user who can neither import nor finds an upstream',
      { access: access({ can_import_vlm: false }) },
      { shown: false, runnable: false },
    ],
    [
      'only an unconfirmed Coding Plan upstream, without import',
      {
        access: access({ can_import_vlm: false }),
        upstreams: [upstream({ coding_plan: true })],
        profiles: [recommended],
      },
      { shown: false, runnable: false },
    ],
  ])('%s', (_label, overrides, expected) => {
    const plan = quickPlan(inputs(overrides))
    expect({ shown: plan.shown, runnable: plan.runnable }).toEqual(expected)
  })

  it('reports the upstreams, profile and owner it will use', () => {
    const blocked = upstream({ id: 'cp', coding_plan: true })
    const plan = quickPlan(
      inputs({ upstreams: [blocked, usable], profiles: [recommended] }),
    )
    expect(plan.upstreams).toEqual([usable])
    expect(plan.profile).toBe(recommended)
    expect(plan.pickUser).toBe(false)
  })

  it('lets root outside dev mode pick the user', () => {
    const root = access({ role: 'root', can_issue_for_others: true })
    const devRoot = access({ role: 'root' })
    expect(quickPlan(inputs({ access: root })).pickUser).toBe(true)
    expect(quickPlan(inputs({ access: devRoot })).pickUser).toBe(false)
  })
})
