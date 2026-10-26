// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import axios, { AxiosHeaders } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'

import { ovClient } from '#/lib/ov-client'
import { client as sdkClient } from '#/gen/ov-client/client.gen'
import {
  ResourceUploadProvider,
  useResourceUpload,
} from './use-resource-upload'

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}))

const SOURCE_URL = 'http://workspace-a.test'
const TARGET_URL = 'http://workspace-b.test'
const originalOptions = ovClient.getOptions()
const originalConnection = ovClient.getConnection()
const originalGlobalAdapter = axios.defaults.adapter
const originalClientAdapter = ovClient.instance.defaults.adapter
const originalSdkAdapter = sdkClient.getConfig().adapter

type RecordedRequest = {
  baseUrl: string | undefined
  credential: string | undefined
  fileName: string | null
  path: string | undefined
  signal: { readonly aborted: boolean } | undefined
}

function selectWorkspace(baseUrl: string, userId: string) {
  ovClient.setOptions({ baseUrl, apiKeyAuth: 'header' })
  ovClient.setConnection({
    accountId: 'team',
    adminApiKey: '',
    apiKey: `fixture-${userId}-key`,
    identityHeaders: true,
    userId,
  })
}

function response(config: InternalAxiosRequestConfig, result: unknown) {
  return {
    config,
    data: { status: 'ok', result },
    headers: {},
    status: 200,
    statusText: 'OK',
  }
}

function recordUploadsWithFirstRequestPaused() {
  const requests: RecordedRequest[] = []
  let finishFirstUpload!: () => void
  const firstUploadPending = new Promise<void>((resolve) => {
    finishFirstUpload = resolve
  })
  const adapter: AxiosAdapter = async (config) => {
    const file =
      config.data instanceof FormData ? config.data.get('file') : null
    const url = new URL(config.url!, config.baseURL || SOURCE_URL)
    requests.push({
      baseUrl: url.origin,
      credential: String(AxiosHeaders.from(config.headers).get('X-API-Key')),
      fileName: file instanceof File ? file.name : null,
      path: url.pathname,
      signal: config.signal,
    })
    if (url.pathname === '/api/v1/resources/temp_upload') {
      if (file instanceof File && file.name === 'first.txt') {
        // Ignore cancellation deliberately: a response may finish while a
        // connection changes, so the continuation must also check ownership.
        await firstUploadPending
      }
      return response(config, {
        temp_file_id: `temp-${file instanceof File ? file.name : 'file'}`,
      })
    }
    if (url.pathname === '/api/v1/resources') {
      return response(config, {
        status: 'success',
        root_uri: 'viking://resources/files',
      })
    }
    return response(config, [])
  }
  axios.defaults.adapter = adapter
  ovClient.instance.defaults.adapter = adapter
  sdkClient.setConfig({ adapter })
  return { requests, finishFirstUpload }
}

beforeEach(() => {
  selectWorkspace(SOURCE_URL, 'alice')
})

afterEach(() => {
  cleanup()
  axios.defaults.adapter = originalGlobalAdapter
  ovClient.instance.defaults.adapter = originalClientAdapter
  sdkClient.setConfig({ adapter: originalSdkAdapter })
  ovClient.setOptions(originalOptions)
  ovClient.setConnection(originalConnection)
  vi.clearAllMocks()
})

