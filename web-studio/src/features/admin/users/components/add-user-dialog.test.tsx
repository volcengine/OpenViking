// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import i18n from '#/i18n'
import { AddUserDialog } from './add-user-dialog'

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('en')
})

it('translates both roles and responds to UI language changes without changing API values', async () => {
  await i18n.changeLanguage('zh-CN')
  const create = vi.fn()
  render(
    <AddUserDialog
      accountId="team"
      isPending={false}
      onCreate={create}
      onOpenChange={vi.fn()}
      open
    />,
  )
  const role = screen.getByRole('combobox')
  expect(role.querySelector('[data-slot="select-value"]')?.textContent).toBe(
    i18n.t('settings:roles.user'),
  )
  await userEvent.click(role)
  await userEvent.click(
    screen.getByRole('option', { name: i18n.t('settings:roles.admin') }),
  )
  expect(role.querySelector('[data-slot="select-value"]')?.textContent).toBe(
    i18n.t('settings:roles.admin'),
  )
  await act(() => i18n.changeLanguage('en'))
  expect(role.querySelector('[data-slot="select-value"]')?.textContent).toBe(
    i18n.t('settings:roles.admin'),
  )
})
