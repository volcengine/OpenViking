// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { FilterSelect } from './filter-select'

afterEach(cleanup)
it('supports keyboard selection and Escape without changing the value', async () => {
  const changed = vi.fn()
  const user = userEvent.setup()
  function Form() {
    const [value, setValue] = useState('first')
    return (
      <FilterSelect
        label="Mode"
        value={value}
        onValueChange={(next) => {
          changed(next)
          setValue(next)
        }}
        options={[
          { value: 'first', label: 'First' },
          { value: 'second', label: 'Second' },
        ]}
      />
    )
  }
  render(<Form />)
  await user.click(screen.getByRole('combobox', { name: 'Mode' }))
  await user.keyboard('{ArrowDown}{Enter}')
  expect(changed).toHaveBeenCalledWith('second')
  changed.mockClear()
  await user.click(screen.getByRole('combobox', { name: 'Mode' }))
  await user.keyboard('{Escape}')
  expect(changed).not.toHaveBeenCalled()
  await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
})

it('selects string values including false and keeps disabled options unavailable', async () => {
  const changed = vi.fn()
  function Form() {
    const [value, setValue] = useState('true')
    return (
      <FilterSelect
        label="Mode"
        value={value}
        onValueChange={(next) => {
          changed(next)
          setValue(next)
        }}
        options={[
          { value: 'true', label: 'Enabled' },
          { value: 'false', label: 'Disabled' },
          { value: 'unavailable', label: 'Unavailable', disabled: true },
        ]}
      />
    )
  }
  const user = userEvent.setup()
  render(<Form />)
  await user.click(screen.getByRole('combobox', { name: 'Mode' }))
  expect(
    (await screen.findByRole('option', { name: 'Unavailable' })).getAttribute(
      'aria-disabled',
    ),
  ).toBe('true')
  await user.click(screen.getByRole('option', { name: 'Disabled' }))
  expect(changed).toHaveBeenCalledWith('false')
  expect(screen.getByRole('combobox', { name: 'Mode' }).textContent).toContain(
    'Disabled',
  )
})
