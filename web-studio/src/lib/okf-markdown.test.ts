import { describe, expect, it } from 'vitest'

import { parseOkfSidecarMarkdown } from './okf-markdown'

const sample = `---
directory: viking://resources/openviking-contribute/
generated_by:
  component: SemanticProcessor
  trigger: parent_refresh
freshness:
  total_entries: 4
  sampled_entries: 4
  unsampled_entries: 0
  pending_child_changes: 0
extensions:
  ranking:
    strategy: semantic
---

这是 OpenViking 相关项目的 PR 贡献规范集合。`

describe('parseOkfSidecarMarkdown', () => {
  it.each(['.abstract.md', '.overview.md'])(
    'separates valid OKF metadata from the Markdown body for %s',
    (filename) => {
      expect(
        parseOkfSidecarMarkdown(
          `viking://resources/openviking-contribute/${filename}`,
          sample,
        ),
      ).toEqual({
        body: '这是 OpenViking 相关项目的 PR 贡献规范集合。',
        metadata: {
          directory: 'viking://resources/openviking-contribute/',
          freshness: {
            pending_child_changes: 0,
            sampled_entries: 4,
            total_entries: 4,
            unsampled_entries: 0,
          },
          generated_by: {
            component: 'SemanticProcessor',
            trigger: 'parent_refresh',
          },
        },
        rawFrontmatter: [
          '---',
          'directory: viking://resources/openviking-contribute/',
          'generated_by:',
          '  component: SemanticProcessor',
          '  trigger: parent_refresh',
          'freshness:',
          '  total_entries: 4',
          '  sampled_entries: 4',
          '  unsampled_entries: 0',
          '  pending_child_changes: 0',
          'extensions:',
          '  ranking:',
          '    strategy: semantic',
          '---',
        ].join('\n'),
      })
    },
  )

  it('does not reinterpret frontmatter in an ordinary Markdown file', () => {
    expect(
      parseOkfSidecarMarkdown(
        'viking://resources/openviking-contribute/README.md',
        sample,
      ),
    ).toBeNull()
  })

  it('falls back to the original Markdown path for malformed metadata', () => {
    expect(
      parseOkfSidecarMarkdown(
        'viking://resources/openviking-contribute/.abstract.md',
        sample.replace('sampled_entries: 4', 'sampled_entries: nope'),
      ),
    ).toBeNull()
  })
})
