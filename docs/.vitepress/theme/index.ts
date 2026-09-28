import { docsLanguageEntry } from './language-entry.js'
import { createLanguagePreference } from './language-preference.js'
import { h, defineAsyncComponent } from 'vue'
import DefaultTheme, { VPButton } from 'vitepress/theme'
import DocBreadcrumb from './components/DocBreadcrumb.vue'
import LocaleSwitch from './components/LocaleSwitch.vue'
import { useData, withBase } from 'vitepress'
import type { EnhanceAppContext } from 'vitepress'
import CopyMarkdownButton from './CopyMarkdownButton.vue'
import LlmsTxtLink from './LlmsTxtLink.vue'
import OpenVikingSearch from './OpenVikingSearch.vue'
import ApiExampleTabsEnhancer from './ApiExampleTabsEnhancer.vue'
import { initVikingBotWidget, syncVikingBotLocale } from './vikingbot-widget'
import { trackPageView } from './track'
import './custom.css'
import './reading.css'

type OpenVikingPreference = {
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

function normalizeTheme(value: string | null): OpenVikingPreference['theme'] {
  if (value === 'light' || value === 'dark') return value
  return undefined
}

function readStoredPreference(): OpenVikingPreference {
  try {
    const rawPreference = sessionStorage.getItem(PREFERENCE_SESSION_KEY)
    if (!rawPreference) return {}
    const preference = JSON.parse(rawPreference) as OpenVikingPreference
    return {
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
  let cookies = ''
  try { cookies = document.cookie } catch { return {} }
  const cookie = cookies
    .split('; ')
    .find((item) => item.startsWith(`${PREFERENCE_COOKIE_KEY}=`))

  if (!cookie) return {}

  try {
    const rawPreference = decodeURIComponent(cookie.slice(PREFERENCE_COOKIE_KEY.length + 1))
    const preference = JSON.parse(rawPreference) as OpenVikingPreference
    return {
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
    theme: incoming.theme ?? base.theme
  }
}

function writeStoredPreference(preference: OpenVikingPreference) {
  const nextPreference = mergePreferences(readStoredPreference(), preference)
  try { sessionStorage.setItem(PREFERENCE_SESSION_KEY, JSON.stringify(nextPreference)) } catch { /* Optional theme persistence. */ }
  try { writeCookiePreference(nextPreference) } catch { /* Optional theme persistence. */ }
}

function readTransferredPreference(): OpenVikingPreference {
  if (typeof window === 'undefined') return {}
  if (!window.name.startsWith(PREFERENCE_TRANSFER_PREFIX)) return {}

  try {
    const preference = JSON.parse(
      window.name.slice(PREFERENCE_TRANSFER_PREFIX.length)
    ) as OpenVikingPreference
    return {
      theme: normalizeTheme(preference.theme ?? null)
    }
  } catch {
    return {}
  }
}

function writeTransferredPreference(preference: OpenVikingPreference) {
  if (!preference.theme) return

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
    theme: normalizeTheme(preference.theme ?? null)
  }

  if (normalizedPreference.theme) {
    writeStoredPreference(normalizedPreference)
    writeTransferredPreference(mergePreferences(readStoredPreference(), normalizedPreference))
  }

  if (normalizedPreference.theme) {
    try { localStorage.setItem(VITEPRESS_THEME_KEY, normalizedPreference.theme) } catch { /* Still apply the theme. */ }
    document.documentElement.classList.toggle('dark', normalizedPreference.theme === 'dark')
  }
}

function syncPreferenceFromPeerSite() {
  if (typeof window === 'undefined') return

  applyPreference(readPersistedPreference())
}

function currentDocsTheme(): OpenVikingPreference['theme'] {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

function syncCurrentDocsPreference() {
  const preference = mergePreferences(readPersistedPreference(), {
    theme: currentDocsTheme()
  })
  writeStoredPreference(preference)
  writeTransferredPreference(preference)
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
    const { lang } = useData()
    const zh = lang.value.startsWith('zh')
    return h(DefaultTheme.Layout, null, {
      'doc-before': () => [h(DocBreadcrumb), h('div', { class: 'doc-page-actions' }, [
        h(LlmsTxtLink),
        h(CopyMarkdownButton)
      ])],
      'sidebar-nav-before': () => h('a', { class: 'sidebar-home-link', href: withBase(zh ? '/zh/' : '/en/') }, zh ? '← 文档首页' : '← Documentation home'),
      'doc-after': () => h(ApiExampleTabsEnhancer),
      'nav-bar-content-before': () => h(OpenVikingSearch),
      'nav-bar-content-after': () => h(LocaleSwitch),
      'nav-screen-content-after': () => h(LocaleSwitch)
    })
  },
  enhanceApp({ app, router }: EnhanceAppContext) {
    app.component('VPButton', VPButton)
    app.component('DocsHome', defineAsyncComponent(() => import('./components/DocsHome.vue')))
    if (import.meta.env.SSR || typeof window === 'undefined') return

    const policy = createLanguagePreference()
    const previousBeforeHook = router.onBeforeRouteChange
    router.onBeforeRouteChange = async (to: string) => {
      if (await previousBeforeHook?.(to) === false) return false
      const target = docsLanguageEntry(new URL(to, location.href).href, withBase('/'), policy)
      if (target) {
        void router.go(target)
        return false
      }
    }
    trackPageView(window.location.pathname)
    initVikingBotWidget()

    const previousHook = router.onAfterRouteChanged
    router.onAfterRouteChanged = (to: string) => {
      previousHook?.(to)
      trackPageView(to.split('?')[0].split('#')[0])
      // Update the existing widget after VitePress applies the page language.
      syncVikingBotLocale()
    }
  }
}
