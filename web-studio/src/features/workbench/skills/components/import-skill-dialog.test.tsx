// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { ImportSkillDialog } from '#/features/workbench/skills/components/import-skill-dialog'

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
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

function selectGithub(url = 'https://github.com/owner/repo') {
  fireEvent.click(screen.getByRole('tab', { name: 'add.githubTab' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'add.githubTab' }), {
    target: { value: url },
  })
}

function confirm() {
  fireEvent.click(screen.getByRole('button', { name: 'add.submit' }))
}

it('clears the draft and resets to upload when reopened', () => {
  const props = { onOpenChange: vi.fn(), onAdded: vi.fn() }
  const { rerender } = render(<ImportSkillDialog {...props} open />)
  selectGithub()
  rerender(<ImportSkillDialog {...props} open={false} />)
  rerender(<ImportSkillDialog {...props} open />)
  expect(
    screen
      .getByRole('tab', { name: 'add.uploadTab' })
      .getAttribute('aria-selected'),
  ).toBe('true')
  fireEvent.click(screen.getByRole('tab', { name: 'add.githubTab' }))
  expect(screen.getByRole('textbox', { name: 'add.githubTab' })).toHaveProperty(
    'value',
    '',
  )
})

it('replaces an uploaded file and submits the validated Markdown', async () => {
  addNewSkill.mockResolvedValue({})
  validateNewSkill.mockResolvedValue({ valid: true, errors: [], warnings: [] })
  render(<ImportSkillDialog open onOpenChange={vi.fn()} onAdded={vi.fn()} />)
  const selectFile = async (name: string, value: string) => {
    const input = document.querySelector<HTMLInputElement>('input[type=file]')!
    const file = new File([value], name, { type: 'text/markdown' })
    file.text = vi.fn().mockResolvedValue(value)
    await act(async () =>
      fireEvent.change(input, { target: { files: [file] } }),
    )
    await screen.findByText(name)
  }
  await selectFile('first.md', 'first')
  fireEvent.click(screen.getByRole('button', { name: 'add.removeFile' }))
  expect(screen.queryByText('first.md')).toBeNull()
  await selectFile('second.md', 'second')
  confirm()
  await waitFor(() =>
    expect(addNewSkill).toHaveBeenCalledWith('second', 'user', undefined),
  )
  expect(validateNewSkill).toHaveBeenCalledWith('second')
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
  await screen.findByText('add.completed')
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
  await screen.findByText('add.selectSkill')
  expect(addNewSkill).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('radio', { name: /Two/ }))
  confirm()
  await waitFor(() =>
    expect(addNewSkill).toHaveBeenCalledWith(url, 'user', 'two'),
  )
})
