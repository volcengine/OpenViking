// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RequestLogRow } from './row'
import type { ConsoleAuditLogItem } from '@ov-server/api/v1/console'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(cleanup)

function renderRow(log: ConsoleAuditLogItem) {
  return render(
    <table>
      <tbody>
        <RequestLogRow log={log} />
      </tbody>
    </table>,
  )
}

describe('RequestLogRow', () => {
  it('expands structured error information from the existing audit row', () => {
    renderRow({
      api_type: 'tasks',
      error_code: 'INVALID_ARGUMENT',
      error_details: { limit: 300 },
      error_message: 'limit must be at most 200',
      method: 'GET',
      route: '/api/v1/tasks',
      status_code: 400,
    })

    expect(screen.queryByText('INVALID_ARGUMENT')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'details.show' }))

    expect(screen.getByText('INVALID_ARGUMENT')).toBeTruthy()
    expect(screen.getByText('limit must be at most 200')).toBeTruthy()
    expect(screen.getByText(/"limit": 300/)).toBeTruthy()
    expect(
      screen
        .getByRole('button', { name: 'details.hide' })
        .getAttribute('aria-expanded'),
    ).toBe('true')
  })

  it('also expands when the user clicks the error row', () => {
    renderRow({
      error_code: 'NOT_FOUND',
      error_message: 'Task was not found',
      method: 'GET',
      route: '/api/v1/tasks/{task_id}',
      status_code: 404,
    })

    const route = screen.getByText('/api/v1/tasks/{task_id}')
    fireEvent.click(route.closest('tr')!)

    expect(screen.getByText('NOT_FOUND')).toBeTruthy()
    expect(screen.getByText('Task was not found')).toBeTruthy()
  })

  it('omits the structured details section for an empty object', () => {
    renderRow({
      error_code: 'PERMISSION_DENIED',
      error_details: {},
      error_message: 'Permission denied',
      method: 'GET',
      route: '/api/v1/resources',
      status_code: 403,
    })

    fireEvent.click(screen.getByRole('button', { name: 'details.show' }))

    expect(screen.getByText('PERMISSION_DENIED')).toBeTruthy()
    expect(screen.getByText('Permission denied')).toBeTruthy()
    expect(screen.queryByText('details.data')).toBeNull()
  })

  it('does not add an expansion control to rows without captured errors', () => {
    renderRow({ method: 'GET', route: '/api/v1/tasks', status_code: 200 })

    expect(screen.queryByRole('button')).toBeNull()
  })
})
