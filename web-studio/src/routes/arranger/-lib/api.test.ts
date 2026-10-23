import { afterEach, describe, expect, it, vi } from 'vitest'

import i18n from '#/i18n/core'
import { OvClientError } from '#/lib/ov-client'
import type * as OvClientModule from '#/lib/ov-client'

import {
  isLiveContextDocument,
  moveLiveContext,
  parseLiveContextMetadata,
  parseMcpMounts,
  parseMcpServers,
  parseMcpTools,
  saveText,
} from './api'

const { post } = vi.hoisted(() => ({ post: vi.fn() }))

vi.mock('#/lib/ov-client', async (importOriginal) => {
  const original = await importOriginal<typeof OvClientModule>()
  return {
    ...original,
    getOvClient: () => ({
      getOptions: () => ({ baseUrl: 'http://localhost:1933' }),
      instance: { post },
    }),
  }
})

afterEach(() => {
  post.mockReset()
  vi.useRealTimers()
})

describe('text saving', () => {
  it('retries transient resource-busy conflicts', async () => {
    vi.useFakeTimers()
    post
      .mockRejectedValueOnce(
        new OvClientError({
          code: 'CONFLICT',
          details: { conflict_type: 'path_busy', retryable: true },
          message: 'resource is busy',
          statusCode: 409,
        }),
      )
      .mockResolvedValueOnce({})

    const saving = saveText(
      'viking://resources/arrange/note.md',
      'content',
      '0'.repeat(64),
    )
    await vi.runAllTimersAsync()

    await expect(saving).resolves.toBeUndefined()
    expect(post).toHaveBeenCalledTimes(2)
  })

  it('does not retry content revision conflicts', async () => {
    const conflict = new OvClientError({
      code: 'CONFLICT',
      details: { conflict_type: 'content_revision', retryable: false },
      message: 'content revision changed',
      statusCode: 409,
    })
    post.mockRejectedValueOnce(conflict)

    await expect(
      saveText('viking://resources/arrange/note.md', 'content', '0'.repeat(64)),
    ).rejects.toBe(conflict)
    expect(post).toHaveBeenCalledTimes(1)
  })
})

describe('live context moving', () => {
  it('does nothing when the normalized target is the source', async () => {
    await moveLiveContext(
      {
        arguments: {},
        mcp: 'k8s',
        node_uri: 'viking://resources/arranger/status.md',
        tool: 'status',
        tool_uri: 'viking://agent/tools/mcp/k8s/status.mcp',
      },
      'viking://resources/arranger/status.md/',
    )

    expect(post).not.toHaveBeenCalled()
  })
})

describe('MCP response parsing', () => {
  it('accepts result objects and direct arrays', () => {
    expect(parseMcpServers({ servers: [{ name: 'k8s' }] })).toEqual([
      { name: 'k8s' },
    ])
    expect(parseMcpServers([{ name: 'database' }])).toEqual([
      { name: 'database' },
    ])
    const mount = {
      arguments: { cluster: 'production' },
      description: 'Production cluster health',
      mcp: 'k8s',
      node_uri: 'viking://resources/arrange/status.md',
      tags: ['environment=production'],
      timeout_seconds: 120,
      title: 'Cluster health',
      tool: 'status',
      tool_uri: 'viking://agent/tools/k8s/status.json',
    }
    expect(parseMcpMounts({ mounts: [mount] })).toEqual([mount])
  })

  it('reports a connection error when Vite HTML is returned', () => {
    expect(() => parseMcpServers('<!doctype html><html></html>')).toThrowError(
      i18n.t('arranger:errors.studioHtmlResponse'),
    )
    expect(() => parseMcpServers(undefined)).toThrowError(
      i18n.t('arranger:errors.invalidApiResponse', {
        resource: i18n.t('arranger:apiResources.mcpServerList'),
      }),
    )
  })

  it('normalizes tool documents into selectable catalog entries', () => {
    expect(
      parseMcpTools('demo', {
        tools: [
          {
            input_revision: 'ir1',
            revision: 'r1',
            tool: {
              description: 'Add two numbers',
              inputSchema: {
                properties: {
                  a: { type: 'number' },
                  b: { type: 'number' },
                },
                required: ['a', 'b'],
                type: 'object',
              },
              name: 'get-sum',
              title: 'Get sum',
            },
          },
        ],
      }),
    ).toEqual([
      expect.objectContaining({
        input_revision: 'ir1',
        revision: 'r1',
        server: 'demo',
        uri: 'viking://agent/tools/demo/get-sum.json',
        tool: expect.objectContaining({
          name: 'get-sum',
          title: 'Get sum',
        }),
      }),
    ])
  })
})

describe('live context frontmatter detection', () => {
  it('parses mount fields without executing the document', () => {
    const content = `---
type: OpenViking Live Context
title: Cluster health
description: Current cluster state
x-openviking:
  type: mcp
  definition: viking://agent/tools/mcp/k8s/status.mcp
  timeout_seconds: 30
  args:
    cluster: production
---
`
    expect(isLiveContextDocument(content)).toBe(true)
    expect(parseLiveContextMetadata(content)).toMatchObject({
      arguments: { cluster: 'production' },
      description: 'Current cluster state',
      timeout_seconds: 30,
      title: 'Cluster health',
      tool_uri: 'viking://agent/tools/mcp/k8s/status.mcp',
    })
  })

  it('does not classify ordinary Markdown as live context', () => {
    expect(isLiveContextDocument('# Notes')).toBe(false)
    expect(
      isLiveContextDocument(`---
title: Notes
---
`),
    ).toBe(false)
  })

  it('fails closed for malformed live frontmatter', () => {
    expect(
      isLiveContextDocument(`---
type: OpenViking Live Context
broken: [
---
`),
    ).toBe(true)
  })
})
