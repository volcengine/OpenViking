import { h } from 'vue'
import DefaultTheme from 'vitepress/theme'
import CopyMarkdownButton from './CopyMarkdownButton.vue'
import LlmsTxtLink from './LlmsTxtLink.vue'
import './custom.css'

type OpenVikingPreference = {
  lang?: 'zh' | 'en'
  theme?: 'light' | 'dark'
}

const PREFERENCE_SESSION_KEY = 'openviking-preferences'
const PREFERENCE_COOKIE_KEY = 'openviking-preferences'
const PREFERENCE_TRANSFER_PREFIX = 'openviking-preferences:'
const VITEPRESS_THEME_KEY = 'vitepress-theme-appearance'
const LOCAL_PLAYGROUND_URL = 'http://localhost:8080/'
const MAIN_SITE_HOSTS = new Set([
  'www.openviking.ai',
  'openviking.ai',
  'www.openviking.net',
  'openviking.net',
  'localhost:8080',
  '127.0.0.1:8080'
])

function normalizeLang(value: string | null): OpenVikingPreference['lang'] {
  if (value === 'zh' || value === 'en') return value
  return undefined
}

function normalizeTheme(value: string | null): OpenVikingPreference['theme'] {
  if (value === 'light' || value === 'dark') return value
  return undefined
}

function readStoredPreference(): OpenVikingPreference {
  const rawPreference = sessionStorage.getItem(PREFERENCE_SESSION_KEY)
  if (!rawPreference) return {}

  try {
    const preference = JSON.parse(rawPreference) as OpenVikingPreference
    return {
      lang: normalizeLang(preference.lang ?? null),
      theme: normalizeTheme(preference.theme ?? null)
    }
  } catch {
    return {}
  }
}

function cookieDomain() {
  const hostname = window.location.hostname
  if (hostname === 'localhost' || hostname === '127.0.0.1') return ''
  if (hostname.endsWith('.openviking.ai') || hostname === 'openviking.ai') {
    return 'Domain=.openviking.ai'
  }
  if (hostname.endsWith('.openviking.net') || hostname === 'openviking.net') {
    return 'Domain=.openviking.net'
  }
  return ''
}

function readCookiePreference(): OpenVikingPreference {
  const cookie = document.cookie
    .split('; ')
    .find((item) => item.startsWith(`${PREFERENCE_COOKIE_KEY}=`))

  if (!cookie) return {}

  try {
    const rawPreference = decodeURIComponent(cookie.slice(PREFERENCE_COOKIE_KEY.length + 1))
    const preference = JSON.parse(rawPreference) as OpenVikingPreference
    return {
      lang: normalizeLang(preference.lang ?? null),
      theme: normalizeTheme(preference.theme ?? null)
    }
  } catch {
    return {}
  }
}

function writeCookiePreference(preference: OpenVikingPreference) {
  const nextPreference = mergePreferences(readCookiePreference(), preference)
  document.cookie = [
    `${PREFERENCE_COOKIE_KEY}=${encodeURIComponent(JSON.stringify(nextPreference))}`,
    'Path=/',
    'Max-Age=31536000',
    'SameSite=Lax',
    cookieDomain()
  ]
    .filter(Boolean)
    .join('; ')
}

function mergePreferences(
  base: OpenVikingPreference,
  incoming: OpenVikingPreference
): OpenVikingPreference {
  return {
    lang: incoming.lang ?? base.lang,
    theme: incoming.theme ?? base.theme
  }
}

function writeStoredPreference(preference: OpenVikingPreference) {
  const nextPreference = mergePreferences(readStoredPreference(), preference)
  sessionStorage.setItem(PREFERENCE_SESSION_KEY, JSON.stringify(nextPreference))
  writeCookiePreference(nextPreference)
}

function readTransferredPreference(): OpenVikingPreference {
  if (typeof window === 'undefined') return {}
  if (!window.name.startsWith(PREFERENCE_TRANSFER_PREFIX)) return {}

  try {
    const preference = JSON.parse(
      window.name.slice(PREFERENCE_TRANSFER_PREFIX.length)
    ) as OpenVikingPreference
    return {
      lang: normalizeLang(preference.lang ?? null),
      theme: normalizeTheme(preference.theme ?? null)
    }
  } catch {
    return {}
  }
}

function writeTransferredPreference(preference: OpenVikingPreference) {
  if (!preference.lang && !preference.theme) return

  window.name = `${PREFERENCE_TRANSFER_PREFIX}${JSON.stringify(preference)}`
}

function readPersistedPreference() {
  return mergePreferences(
    mergePreferences(readStoredPreference(), readCookiePreference()),
    readTransferredPreference()
  )
}