describe('ResourceUploadProvider connection ownership', () => {
  it('loads server tasks after StrictMode replays the initial mount', async () => {
    const { requests } = recordUploadsWithFirstRequestPaused()
    renderHook(() => useResourceUpload(), {
      reactStrictMode: true,
      wrapper: ResourceUploadProvider,
    })

    await waitFor(() =>
      expect(requests.some((request) => request.path === '/api/v1/tasks')).toBe(
        true,
      ),
    )
  })

  it('stops the remaining local files when its workspace unmounts during temp upload', async () => {
    const { requests, finishFirstUpload } =
      recordUploadsWithFirstRequestPaused()
    const view = renderHook(() => useResourceUpload(), {
      wrapper: ResourceUploadProvider,
    })
    act(() => {
      view.result.current.enqueueUploads({
        commonBody: {},
        files: [
          {
            file: new File(['private A first file'], 'first.txt'),
            fileType: null,
          },
          {
            file: new File(['private A second file'], 'second.txt'),
            fileType: null,
          },
        ],
      })
    })
    await waitFor(() =>
      expect(requests.filter((request) => request.fileName)).toHaveLength(1),
    )
    expect(
      requests.find((request) => request.fileName === 'first.txt'),
    ).toMatchObject({
      baseUrl: SOURCE_URL,
      credential: 'fixture-alice-key',
    })

    view.unmount()
    expect(
      requests.find((request) => request.fileName === 'first.txt')?.signal
        ?.aborted,
    ).toBe(true)
    selectWorkspace(TARGET_URL, 'bob')
    await act(async () => {
      finishFirstUpload()
    })

    expect(
      requests.filter(
        (request) => request.baseUrl === TARGET_URL && request.fileName,
      ),
    ).toEqual([])
    expect(requests.filter((request) => request.fileName)).toHaveLength(1)
    expect(
      requests.filter((request) => request.baseUrl === TARGET_URL),
    ).toEqual([])
    expect(
      requests.filter((request) => request.path === '/api/v1/resources'),
    ).toEqual([])
  })

  it.each([
    ['service URL', () => selectWorkspace(TARGET_URL, 'bob')],
    [
      'API key on the same service',
      () => ovClient.setConnection({ apiKey: 'fixture-rotated-key' }),
    ],
  ] as const)(
    'does not import a temporary file after the %s changes before unmount',
    async (_label, changeConnection) => {
      const { requests, finishFirstUpload } =
        recordUploadsWithFirstRequestPaused()
      const view = renderHook(() => useResourceUpload(), {
        wrapper: ResourceUploadProvider,
      })
      act(() => {
        view.result.current.enqueueUploads({
          commonBody: {},
          files: [
            {
              file: new File(['private A first file'], 'first.txt'),
              fileType: null,
            },
          ],
        })
      })
      await waitFor(() =>
        expect(
          requests.some((request) => request.fileName === 'first.txt'),
        ).toBe(true),
      )

      changeConnection()
      await act(async () => {
        finishFirstUpload()
      })

      expect(
        requests.filter((request) => request.path === '/api/v1/resources'),
      ).toEqual([])
      expect(
        requests.filter((request) => request.baseUrl === TARGET_URL),
      ).toEqual([])
      expect(
        requests.find((request) => request.fileName === 'first.txt')?.signal
          ?.aborted,
      ).toBe(true)
    },
  )

  it('uploads and imports both files in order with the original workspace credentials', async () => {
    const { requests, finishFirstUpload } =
      recordUploadsWithFirstRequestPaused()
    const view = renderHook(() => useResourceUpload(), {
      wrapper: ResourceUploadProvider,
    })
    act(() => {
      view.result.current.enqueueUploads({
        commonBody: {},
        files: [
          {
            file: new File(['first file'], 'first.txt'),
            fileType: null,
          },
          {
            file: new File(['second file'], 'second.txt'),
            fileType: null,
          },
        ],
      })
    })
    await waitFor(() =>
      expect(requests.filter((request) => request.fileName)).toHaveLength(1),
    )
    expect(
      requests.filter((request) => request.path === '/api/v1/resources'),
    ).toEqual([])
    await act(async () => {
      finishFirstUpload()
    })
    await waitFor(() =>
      expect(view.result.current.tasks.map((task) => task.status)).toEqual([
        'success',
        'success',
      ]),
    )

    const writes = requests.filter((request) =>
      request.path?.startsWith('/api/v1/resources'),
    )
    expect(writes.map(({ fileName, path }) => ({ fileName, path }))).toEqual([
      { fileName: 'first.txt', path: '/api/v1/resources/temp_upload' },
      { fileName: null, path: '/api/v1/resources' },
      { fileName: 'second.txt', path: '/api/v1/resources/temp_upload' },
      { fileName: null, path: '/api/v1/resources' },
    ])
    for (const request of writes) {
      expect(request).toMatchObject({
        baseUrl: SOURCE_URL,
        credential: 'fixture-alice-key',
      })
      expect(request.signal?.aborted).toBe(false)
    }
  })
})
