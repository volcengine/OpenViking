/**
 * Shared language contract, copied into Website, Blog, Docs and Studio.
 * Keep this file and its contract tests identical across sites. No runtime dependency.
 * Self-contained so the same code can run in a pre-paint bootstrap script.
 */
export function createLanguagePreference(browser = typeof window === 'undefined' ? undefined : window) {
  const key = 'openviking-language-preference';
  const languages = ['en', 'zh', 'zh-TW', 'ja', 'ko', 'es'];
  const listeners = new Set();
  let pending = null;

  function parse(value) {
    if (typeof value !== 'string') return undefined;
    const tag = value.trim().replace(/_/g, '-').toLowerCase();
    if (!/^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/.test(tag)) return undefined;
    const parts = tag.split('-');
    if (parts[0] === 'zh') {
      if (parts.includes('hans')) return 'zh';
      return parts.some(part => ['hant', 'tw', 'hk', 'mo'].includes(part)) ? 'zh-TW' : 'zh';
    }
    return languages.includes(parts[0]) ? parts[0] : undefined;
  }

  function validPreference(value) {
    return value === 'auto' || languages.includes(value) ? value : undefined;
  }

  function cookiePreference() {
    try {
      for (const row of browser.document.cookie.split(';')) {
        const item = row.trim();
        if (!item.startsWith(key + '=')) continue;
        try {
          const value = validPreference(decodeURIComponent(item.slice(key.length + 1)));
          if (value) return value;
        } catch { /* Ignore a malformed cookie. */ }
      }
    } catch { /* Cookies may be disabled. */ }
    return undefined;
  }

  function localPreference() {
    try { return validPreference(browser.localStorage.getItem(key)); } catch { return undefined; }
  }

  function read() {
    const cookie = cookiePreference();
    // If a cookie write was rejected, keep the local choice until a peer changes it.
    const local = localPreference();
    if (pending && pending.cookie === cookie && pending.local === local) return pending.value;
    pending = null;
    if (cookie) return cookie;
    return local || 'auto';
  }

  function preferred() {
    const preference = read();
    if (preference !== 'auto') return preference;
    return parse(browser?.navigator.languages?.[0] || browser?.navigator.language) || 'en';
  }

  function query(names = ['lang']) {
    const params = new URLSearchParams(browser?.location.search || '');
    for (const name of names) {
      const value = parse(params.get(name));
      if (value) return value;
    }
    return undefined;
  }

  function resolve(explicit = query(), bilingual = false) {
    const language = parse(explicit) || preferred();
    return bilingual ? (language.startsWith('zh') ? 'zh' : 'en') : language;
  }

  function refresh() {
    for (const listener of listeners) listener();
  }

  function clearQuery(names = ['lang']) {
    if (!browser) return;
    const url = new URL(browser.location.href);
    for (const name of names) url.searchParams.delete(name);
    if (url.href !== browser.location.href) {
      browser.history.replaceState(browser.history.state, '', url.href);
    }
  }

  function save(value) {
    if (!validPreference(value)) return;
    const before = cookiePreference();
    try { browser.localStorage.setItem(key, value); } catch { /* In-page fallback below. */ }
    try {
      const hostname = browser.location.hostname.toLowerCase();
      const domain = ['openviking.ai', 'openviking.net'].find(
        candidate => hostname === candidate || hostname.endsWith('.' + candidate),
      );
      browser.document.cookie = [
        key + '=' + encodeURIComponent(value), 'Path=/', 'Max-Age=31536000', 'SameSite=Lax',
        domain ? 'Domain=.' + domain : '', browser.location.protocol === 'https:' ? 'Secure' : '',
      ].filter(Boolean).join('; ');
    } catch { /* Storage access must not prevent a language switch. */ }
    pending = cookiePreference() === value ? null : { value, cookie: before, local: localPreference() };
    refresh();
  }

  function subscribe(listener) {
    if (listeners.size === 0) {
      for (const event of ['focus', 'pageshow', 'popstate', 'storage', 'languagechange']) {
        browser?.addEventListener(event, refresh);
      }
    }
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) {
        for (const event of ['focus', 'pageshow', 'popstate', 'storage', 'languagechange']) {
          browser?.removeEventListener(event, refresh);
        }
      }
    };
  }

  return { key, parse, read, preferred, query, resolve, save, clearQuery, subscribe, refresh };
}
