// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { AddSkillSheet } from '#/features/workbench/skills/components/add-skill-sheet'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(cleanup)

it('clears a previous draft whenever the drawer is reopened', () => {
  const props = { onOpenChange: vi.fn(), onAdded: vi.fn() }
  const { rerender } = render(<AddSkillSheet {...props} open />)
  fireEvent.change(screen.getByRole('textbox', { name: 'add.content' }), {
    target: { value: 'old skill' },
  })
  rerender(<AddSkillSheet {...props} open={false} />)
  rerender(<AddSkillSheet {...props} open />)
  expect(
    screen.getByRole('textbox', {
      name: 'add.content',
    }).value,
  ).toBe('')
})

it('removes an uploaded file and allows choosing another', async () => {
  render(<AddSkillSheet open onOpenChange={vi.fn()} onAdded={vi.fn()} />)
  const input = document.querySelector<HTMLInputElement>('input[type=file]')!
  const first = new File(['first'], 'first.md', { type: 'text/markdown' })
  first.text = vi.fn().mockResolvedValue('first')
  await act(async () => fireEvent.change(input, { target: { files: [first] } }))
  expect(
    screen.getByRole('textbox', {
      name: 'add.content',
    }).value,
  ).toBe('first')
  fireEvent.click(screen.getByRole('button', { name: /add.removeFile/ }))
  expect(
    screen.getByRole('textbox', {
      name: 'add.content',
    }).value,
  ).toBe('')
  const second = new File(['second'], 'second.md', { type: 'text/markdown' })
  second.text = vi.fn().mockResolvedValue('second')
  await act(async () =>
    fireEvent.change(input, { target: { files: [second] } }),
  )
  expect(
    screen.getByRole('textbox', {
      name: 'add.content',
    }).value,
  ).toBe('second')
})
