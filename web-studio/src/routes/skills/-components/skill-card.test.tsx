// @vitest-environment jsdom

import * as React from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SkillCard } from './skill-card'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { name?: string }) => {
      if (key === 'scopes.user') return '用户技能'
      if (key === 'scopes.agent') return '共享技能'
      if (key === 'share') return '共享'
      if (key === 'sharing') return '共享中...'
      if (key === 'shareSkill') return `将 ${values?.name} 设为共享技能`
      if (key === 'viewDetail') return `查看 ${values?.name} 详情`
      if (key === 'detail') return '详情'
      return key
    },
  }),
}))

afterEach(cleanup)

describe('SkillCard', () => {
  const userSkill = {
    description: 'A private skill',
    name: 'private-reviewer',
    scope: 'user' as const,
    uri: 'viking://user/default/skills/private-reviewer',
  }

  it('opens the skill detail from the full-card action', () => {
    const onOpen = vi.fn()

    render(
      <SkillCard
        isSharing={false}
        skill={userSkill}
        onOpen={onOpen}
        onShare={vi.fn()}
      />,
    )

    fireEvent.click(
      screen.getByRole('button', {
        name: '查看 private-reviewer 详情',
      }),
    )

    expect(onOpen).toHaveBeenCalledOnce()
  })

  it('offers the share action for a user skill', () => {
    const onOpen = vi.fn()
    const onShare = vi.fn()

    render(
      <SkillCard
        isSharing={false}
        skill={userSkill}
        onOpen={onOpen}
        onShare={onShare}
      />,
    )

    fireEvent.click(
      screen.getByRole('button', {
        name: '将 private-reviewer 设为共享技能',
      }),
    )

    expect(onShare).toHaveBeenCalledOnce()
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('does not offer the share action for a shared skill', () => {
    render(
      <SkillCard
        isSharing={false}
        skill={{
          ...userSkill,
          scope: 'agent',
          uri: 'viking://agent/skills/private-reviewer',
        }}
        onOpen={vi.fn()}
        onShare={vi.fn()}
      />,
    )

    expect(
      screen.queryByRole('button', {
        name: '将 private-reviewer 设为共享技能',
      }),
    ).toBeNull()
  })
})
