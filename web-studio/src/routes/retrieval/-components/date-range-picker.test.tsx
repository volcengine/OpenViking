// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { TFunction } from 'i18next'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import { DateRangePicker } from './date-range-picker'

const labels: Record<string, string> = {
  'advanced.anyTime': 'Any time',
  'advanced.clearTimeRange': 'Clear',
  'advanced.customRange': 'Custom dates',
  'advanced.endDate': 'End date',
  'advanced.last24Hours': 'Last 24 hours',
  'advanced.last30Days': 'Last 30 days',
  'advanced.last7Days': 'Last 7 days',
  'advanced.quickRange': 'Quick range',
  'advanced.startDate': 'Start date',
  'advanced.timeRange': 'Time range',
}

const t = ((key: string) => labels[key] ?? key) as TFunction<'retrieval'>

afterEach(cleanup)

function DateRangePickerHarness() {
  const [range, setRange] = useState<{
    since?: string
    until?: string
  }>({})

  return (
    <DateRangePicker
      onChange={setRange}
      since={range.since}
      t={t}
      until={range.until}
    />
  )
}

describe('DateRangePicker', () => {
  it('supports quick and custom date ranges', () => {
    render(<DateRangePickerHarness />)

    fireEvent.click(screen.getByRole('button', { name: 'Any time' }))
    fireEvent.click(screen.getByRole('button', { name: 'Last 7 days' }))
    expect(screen.getByRole('button', { name: 'Last 7 days' })).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: 'Last 7 days' }))
    fireEvent.change(screen.getByLabelText('Start date'), {
      target: { value: '2026-07-01' },
    })
    fireEvent.change(screen.getByLabelText('End date'), {
      target: { value: '2026-07-27' },
    })

    expect(
      screen.getByRole('button', {
        name: '2026-07-01 → 2026-07-27',
      }),
    ).toBeDefined()
  })
})
