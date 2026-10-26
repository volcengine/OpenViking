import { expect, it } from 'vitest'
import { splitMarkdownFrontmatter } from './markdown-frontmatter'

it('separates leading metadata without changing body separators', () => {
  expect(
    splitMarkdownFrontmatter('---\ntitle: Report\n---\n# Report\n---\nBody'),
  ).toEqual({
    rawFrontmatter: '---\ntitle: Report\n---',
    body: '# Report\n---\nBody',
  })
})
it('ignores ordinary separators and incomplete metadata', () => {
  expect(splitMarkdownFrontmatter('# Report\n---\nBody')).toBeNull()
  expect(splitMarkdownFrontmatter('---\nBody')).toBeNull()
})
it('preserves embedded fences and Windows newlines as plain metadata', () => {
  expect(
    splitMarkdownFrontmatter('\uFEFF---\r\nvalue: ```\r\n...\r\n# Body'),
  ).toEqual({ rawFrontmatter: '---\r\nvalue: ```\r\n...', body: '# Body' })
})
