export type SearchLocale = 'en' | 'zh'
export type SearchMode = 'semantic' | 'keyword' | 'file'

export type DocsIndexRecord = {
  locale: SearchLocale
  path: string
  text: string
  title: string
  url: string
}

export type DocsSearchResult = {
  line?: number | null
  mode?: SearchMode
  relativePath?: string
  score?: number | null
  snippet?: string
  title?: string
  uri?: string
  url?: string
}

type LocalIndexLoader = () => Promise<DocsIndexRecord[]>

export async function localizeSearchResultTitles(
  results: DocsSearchResult[],
  locale: SearchLocale,
  loadLocalIndex: LocalIndexLoader
) {
  try {
    const localIndex = await loadLocalIndex()
    return enrichSearchResultsWithLocalTitles(results, localIndex, locale)
  } catch {
    return results
  }
}

export function enrichSearchResultsWithLocalTitles(
  results: DocsSearchResult[],
  localIndex: DocsIndexRecord[],
  locale: SearchLocale
) {
  const titleByKey = buildLocalTitleLookup(localIndex, locale)

  return results.map((result) => {
    const title = localizedTitleForResult(result, titleByKey, locale)
    return title && title !== result.title ? { ...result, title } : result
  })
}

function buildLocalTitleLookup(localIndex: DocsIndexRecord[], locale: SearchLocale) {
  const titleByKey = new Map<string, string>()

  for (const record of localIndex) {
    if (record.locale !== locale || !record.title) continue

    for (const key of localRecordKeys(record, locale)) {
      titleByKey.set(key, record.title)
    }
  }

  return titleByKey
}

function localizedTitleForResult(
  result: DocsSearchResult,
  titleByKey: Map<string, string>,
  locale: SearchLocale
) {
  for (const key of resultKeys(result, locale)) {
    const title = titleByKey.get(key)
    if (title) return title
  }

  return result.title
}

function localRecordKeys(record: DocsIndexRecord, locale: SearchLocale) {
  return [
    normalizeDocsUrl(record.url),
    normalizeLocalizedPath(record.path, locale),
    normalizeDocsUrl(urlFromRelativePath(record.path, locale))
  ].filter(Boolean)
}

function resultKeys(result: DocsSearchResult, locale: SearchLocale) {
  return [
    normalizeDocsUrl(result.url),
    normalizeLocalizedPath(result.relativePath, locale),
    normalizeDocsUrl(urlFromRelativePath(result.relativePath, locale)),
    normalizeDocsUrl(urlFromVikingUri(result.uri))
  ].filter(Boolean)
}

function normalizeDocsUrl(value: string | undefined) {
  if (!value) return ''

  const pathname = value.replace(/^https?:\/\/[^/]+/, '').split(/[?#]/, 1)[0]
  return `/${pathname.replace(/^\/+/, '')}`.replace(/\/index$/, '')
}

function normalizeLocalizedPath(value: string | undefined, locale: SearchLocale) {
  if (!value) return ''

  return value
    .replace(/^\/+/, '')
    .replace(/^docs\//, '')
    .replace(new RegExp(`^${locale}/`), '')
    .replace(/(?:\/index)?\.mdx?$/, '')
}

function urlFromRelativePath(value: string | undefined, locale: SearchLocale) {
  const normalizedPath = normalizeLocalizedPath(value, locale)
  return normalizedPath ? `/${locale}/${normalizedPath}` : ''
}

function urlFromVikingUri(value: string | undefined) {
  if (!value) return ''

  const match = value.match(/\/docs\/(en|zh)\/(.+)$/)
  if (!match) return ''

  return `/${match[1]}/${match[2].replace(/(?:\/index)?\.mdx?$/, '')}`
}
