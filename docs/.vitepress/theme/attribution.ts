export const UTM_KEYS = [
  'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'
] as const

export type Utm = Partial<Record<typeof UTM_KEYS[number], string>>

const SESSION_KEY = 'openviking-docs-utm'
const MARKETING_MEDIA = new Set(['social', 'paid_social', 'cpc'])
const WEBSITE_ORIGINS = new Set([
  'https://openviking.ai', 'https://www.openviking.ai',
  'https://openviking.net', 'https://www.openviking.net'
])

export function readUtm(href: string): Utm | undefined {
  try {
    const params = new URL(href).searchParams
    const utm: Utm = {}
    for (const key of UTM_KEYS) {
      const values = params.getAll(key)
      if (values.length > 1 || values.some(value => value.length > 2048)) return
      if (values.length) utm[key] = values[0]
    }
    if (!utm.utm_source?.trim() || !utm.utm_medium?.trim()) return
    if (MARKETING_MEDIA.has(utm.utm_medium) && !utm.utm_campaign?.trim()) return
    return utm
  } catch {
    return undefined
  }
}

function relativePath(pathname: string, base: string): string | undefined {
  const prefix = '/' + base.split('/').filter(Boolean).join('/')
  if (pathname === prefix) return ''
  const directory = prefix === '/' ? '/' : prefix + '/'
  return pathname.startsWith(directory) ? pathname.slice(directory.length) : undefined
}

export function docsCampaign(pathname: string, base = '/'): string {
  return (relativePath(pathname, base) ?? '')
    .replace(/^(en|zh)(\/|$)/, '')
    .replace(/(?:^|\/)index(?:\.html)?$/, '')
    .replace(/\.html$/, '')
    .split('/').filter(Boolean).join('_') || 'main'
}

function isDocsPage(url: URL, current: URL, base: string): boolean {
  if (url.origin !== current.origin) return false
  const path = relativePath(url.pathname, base)
  if (path === undefined) return false
  if (path === '' || path === 'index.html') return true
  return /^(en|zh)(\/|$)/.test(path) && !/\.(?!html$)[^/]+$/.test(path)
}

function isConversionTarget(url: URL): boolean {
  if (url.origin === 'https://www.volcengine.com') {
    return /^\/product\/openviking-service\/?$/.test(url.pathname)
  }
  if (url.origin === 'https://console.volcengine.com') {
    return /^\/vikingdb\/openviking(?:\/|$)/.test(url.pathname)
  }
  return WEBSITE_ORIGINS.has(url.origin) && /^\/(?:enterprise\/?)?$/.test(url.pathname)
}

/** 只传标准 UTM，业务 query、区域路径及锚点保持不变。 */
export function attributionUrl(href: string, currentHref: string, base = '/'): string | undefined {
  try {
    const current = new URL(currentHref)
    const target = new URL(href, current)
    if (!['http:', 'https:'].includes(target.protocol)) return
    const incoming = readUtm(current.href)
    let utm: Utm
    if (isDocsPage(target, current, base)) {
      // 显式带参的目标是独立入口，不混入当前链路参数。
      if (!incoming || UTM_KEYS.some(key => target.searchParams.has(key))) return
      utm = incoming
    } else {
      if (!isConversionTarget(target)) return
      if (!incoming && readUtm(target.href)) return
      utm = incoming ?? {
        utm_source: 'opensource_docs', utm_medium: 'referral',
        utm_campaign: docsCampaign(current.pathname, base)
      }
    }
    const original = target.href
    for (const key of UTM_KEYS) {
      target.searchParams.delete(key)
      if (utm[key] !== undefined) target.searchParams.set(key, utm[key]!)
    }
    return target.href === original ? undefined : target.href
  } catch {
    return undefined
  }
}

export function startDocsAttribution(base = '/') {
  const originals = new WeakMap<HTMLAnchorElement, { original: string; applied: string }>()
  let refreshedHref: string | undefined

  function persist() {
    // URL 是本次来源的依据；无参数的新入站清理缓存，不恢复历史渠道。
    try {
      const utm = readUtm(location.href)
      if (!utm) sessionStorage.removeItem(SESSION_KEY)
      else {
        const serialized = JSON.stringify(utm)
        if (sessionStorage.getItem(SESSION_KEY) !== serialized) sessionStorage.setItem(SESSION_KEY, serialized)
      }
    } catch { /* 存储不可用不影响链接透传。 */ }
  }

  function refreshLink(link: HTMLAnchorElement) {
    if (link.hasAttribute('download')) return
    const href = link.getAttribute('href')
    if (href === null) return
    let entry = originals.get(link)
    if (!entry || href !== entry.applied) {
      entry = { original: href, applied: href }
      originals.set(link, entry)
    }
    const next = attributionUrl(entry.original, location.href, base) ?? entry.original
    entry.applied = next
    if (href !== next) link.setAttribute('href', next)
  }

  function refresh() {
    const href = location.href.split('#')[0]
    if (href === refreshedHref) return
    refreshedHref = href
    persist()
    document.querySelectorAll<HTMLAnchorElement>('a[href]').forEach(refreshLink)
  }

  const observer = new MutationObserver(records => {
    // 每批只处理新增或被外部改写的链接，自身补参不再触发链接计算。
    const links = new Set<HTMLAnchorElement>()
    for (const record of records) {
      if (record.type === 'attributes') {
        const link = record.target
        if (link instanceof HTMLAnchorElement && link.getAttribute('href') !== originals.get(link)?.applied) {
          links.add(link)
        }
      } else {
        for (const node of record.addedNodes) {
          if (!(node instanceof Element)) continue
          if (node instanceof HTMLAnchorElement && node.hasAttribute('href')) links.add(node)
          node.querySelectorAll<HTMLAnchorElement>('a[href]').forEach(link => {
            if (link instanceof HTMLAnchorElement) links.add(link)
          })
        }
      }
    }
    for (const link of links) {
      if (link.isConnected && link.ownerDocument === document) refreshLink(link)
    }
  })
  observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['href'] })
  const controller = new AbortController()
  document.addEventListener('click', event => {
    const link = event.target instanceof Element ? event.target.closest('a') : null
    if (link instanceof HTMLAnchorElement) refreshLink(link)
  }, { capture: true, signal: controller.signal })
  window.addEventListener('pageshow', refresh, { signal: controller.signal })
  refresh()

  return {
    refresh,
    routeTarget(to: string) {
      const target = new URL(to, location.href)
      return isDocsPage(target, new URL(location.href), base)
        ? attributionUrl(to, location.href, base) : undefined
    },
    dispose() { observer.disconnect(); controller.abort() }
  }
}