function applyPreference(preference: OpenVikingPreference) {
  const normalizedPreference: OpenVikingPreference = {
    lang: normalizeLang(preference.lang ?? null),
    theme: normalizeTheme(preference.theme ?? null)
  }

  if (normalizedPreference.lang || normalizedPreference.theme) {
    writeStoredPreference(normalizedPreference)
    writeTransferredPreference(mergePreferences(readStoredPreference(), normalizedPreference))
  }

  if (normalizedPreference.theme) {
    localStorage.setItem(VITEPRESS_THEME_KEY, normalizedPreference.theme)
    document.documentElement.classList.toggle('dark', normalizedPreference.theme === 'dark')
  }

  const lang = normalizedPreference.lang
  if (!lang) return

  const targetPath = resolveLocalizedPath(window.location.pathname, lang)
  const targetUrl = `${targetPath}${window.location.search}${window.location.hash}`
  const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`

  if (targetUrl !== currentUrl) {
    window.location.replace(targetUrl)
  }
}

function syncPreferenceFromPeerSite() {
  if (typeof window === 'undefined') return

  applyPreference(readPersistedPreference())
}

function resolveLocalizedPath(pathname: string, lang?: OpenVikingPreference['lang']) {
  if (lang === 'zh') {
    if (pathname.startsWith('/en/')) return pathname.replace(/^\/en\//, '/zh/')
  }

  if (lang === 'en') {
    if (pathname.startsWith('/zh/')) return pathname.replace(/^\/zh\//, '/en/')
  }

  return pathname
}

function currentDocsLang(): OpenVikingPreference['lang'] {
  if (window.location.pathname.startsWith('/zh/')) return 'zh'
  if (window.location.pathname.startsWith('/en/')) return 'en'
  return undefined
}

function currentDocsTheme(): OpenVikingPreference['theme'] {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

function syncCurrentDocsPreference() {
  const preference = mergePreferences(readPersistedPreference(), {
    lang: currentDocsLang(),
    theme: currentDocsTheme()
  })
  writeStoredPreference(preference)
  writeTransferredPreference(preference)
}

function patchHistoryForPreferenceSync() {
  const originalPushState = window.history.pushState
  const originalReplaceState = window.history.replaceState

  window.history.pushState = function patchedPushState(...args) {
    const result = originalPushState.apply(this, args)
    window.setTimeout(syncCurrentDocsPreference)
    return result
  }

  window.history.replaceState = function patchedReplaceState(...args) {
    const result = originalReplaceState.apply(this, args)
    window.setTimeout(syncCurrentDocsPreference)
    return result
  }

  window.addEventListener('popstate', () => window.setTimeout(syncCurrentDocsPreference))
}

function watchThemePreference() {
  const observer = new MutationObserver(syncCurrentDocsPreference)
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class']
  })
}

function mainSiteUrlWithPreference(href: string) {
  const url = new URL(href, window.location.href)
  const isLocalDocs = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  const isMainSite = MAIN_SITE_HOSTS.has(url.host)

  if (!isMainSite) return undefined

  if (
    isLocalDocs &&
    ['www.openviking.ai', 'openviking.ai', 'www.openviking.net', 'openviking.net'].includes(
      url.hostname
    )
  ) {
    const localUrl = new URL(LOCAL_PLAYGROUND_URL)
    localUrl.pathname = url.pathname
    localUrl.search = url.search
    localUrl.hash = url.hash
    return localUrl
  }

  return url
}

function syncPreferenceToMainSiteLinks() {
  document.addEventListener(
    'click',
    (event) => {
      const link = event.target instanceof Element ? event.target.closest('a') : null
      if (!link) return

      const url = mainSiteUrlWithPreference(link.href)
      if (!url) return

      const preference = mergePreferences(readPersistedPreference(), {
        lang: currentDocsLang(),
        theme: currentDocsTheme()
      })
      writeStoredPreference(preference)
      writeTransferredPreference(preference)

      link.href = url.toString()
    },
    true
  )
}

function startPreferenceSync() {
  syncPreferenceFromPeerSite()
  syncCurrentDocsPreference()
  patchHistoryForPreferenceSync()
  watchThemePreference()
  syncPreferenceToMainSiteLinks()
  window.addEventListener('pageshow', syncPreferenceFromPeerSite)
}

syncPreferenceFromPeerSite()

if (typeof window !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startPreferenceSync, { once: true })
  } else {
    startPreferenceSync()
  }
}

export default {
  extends: DefaultTheme,
  Layout() {
    return h(DefaultTheme.Layout, null, {
      'doc-before': () => h(CopyMarkdownButton),
      'doc-footer-before': () => h(LlmsTxtLink)
    })
  }
}
