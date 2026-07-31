import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchServerHealth } from './use-server-mode'

const { getHealthMock } = vi.hoisted(() => ({
  getHealthMock: vi.fn(),
}))

vi.mock('#/lib/ov-client', () => ({
  getHealth: getHealthMock,
}))

describe('fetchServerHealth', () => {
  beforeEach(() => {
    getHealthMock.mockReset()
    getHealthMock.mockResolvedValue({
      data: { auth_mode: 'api_key', role: 'user' },
    })
  })

  it('reuses an identical health probe during connection initialization', async () => {
    const headers = {
      'X-API-Key': 'user-key',
      'X-OpenViking-Account': 'default',
      'X-OpenViking-User': 'alice',
    }

    const first = fetchServerHealth('http://localhost:1933/', headers)
    const second = fetchServerHealth('http://localhost:1933', headers)

    await expect(Promise.all([first, second])).resolves.toEqual([
      { auth_mode: 'api_key', role: 'user' },
      { auth_mode: 'api_key', role: 'user' },
    ])
    expect(getHealthMock).toHaveBeenCalledTimes(1)
  })

  it('does not share health probes across credentials', async () => {
    await Promise.all([
      fetchServerHealth('http://localhost:1934', {
        'X-API-Key': 'first-key',
      }),
      fetchServerHealth('http://localhost:1934', {
        'X-API-Key': 'second-key',
      }),
    ])

    expect(getHealthMock).toHaveBeenCalledTimes(2)
  })
})
