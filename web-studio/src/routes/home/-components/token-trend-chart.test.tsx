// @vitest-environment jsdom

import type { PropsWithChildren } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { HomeT } from '../-types/dashboard'
import { TokenTrendChart } from './token-trend-chart'

vi.mock('recharts', () => ({
  Area: () => null,
  AreaChart: ({ children }: PropsWithChildren) => <svg>{children}</svg>,
  CartesianGrid: () => null,
  ResponsiveContainer: ({ children }: PropsWithChildren) => <>{children}</>,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: ({ width }: { width?: number | 'auto' }) => (
    <g data-testid="token-trend-y-axis" data-width={width} />
  ),
}))

afterEach(cleanup)

describe('TokenTrendChart', () => {
  it('lets the Y-axis fit million-scale labels without clipping', () => {
    render(
      <TokenTrendChart
        items={[
          {
            date: '2026-08-18',
            embedding_input: 1_411_688,
            total: 26_946_050,
            vlm_input: 21_445_905,
            vlm_output: 4_088_457,
          },
        ]}
        t={((key: string) => key) as HomeT}
      />,
    )

    expect(
      screen.getByTestId('token-trend-y-axis').getAttribute('data-width'),
    ).toBe('auto')
  })
})
