// @vitest-environment jsdom

import * as React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SkillScopeTabs, getSkillsForScope } from './skill-scope-tabs'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      if (key === 'tabs.user') return '用户技能'
      if (key === 'tabs.agent') return '共享技能'
      return key
    },
  }),
}))

afterEach(cleanup)

describe('SkillScopeTabs', () => {
  const skills = [
    { name: 'user-reviewer', scope: 'user' as const },
    { name: 'shared-planner', scope: 'agent' as const },
  ]

  it('filters skills by the selected scope', () => {
    expect(getSkillsForScope(skills, 'user')).toEqual([skills[0]])
    expect(getSkillsForScope(skills, 'agent')).toEqual([skills[1]])
  })

  it('shows counts and switches to the selected scope', () => {
    const onScopeChange = vi.fn()

    render(
      <SkillScopeTabs
        activeScope="user"
        skills={skills}
        onScopeChange={onScopeChange}
      />,
    )

    const userTab = screen.getByRole('tab', { name: /用户技能1/ })
    const sharedTab = screen.getByRole('tab', { name: /共享技能1/ })

    expect(userTab.getAttribute('aria-selected')).toBe('true')
    expect(sharedTab.getAttribute('aria-selected')).toBe('false')

    fireEvent.click(sharedTab)

    expect(onScopeChange).toHaveBeenCalledWith('agent')
  })
})
