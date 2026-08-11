import { describe, expect, it } from 'vitest'

import {
  detectRemoteResourceKind,
  matchesRemoteResourceTypeSelection,
} from './resource-source'

describe('detectRemoteResourceKind', () => {
  it.each([
    ['https://example.feishu.cn/docx/doxcn123', 'feishu'],
    ['https://open.larksuite.com/wiki/wikcn123', 'feishu'],
    ['https://github.com/volcengine/OpenViking', 'git'],
    ['git@github.com:volcengine/OpenViking.git', 'git'],
    ['https://gitlab.com/group/repo.git', 'git'],
    ['https://example.com/sitemap.xml', 'webFeed'],
    ['https://example.com/feed.atom', 'webFeed'],
    ['https://example.com/guide.pdf', 'remoteFile'],
    ['https://example.com/docs/getting-started', 'webPage'],
    ['tos://bucket/docs', 'tos'],
    ['custom://workspace/source', 'unknown'],
    ['', 'unknown'],
  ] as const)('detects %s as %s', (source, expected) => {
    expect(detectRemoteResourceKind(source)).toBe(expected)
  })

  it('does not classify a GitHub blob URL as a repository', () => {
    expect(
      detectRemoteResourceKind(
        'https://github.com/volcengine/OpenViking/blob/main/README.md',
      ),
    ).toBe('remoteFile')
  })
})

describe('matchesRemoteResourceTypeSelection', () => {
  it('matches native server routing categories', () => {
    expect(matchesRemoteResourceTypeSelection('feishu', 'feishu')).toBe(true)
    expect(matchesRemoteResourceTypeSelection('git', 'feishu')).toBe(false)
    expect(matchesRemoteResourceTypeSelection('webFeed', 'webPage')).toBe(true)
    expect(matchesRemoteResourceTypeSelection('webPage', 'remoteFile')).toBe(
      true,
    )
  })
})
