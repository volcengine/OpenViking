// @vitest-environment jsdom
import { SkillPicker } from '#/features/workbench/compile/components/skill-picker'
import zh from '#/i18n/locales/zh-CN/compile'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type * as ReactI18next from 'react-i18next'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof ReactI18next>()),
  useTranslation: () => ({
    t: (key: string) =>
      key.split('.').reduce((value: any, part) => value?.[part], zh) ?? key,
  }),
}))

const skills = [
  {
    uri: 'viking://system/compile/example-skills/llm-wiki',
    name: 'llm-wiki',
    description: '',
  },
  {
    uri: 'viking://system/compile/example-skills/ov-session-report',
    name: 'ov-session-report',
    description: '',
  },
  {
    uri: 'viking://user/alice/skills/custom',
    name: 'Custom',
    description: 'My custom skill',
  },
]

afterEach(cleanup)

it('previews requirements using the keyboard without selecting until Enter', async () => {
  const onChange = vi.fn()
  const user = userEvent.setup()
  render(<SkillPicker skills={skills} value="" onChange={onChange} />)
  await user.tab()
  await user.keyboard('{ArrowDown}')
  const search = await screen.findByRole('combobox', { name: '搜索 Skill' })
  await user.type(search, '会话')
  await user.keyboard('{ArrowDown}')
  const details = within(screen.getByRole('region', { name: '能力说明' }))
  expect(details.getByText(/sessions_root/)).toBeTruthy()
  expect(details.getByText(/默认输出英文/)).toBeTruthy()
  expect(onChange).not.toHaveBeenCalled()
  expect(screen.getByRole('option').textContent).not.toContain('sessions_root')
  await user.keyboard('{Enter}')
  expect(onChange).toHaveBeenCalledWith(skills[1].uri)
})

it('does not show stale skill details when search has no matches', async () => {
  const user = userEvent.setup()
  render(
    <SkillPicker skills={skills} value={skills[0].uri} onChange={vi.fn()} />,
  )
  await user.tab()
  await user.keyboard('{ArrowDown}')
  await user.type(
    await screen.findByRole('combobox', { name: '搜索 Skill' }),
    'no-such-skill',
  )
  expect(screen.getByText(zh.noMatchingSkills)).toBeTruthy()
  expect(screen.queryByRole('region', { name: '能力说明' })).toBeNull()
})
