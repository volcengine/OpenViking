type HeadEntry = [string, Record<string, string>]

const locales = { en: 'en-US', zh: 'zh-CN' } as const

/** Public URL of a page source path, e.g. `zh/guides/01-a.md` -> `${siteRoot}zh/guides/01-a`. */
export function pageUrl(siteRoot: string, relativePath: string): string {
  return siteRoot + relativePath.replace(/(^|\/)index\.md$/, '$1').replace(/\.md$/, '')
}

/** Canonical plus hreflang alternates for every language version that exists. */
export function seoLinks(siteRoot: string, relativePath: string, exists: (relativePath: string) => boolean): HeadEntry[] {
  const head: HeadEntry[] = [['link', { rel: 'canonical', href: pageUrl(siteRoot, relativePath) }]]
  const match = relativePath.match(/^(en|zh)\/(.*)$/)
  if (!match) return head

  const versions = Object.entries(locales)
    .map(([locale, lang]) => ({ path: `${locale}/${match[2]}`, lang }))
    .filter(version => exists(version.path))
  if (versions.length < 2) return head

  for (const { path, lang } of versions) {
    head.push(['link', { rel: 'alternate', hreflang: lang, href: pageUrl(siteRoot, path) }])
  }
  head.push(['link', { rel: 'alternate', hreflang: 'x-default', href: pageUrl(siteRoot, versions[0].path) }])
  return head
}

/** First prose paragraph after the title, as plain text, for pages without a description. */
export function inferDescription(markdown: string, maxLength = 160): string {
  const body = markdown
    .replace(/^---[\s\S]*?---\s*/, '')
    .replace(/```[\s\S]*?```/g, '\n\n')
    .replace(/<[^>]+>/g, '')
  const paragraph = body.split(/\n\s*\n/)
    .map(block => block.trim())
    .find(block => block && !/^(#|>|[-*+]\s|\d+\.\s|\||:::|!\[)/.test(block))
  if (!paragraph) return ''

  const text = paragraph
    .replace(/!\[[^\]]*\]\([^)]+\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/(\*\*|\*)([^*\n]+)\1/g, '$2')
    .replace(/\s+/g, ' ')
    .trim()
  return text.length <= maxLength ? text : text.slice(0, maxLength - 1).trimEnd() + '…'
}
