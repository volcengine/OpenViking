// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { ImportSkillDialog } from '#/features/workbench/skills/components/import-skill-dialog'
import i18n from '#/i18n'

const { addNewSkill, listSkillSources, validateNewSkill, fetchSkillTask } =
  vi.hoisted(() => ({
    addNewSkill: vi.fn(),
    listSkillSources: vi.fn(),
    validateNewSkill: vi.fn(),
    fetchSkillTask: vi.fn(),
  }))
vi.mock('#/features/workbench/skills/lib/add-skill', () => ({
  addNewSkill,
  listSkillSources,
  validateNewSkill,
  fetchSkillTask,
}))
const copy = i18n.getFixedT('en', 'skillsPage')

beforeEach(async () => {
  await i18n.changeLanguage('en')
})

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

function selectGithub(url = 'https://github.com/owner/repo') {
  fireEvent.click(screen.getByRole('tab', { name: copy('add.githubTab') }))
  fireEvent.change(
    screen.getByRole('textbox', { name: copy('add.githubTab') }),
    {
      target: { value: url },
    },
  )
}

function confirm() {
  fireEvent.click(screen.getByRole('button', { name: copy('add.submit') }))
}

async function selectFile(name: string, value: string) {
  const input = document.querySelector<HTMLInputElement>('input[type=file]')!
  const file = new File([value], name, { type: 'text/markdown' })
  file.text = vi.fn().mockResolvedValue(value)
  await act(async () => fireEvent.change(input, { target: { files: [file] } }))
  await screen.findByText(name)
}

it('clears the draft and resets to upload when reopened', () => {
  const props = { onOpenChange: vi.fn(), onAdded: vi.fn() }
  const { rerender } = render(<ImportSkillDialog {...props} open />)
  selectGithub()
  rerender(<ImportSkillDialog {...props} open={false} />)
  rerender(<ImportSkillDialog {...props} open />)
  expect(
    screen
      .getByRole('tab', { name: copy('add.uploadTab') })
      .getAttribute('aria-selected'),
  ).toBe('true')
  fireEvent.click(screen.getByRole('tab', { name: copy('add.githubTab') }))
  expect(
    screen.getByRole('textbox', { name: copy('add.githubTab') }),
  ).toHaveProperty('value', '')
})

it('replaces an uploaded file and submits the validated Markdown', async () => {
  addNewSkill.mockResolvedValue({})
  validateNewSkill.mockResolvedValue({ valid: true, errors: [], warnings: [] })
  render(<ImportSkillDialog open onOpenChange={vi.fn()} onAdded={vi.fn()} />)
  await selectFile('first.md', 'first')
  fireEvent.click(screen.getByRole('button', { name: 'Remove file first.md' }))
  expect(screen.queryByText('first.md')).toBeNull()
  await selectFile('second.md', 'second')
  confirm()
  await waitFor(() =>
    expect(addNewSkill).toHaveBeenCalledWith('second', 'user', undefined),
  )
  expect(validateNewSkill).toHaveBeenCalledWith('second')
})

it('retranslates known validation issues and keeps unknown diagnostics', async () => {
  validateNewSkill.mockResolvedValue({
    valid: false,
    errors: [
      { rule: 'name_required', message: 'name is required' },
      { rule: 'future_rule', message: 'Original diagnostic: {{field}}' },
    ],
    warnings: [
      {
        rule: 'name_max_length',
        message: 'name must not exceed 64 characters',
      },
    ],
  })
  render(<ImportSkillDialog open onOpenChange={vi.fn()} onAdded={vi.fn()} />)
  await selectFile('SKILL.md', '---\ndescription: Example\n---')
  confirm()
  await screen.findByText('Add a name to the YAML frontmatter.')
  expect(
    screen.getByText('The name must be 64 characters or fewer.'),
  ).toBeTruthy()

  await act(async () => {
    await i18n.changeLanguage('zh-CN')
  })
  expect(screen.getByText('请在 YAML 元数据中填写 name 字段。')).toBeTruthy()
  expect(screen.getByText('name 字段最多可包含 64 个字符。')).toBeTruthy()
  expect(screen.getByText('Original diagnostic: {{field}}')).toBeTruthy()
  expect(screen.queryByText('name is required')).toBeNull()
  expect(validateNewSkill).toHaveBeenCalledTimes(1)
  expect(addNewSkill).not.toHaveBeenCalled()
})

it('imports a single GitHub skill to the selected directory and tracks the task', async () => {
  const url = 'https://github.com/owner/repo/tree/main/skills/one'
  listSkillSources.mockResolvedValue([
    { name: 'One', description: '', path: 'one' },
  ])
  addNewSkill.mockResolvedValue({ task_id: 'git-task' })
  fetchSkillTask.mockResolvedValue({ status: 'completed' })
  render(<ImportSkillDialog open onOpenChange={vi.fn()} onAdded={vi.fn()} />)
  selectGithub(url)
  fireEvent.click(
    screen.getByRole('radio', { name: /viking:\/\/agent\/skills/ }),
  )
  confirm()
  await screen.findByText(copy('add.completed'))
  expect(listSkillSources).toHaveBeenCalledWith(url)
  expect(addNewSkill).toHaveBeenCalledWith(url, 'agent', 'one')
  expect(fetchSkillTask).toHaveBeenCalledWith('git-task')
  expect(validateNewSkill).not.toHaveBeenCalled()
})

it('requires an explicit selection when the repository contains multiple skills', async () => {
  const url = 'https://github.com/owner/repo'
  listSkillSources.mockResolvedValue([
    { name: 'One', description: 'First skill', path: 'one' },
    { name: 'Two', description: 'Second skill', path: 'two' },
  ])
  addNewSkill.mockResolvedValue({})
  render(<ImportSkillDialog open onOpenChange={vi.fn()} onAdded={vi.fn()} />)
  selectGithub(url)
  confirm()
  await screen.findByText(copy('add.selectSkill'))
  expect(addNewSkill).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('radio', { name: /Two/ }))
  confirm()
  await waitFor(() =>
    expect(addNewSkill).toHaveBeenCalledWith(url, 'user', 'two'),
  )
})
