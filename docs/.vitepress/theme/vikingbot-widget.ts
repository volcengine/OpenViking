/**
 * Loads the shared VikingBot embed widget for the docs site.
 *
 * There is no docs-owned trigger any more: the widget renders its own bottom
 * entry pill and right-edge toggle, the same as on the marketing site and the
 * blog, and pushes the VitePress layout aside via the rail contract read in
 * custom.css.
 *
 * Everything here is client-only. VitePress prerenders the theme during build,
 * so nothing may run at module scope that touches `window`.
 */

const WIDGET_URL =
  import.meta.env.VITE_VIKINGBOT_WIDGET_URL ||
  'https://openviking.net/studio/embed-widget/vikingbot-widget.js'
const WIDGET_SITE = 'vikingbot'

type WidgetLocale = 'en' | 'zh'

type WidgetMountOptions = {
  site: string
  locale?: WidgetLocale
  /** 'composer' renders the entry pill plus the right-edge toggle. */
  trigger?: 'composer' | 'floating' | 'none'
  /** 'push' publishes the rail contract on <html>. */
  layout?: 'push' | 'overlay'
  conversation?: 'thread' | 'channel'
  open?: boolean
}

type WidgetApi = {
  mount: (options: WidgetMountOptions) => void
  open: () => void
  toggle: () => void
  close: () => void
  unmount: () => void
}

declare global {
  interface Window {
    VikingBotWidget?: WidgetApi
  }
}

let scriptPromise: Promise<void> | null = null
let mountedLocale: WidgetLocale | null = null
let started = false

function loadScriptOnce(): Promise<void> {
  if (!scriptPromise) {
    scriptPromise = new Promise<void>((resolve, reject) => {
      const script = document.createElement('script')
      script.src = WIDGET_URL
      script.async = true
      script.dataset.vikingbotWidget = ''
      script.addEventListener('load', () => resolve(), { once: true })
      script.addEventListener(
        'error',
        () => {
          script.remove()
          reject(new Error(`Failed to load the VikingBot widget from ${WIDGET_URL}`))
        },
        { once: true }
      )
      document.head.appendChild(script)
    })
  }

  return scriptPromise.catch((error: unknown) => {
    // Drop the cached rejection so a later attempt can retry the load.
    scriptPromise = null
    throw error
  })
}

/**
 * VitePress writes the active locale onto <html lang>, which survives a
 * configured `base` — unlike matching the pathname against '/zh'.
 */
function currentLocale(): WidgetLocale {
  return document.documentElement.lang.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

function mount(locale: WidgetLocale) {
  const widget = window.VikingBotWidget
  if (!widget) {
    throw new Error('The VikingBot widget script did not register window.VikingBotWidget')
  }
  widget.mount({
    site: WIDGET_SITE,
    locale,
    trigger: 'composer',
    layout: 'push'
  })
  mountedLocale = locale
}

/**
 * Idle window before the script is fetched. The widget is an assistive
 * affordance, not documentation, and must not compete with the page for
 * bandwidth. requestIdleCallback is unavailable on Safari < 16.4.
 */
const IDLE_FALLBACK_MS = 2500

function whenIdle(run: () => void) {
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(run, { timeout: IDLE_FALLBACK_MS })
    return
  }
  window.setTimeout(run, IDLE_FALLBACK_MS)
}

/** Mounts the widget once per page load. Safe to call on every enhanceApp. */
export function initVikingBotWidget() {
  if (started) return
  started = true

  whenIdle(() => {
    void loadScriptOnce()
      .then(() => {
        mount(currentLocale())
      })
      .catch((error: unknown) => {
        // Silent beyond the console: a reader who never asked for the widget
        // should not be shown an error about a script they did not request.
        console.error(error)
      })
  })
}

/**
 * Switching locale is a client-side route change, so a widget mounted in the
 * other language would keep its old strings for the rest of the session.
 * Remount it — the visitor just asked for a different language, and their
 * conversation is keyed on the browser, not on this mount.
 */
export function syncVikingBotLocale() {
  const locale = currentLocale()
  if (!mountedLocale || mountedLocale === locale) return
  const widget = window.VikingBotWidget
  if (!widget) return
  widget.unmount()
  mount(locale)
}
