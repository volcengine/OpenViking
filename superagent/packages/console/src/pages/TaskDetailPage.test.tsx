import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { installFixtureFetch } from '../test/fixture-fetch'
import { TaskDetailPage } from './TaskDetailPage'

afterEach(() => vi.unstubAllGlobals())

it('separates Worker report, Router relay, and final IM delivery', async () => {
  vi.stubGlobal('fetch', installFixtureFetch())
  render(<TaskDetailPage instance="primary" taskId="t201" />)
  expect(await screen.findByRole('heading', { name: 'Design the console read API' })).toBeInTheDocument()
  expect(screen.getByText('Worker reported')).toBeInTheDocument()
  expect(screen.getByText('Router inbox')).toBeInTheDocument()
  expect(screen.getByText('Final IM delivery')).toBeInTheDocument()
  expect(screen.getByText('inbox_928')).toBeInTheDocument()
  expect(screen.getByText('out_1821')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument()
})

it('explains uncertain final delivery without claiming the report failed', async () => {
  vi.stubGlobal('fetch', installFixtureFetch())
  render(<TaskDetailPage instance="primary" taskId="t201" />)
  await userEvent.click(await screen.findByRole('tab', { name: /Reports/ }))
  expect(await screen.findByText('Final delivery is uncertain')).toBeInTheDocument()
  expect(screen.getByText(/Check the IM manually before retrying/)).toBeInTheDocument()
  expect(screen.getByText('inbox_913')).toBeInTheDocument()
})
