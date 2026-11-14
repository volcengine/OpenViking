// @vitest-environment jsdom

import { ovClient } from '#/shared/api/ov-client/index'
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

const { validateNewSkill, addNewSkill } = vi.hoisted(() => ({
  validateNewSkill: vi.fn(),
  addNewSkill: vi.fn(),
}))
vi.mock('#/features/workbench/skills/lib/add-skill', () => ({
  validateNewSkill,
  addNewSkill,
  fetchSkillTask: vi.fn(),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

async function submitFile(content: string) {
  const file = new File([content], 'SKILL.md', { type: 'text/markdown' })
  file.text = vi.fn().mockResolvedValue(content)
  const input = document.querySelector<HTMLInputElement>('input[type=file]')!
  await act(async () => fireEvent.change(input, { target: { files: [file] } }))
  await waitFor(() => expect(screen.getByText('SKILL.md')).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: 'add.submit' }))
}

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
  const view = render(<ImportSkillDialog key="source" {...props} />)
  await submitFile('# Source skill')
  expect(validateNewSkill).toHaveBeenCalledWith('# Source skill')

  // Workspace switch remounts the dialog while the old validation is pending.
  view.rerender(<ImportSkillDialog key="target" {...props} />)
  await act(async () => {
    finishValidation({ valid: true, warnings: [], errors: [] })
  })
  expect(addNewSkill).not.toHaveBeenCalled()
  expect(onAdded).not.toHaveBeenCalled()
})

it('does not add a skill from a dialog closed during validation', async () => {
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
  const view = render(<ImportSkillDialog {...props} open />)
  await submitFile('# Draft')
  view.rerender(<ImportSkillDialog {...props} open={false} />)
  await act(async () => {
    finishValidation({ valid: true, warnings: [], errors: [] })
  })
  expect(addNewSkill).not.toHaveBeenCalled()
})

it('does not add a skill when the runtime connection changes before React unmounts the dialog', async () => {
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
    credentialScope: 'data',
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

  render(<ImportSkillDialog open onOpenChange={vi.fn()} onAdded={vi.fn()} />)
  await submitFile('# Source skill')

  // The client switches synchronously; React may commit the old dialog later.
  baseUrl = 'http://target.test'
  await act(async () => {
    finishValidation({ valid: true, warnings: [], errors: [] })
  })
  expect(addNewSkill).not.toHaveBeenCalled()
})
