// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ovClient } from '#/lib/ov-client'

import { AddSkillSheet } from './add-skill-sheet'

const { validateNewSkill, addNewSkill } = vi.hoisted(() => ({
  validateNewSkill: vi.fn(),
  addNewSkill: vi.fn(),
}))
vi.mock('../-lib/add-skill', () => ({
  validateNewSkill,
  addNewSkill,
  fetchSkillTask: vi.fn(),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(() => {
  cleanup()
  validateNewSkill.mockReset()
  addNewSkill.mockReset()
  vi.restoreAllMocks()
})

it('does not add a skill after validation resolves for an unmounted workspace', async () => {
  let finishValidation!: (result: {
    valid: boolean
    warnings: []
    errors: []
  }) => void
  validateNewSkill.mockReturnValue(
    new Promise((resolve) => {
      finishValidation = resolve
    }),
  )
  const onAdded = vi.fn()
  const props = { open: true, onOpenChange: vi.fn(), onAdded }
  const view = render(<AddSkillSheet key="source" {...props} />)
  const content = screen.getByRole('textbox', { name: 'add.content' })
  fireEvent.change(content, { target: { value: '# Source skill' } })
  fireEvent.submit(content.closest('form')!)
  expect(validateNewSkill).toHaveBeenCalledWith('# Source skill')

  // Workspace switch remounts the sheet while the old validation is pending.
  view.rerender(<AddSkillSheet key="target" {...props} />)
  await act(async () => {
    finishValidation({ valid: true, warnings: [], errors: [] })
  })
  expect(addNewSkill).not.toHaveBeenCalled()
  expect(onAdded).not.toHaveBeenCalled()
})

it('does not add a skill from a drawer closed during validation', async () => {
  let finishValidation!: (result: {
    valid: boolean
    warnings: []
    errors: []
  }) => void
  validateNewSkill.mockReturnValue(
    new Promise((resolve) => {
      finishValidation = resolve
    }),
  )
  const props = { onOpenChange: vi.fn(), onAdded: vi.fn() }
  const view = render(<AddSkillSheet {...props} open />)
  const content = screen.getByRole('textbox', { name: 'add.content' })
  fireEvent.change(content, { target: { value: '# Draft' } })
  fireEvent.submit(content.closest('form')!)
  view.rerender(<AddSkillSheet {...props} open={false} />)
  await act(async () => {
    finishValidation({ valid: true, warnings: [], errors: [] })
  })
  expect(addNewSkill).not.toHaveBeenCalled()
})

it('does not add a skill when the runtime connection changes before React unmounts the drawer', async () => {
  let finishValidation!: (result: {
    valid: boolean
    warnings: []
    errors: []
  }) => void
  validateNewSkill.mockReturnValue(
    new Promise((resolve) => {
      finishValidation = resolve
    }),
  )
  let baseUrl = 'http://source.test'
  vi.spyOn(ovClient, 'getOptions').mockImplementation(() => ({
    apiKeyAuth: 'header',
    apiKeyStorageKey: 'test',
    baseUrl,
    defaultTelemetry: false,
  }))
  vi.spyOn(ovClient, 'getConnection').mockReturnValue({
    accountId: 'default',
    adminApiKey: '',
    apiKey: 'source-key',
    identityHeaders: true,
    userId: 'alice',
  })

  render(<AddSkillSheet open onOpenChange={vi.fn()} onAdded={vi.fn()} />)
  const content = screen.getByRole('textbox', { name: 'add.content' })
  fireEvent.change(content, { target: { value: '# Source skill' } })
  fireEvent.submit(content.closest('form')!)

  // The client switches synchronously; React may commit the old sheet later.
  baseUrl = 'http://target.test'
  await act(async () => {
    finishValidation({ valid: true, warnings: [], errors: [] })
  })
  expect(addNewSkill).not.toHaveBeenCalled()
})
