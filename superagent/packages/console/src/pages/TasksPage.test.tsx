import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import fixtures from '../../test/fixtures.json'
import { TasksPage } from './TasksPage'

const capabilities = { ...fixtures.capabilities, source: 'rfc' as const }
const task = (id: number) => ({ ...fixtures.tasks[0], id: `t${id}`, title: `Task ${id}`, createdAt: '2026-11-14T14:00:00Z', updatedAt: '2026-11-14T14:00:00Z' })

afterEach(() => vi.unstubAllGlobals())

describe('historical task pagination', () => {
  it('walks 60 tasks without duplicates and keeps the historical before cursor stable', async () => {
    const pages = [Array.from({ length: 20 }, (_, i) => task(60 - i)), Array.from({ length: 20 }, (_, i) => task(40 - i)), Array.from({ length: 20 }, (_, i) => task(20 - i))]
    const requested: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input))
      requested.push(url.searchParams.get('before') ?? 'latest')
      const before = url.searchParams.get('before')
      const index = before === null ? 0 : before === '41' ? 1 : 2
      return new Response(JSON.stringify({ items: pages[index], nextCursor: index < 2 ? String(41 - index * 20) : null }), { status: 200 })
    }))
    render(<TasksPage instance="primary" capabilities={capabilities} />)
    expect(await screen.findByText('Task 60')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Older/ }))
    expect(await screen.findByText('Task 40')).toBeInTheDocument()
    expect(screen.queryByText('Task 60')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Older/ }))
    expect(await screen.findByText('Task 20')).toBeInTheDocument()
    expect(screen.getByText('Task 1')).toBeInTheDocument()
    expect(requested).toEqual(['latest', '41', '21'])
    expect(screen.getByRole('button', { name: /Older/ })).toBeDisabled()
  })

  it('does not request tasks when the capability is unavailable', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const legacy = { ...capabilities, modules: { ...capabilities.modules, 'tasks.read': { enabled: true, available: false, effective: false, reasonCode: 'workers_not_merged', message: 'Workers backend required' } } }
    render(<TasksPage instance="legacy" capabilities={legacy} />)
    expect(await screen.findByText('Task observation is not available')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
