import { describe, expect, it } from 'vitest'

import {
  formatBytes,
  formatClockTime,
  formatCompact,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatPercent,
  formatRelativeTime,
  hostFromUrl,
  humanizeSeconds,
  isLoopbackUrl,
  shortenId,
} from './format'

const now = Date.UTC(2026, 9, 5, 12, 0, 0)
const seconds = now / 1000

describe('time', () => {
  it.each([
    [seconds - 30, '30 seconds ago'],
    [seconds - 180, '3 minutes ago'],
    [seconds - 7200, '2 hours ago'],
    [seconds - 86400, 'yesterday'],
    [seconds + 20, 'in 20 seconds'],
  ])('relative %s → %s', (value, expected) => {
    expect(formatRelativeTime(value, 'en', now)).toBe(expected)
  })

  it('speaks the requested language', () => {
    expect(formatRelativeTime(seconds - 180, 'zh-CN', now)).toBe('3分钟前')
  })

  it('formats absolute and clock times', () => {
    expect(formatDateTime(seconds, 'en')).toContain('2026')
    expect(formatDateTime(seconds, 'en')).not.toMatch(/:\d{2}:\d{2}/)
    expect(formatDateTime(seconds, 'en', true)).toMatch(/:\d{2}:\d{2}/)
    expect(formatClockTime(now, 'en')).toMatch(/\d{2}:\d{2}:\d{2}/)
  })
})

describe('numbers', () => {
  it('formats counts, tokens and ratios', () => {
    expect(formatNumber(10000, 'en')).toBe('10,000')
    expect(formatCompact(1234, 'en')).toBe('1.2K')
    expect(formatCompact(980, 'en')).toBe('980')
    expect(formatPercent(0.873, 'en')).toBe('87%')
    expect(formatPercent(0.054, 'en')).toBe('5.4%')
    expect(formatPercent(0, 'en')).toBe('0%')
  })

  it('formats durations', () => {
    expect(formatDuration(820, 'en')).toBe('820 ms')
    expect(formatDuration(1400, 'en')).toBe('1.4 sec')
    expect(humanizeSeconds(45, 'en')).toBe('45 sec')
    expect(humanizeSeconds(600, 'en')).toBe('10 min')
    expect(humanizeSeconds(5400, 'en')).toBe('1.5 hr')
    expect(humanizeSeconds(600, 'zh-CN')).toBe('10分钟')
  })

  it('formats byte sizes', () => {
    expect(formatBytes(65536, 'en')).toBe('64 kB')
    expect(formatBytes(1048576, 'en')).toBe('1 MB')
  })
})

describe('addresses', () => {
  it('reads hosts', () => {
    expect(hostFromUrl('https://api.openai.com/v1')).toBe('api.openai.com')
    expect(hostFromUrl('http://10.0.0.5:8000')).toBe('10.0.0.5:8000')
    expect(hostFromUrl('not a url')).toBe('not a url')
  })

  it.each([
    ['http://127.0.0.1:1935', true],
    ['http://localhost:1935', true],
    ['http://[::1]:1935', true],
    ['http://0.0.0.0:1935', true],
    ['https://ov.example.com', false],
    ['http://10.0.0.5:1935', false],
    ['nonsense', false],
  ])('%s is loopback: %s', (url, expected) => {
    expect(isLoopbackUrl(url)).toBe(expected)
  })
})

it('shortens long ids', () => {
  expect(shortenId('0123456789abcdef')).toBe('01234567…')
  expect(shortenId('012345678')).toBe('012345678')
})
