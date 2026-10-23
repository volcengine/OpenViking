import { describe, expect, it } from 'vitest'

import {
  assertMutationBoundary,
  isWithinRoot,
  joinVikingUri,
  mcpServerNameFromToolUri,
  normalizeVikingUri,
  parentVikingUri,
  resolveMountNodeUri,
  toggleExpandedUri,
} from './uri'

describe('arranger Viking URI boundaries', () => {
  it('normalizes trailing separators and rejects relative segments', () => {
    expect(normalizeVikingUri(' viking://resources/team/ ')).toBe(
      'viking://resources/team',
    )
    expect(() => normalizeVikingUri('viking://resources/../secret')).toThrow()
    expect(
      parentVikingUri('viking://resources/arrange/knowledge_base_server/'),
    ).toBe('viking://resources/arrange')
    expect(
      parentVikingUri(
        'viking://resources/arrange/knowledge_base_server/error.log.md',
      ),
    ).toBe('viking://resources/arrange/knowledge_base_server')
  })

  it('compares path segments instead of string prefixes', () => {
    expect(
      isWithinRoot(
        'viking://resources/arrange/devops',
        'viking://resources/arrange',
      ),
    ).toBe(true)
    expect(
      isWithinRoot('viking://resources/arranger', 'viking://resources/arrange'),
    ).toBe(false)
  })

  it('collapses a directory and all expanded descendants', () => {
    const expanded = new Set([
      'viking://resources/arrange',
      'viking://resources/arrange/mysql',
      'viking://resources/arrange/mysql/metrics/',
      'viking://resources/arrange/mysql-other',
    ])

    expect([
      ...toggleExpandedUri(expanded, 'viking://resources/arrange/mysql/'),
    ]).toEqual([
      'viking://resources/arrange',
      'viking://resources/arrange/mysql-other',
    ])
    expect([
      ...toggleExpandedUri(
        new Set(['viking://resources/arrange']),
        'viking://resources/arrange/mysql',
      ),
    ]).toEqual([
      'viking://resources/arrange',
      'viking://resources/arrange/mysql',
    ])
  })

  it('keeps targets in the root and out of the source subtree', () => {
    expect(() =>
      assertMutationBoundary(
        'viking://resources/arrange/a',
        'viking://resources/arrange/a/child',
        'viking://resources/arrange',
      ),
    ).toThrow()
    expect(() =>
      assertMutationBoundary(
        'viking://resources/arrange/a',
        'viking://resources/other/a',
        'viking://resources/arrange',
      ),
    ).toThrow()
    expect(joinVikingUri('viking://resources/arrange', 'notes.md')).toBe(
      'viking://resources/arrange/notes.md',
    )
    expect(joinVikingUri('viking://resources/arrange/', 'notes.md')).toBe(
      'viking://resources/arrange/notes.md',
    )
  })

  it('derives the MCP server from a Tool URI', () => {
    expect(
      mcpServerNameFromToolUri(
        'viking://agent/tools/mcp_service_volcengine_tls/search_logs_v2_tool.json',
      ),
    ).toBe('mcp_service_volcengine_tls')
    expect(
      mcpServerNameFromToolUri('viking://resources/arrange/status.md'),
    ).toBeNull()
    expect(mcpServerNameFromToolUri('not-a-viking-uri')).toBeNull()
  })

  it('keeps an edited mount at its existing URI', () => {
    expect(
      resolveMountNodeUri(
        'viking://resources/arrange',
        'error.log.md',
        'viking://resources/arrange/knowledge_base_server/error.log.md',
      ),
    ).toBe('viking://resources/arrange/knowledge_base_server/error.log.md')
    expect(
      resolveMountNodeUri(
        'viking://resources/arrange/knowledge_base_server/',
        'new.log.md',
      ),
    ).toBe('viking://resources/arrange/knowledge_base_server/new.log.md')
  })
})
