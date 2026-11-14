// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { UserApiKeyCell } from './user-api-key-cell'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(cleanup)
it('masks the key until revealed and copies the complete key', () => {
  const onCopy = vi.fn()
  render(
    <UserApiKeyCell
      user={{
        accountId: 'space',
        userId: 'member',
        role: 'user',
        apiKey: 'sk-example-secret-key',
      }}
      onCopy={onCopy}
    />,
  )
  expect(screen.queryByText('sk-example-secret-key')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'apiKey.show' }))
  expect(screen.getByText('sk-example-secret-key')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'apiKey.copy' }))
  expect(onCopy).toHaveBeenCalledWith('sk-example-secret-key')
  fireEvent.click(screen.getByRole('button', { name: 'apiKey.hide' }))
  expect(screen.queryByText('sk-example-secret-key')).toBeNull()
})
it('shows only the known prefix when the service does not return a key', () => {
  render(
    <UserApiKeyCell
      user={{
        accountId: 'space',
        userId: 'member',
        role: 'user',
        keyPrefix: 'sk-prefix',
      }}
      onCopy={vi.fn()}
    />,
  )
  expect(screen.getByText('sk-prefix********')).toBeTruthy()
  expect(screen.queryByRole('button')).toBeNull()
})
