import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { MarkdownLink, resolveMarkdownLinkUrl } from './file-preview'

const fileUri = 'viking://workspace/notes/readme.md'

function renderedLink(href: string): string {
  return renderToStaticMarkup(
    <MarkdownLink fileUri={fileUri} href={href}>
      untrusted link
    </MarkdownLink>,
  )
}

describe('Markdown link URL policy', () => {
  it.each([
    'javascript:alert(1)',
    ' JAVASCRIPT:alert(1)',
    'java\nscript:alert(1)',
    'javascript%3Aalert(1)',
    'javascript%253Aalert(1)',
    'vbscript:msgbox(1)',
    'data:text/html,alert(1)',
    'blob:https://example.test/id',
    'file:///etc/passwd',
    'custom-scheme:payload',
    '//attacker.example/path',
  ])('omits href for unsafe or unknown target %j', (href) => {
    expect(resolveMarkdownLinkUrl(href, fileUri)).toBeNull()

    const html = renderedLink(href)
    expect(html).toBe('<a>untrusted link</a>')
  })

  it.each([
    'https://example.test/path',
    'HTTPS://example.test/path',
    'mailto:security@example.test',
    'tel:+4930123456',
  ])('keeps explicitly allowed external target %j clickable', (href) => {
    expect(resolveMarkdownLinkUrl(href, fileUri)).toBe(href)

    const html = renderedLink(href)
    expect(html).toContain(`href="${href}"`)
    expect(html).toContain('rel="noreferrer noopener"')
  })

  it('keeps fragments and resolves Viking and relative links through the download API', () => {
    expect(resolveMarkdownLinkUrl('#details', fileUri)).toBe('#details')

    for (const href of ['viking://workspace/guide.md', '../guide.md']) {
      const resolved = resolveMarkdownLinkUrl(href, fileUri)
      expect(resolved).toContain('/api/v1/content/download')
      expect(decodeURIComponent(resolved!)).toContain('viking://workspace')
      expect(renderedLink(href)).toContain('href=')
    }
  })
})
