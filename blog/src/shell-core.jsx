import { useState, useEffect, useCallback } from 'react';
import { createLanguagePreference } from './language-preference.js';
export const languagePreference = createLanguagePreference();

/* ---------- pathname router ---------- */

export { parsePath, parseHash, parseBrowserLocation, buildPath, postPath } from './routes.js';
import { parsePath, parseHash, parseBrowserLocation, buildPath } from './routes.js';

function parseHref(href, fallbackRoute) {
  if (href.startsWith('#/')) return parseHash(href);
  if (href.startsWith('/')) {
    const url = new URL(href, window.location.origin);
    return parsePath(url.pathname, url.search);
  }
  return { route: fallbackRoute || { name: 'index' }, query: {}, raw: href };
}

export function useSiteRouter() {
  const [state, setState] = useState(() => parseBrowserLocation());

  useEffect(() => {
    if (location.hash.startsWith('#/')) {
      const next = parseHash(location.hash);
      history.replaceState(null, '', window.location.origin + buildPath(next.route, next.query));
      setState(next);
    }

    const onPop = () => setState(parseBrowserLocation());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((href) => {
    let next;
    if (typeof href === 'string') {
      next = parseHref(href, state.route);
    } else {
      next = {
        route: href.route || state.route,
        query: { ...state.query, ...(href.query || {}) },
      };
    }

    const path = buildPath(next.route, next.query);
    if (`${location.pathname}${location.search}` !== path) history.pushState(null, '', window.location.origin + path);
    setState({ ...next, raw: path });
  }, [state]);

  const setQuery = useCallback((patch) => {
    const next = { ...state.query, ...patch };
    Object.keys(next).forEach(k => { if (next[k] == null || next[k] === '') delete next[k]; });
    const path = buildPath(state.route, next) + (location.hash.startsWith('#/') ? '' : location.hash);
    // Unknown paths can start with //; keep history updates on this origin.
    if (`${location.pathname}${location.search}` !== path) history.pushState(null, '', window.location.origin + path);
    setState({ route: state.route, query: next, raw: path });
  }, [state]);

  return { ...state, navigate, setQuery };
}

/* ---------- i18n / locale helpers ---------- */

export const LANGS = [
  { code: 'en', label: 'English', short: 'EN' },
  { code: 'zh', label: '中文', short: '中' },
];

export const SHELL_STRINGS = {
  en: {
    siteName: 'OpenViking Blog',
    siteSub: 'Engineering notes',
    indexEyebrow: '2026',
    indexTitle: 'Blog in Public.',
    indexLede: 'Technical notes from the OpenViking team — on agents, protocols, and the systems behind them.',
    indexFocus: 'Agents / protocols / context',
    indexCadence: 'Field notes',
    countLabel: (n) => `${n} essays`,
    filterAll: 'All',
    filterMore: 'More',
    filterLess: 'Less',
    sortNewest: 'Newest first',
    sortOldest: 'Oldest first',
    sortNewestShort: 'New',
    sortOldestShort: 'Old',
    backToIndex: '← All essays',
    publishedOn: 'Published',
    updatedOn: 'Updated',
    readingTime: (m) => `${m} min read`,
    by: 'by',
    contents: 'Contents',
    prev: 'Previous',
    next: 'Next',
    relatedTitle: 'Continue reading',
    notFoundTitle: 'A page out of place.',
    notFoundBody: 'We couldn’t find this page. The link may have changed, or the address may be incomplete.',
    notFoundLabel: 'Page not found',
    notFoundHome: 'Back to all essays',
    notFoundRead: 'A good place to pick up',
    notFoundNote: 'There’s more to the story.',
    notFoundDocs: 'Explore the docs',
    langLabel: 'Language',
    followBrowser: 'Follow browser',
    themeLabel: 'Theme',
    notAvailableLang: 'This essay is not yet translated. Showing the available language.',
    tags: 'Tags',
  },
  zh: {
    siteName: 'OpenViking 博客',
    siteSub: '技术笔记',
    indexEyebrow: '2026',
    indexTitle: '感受 AI',
    indexLede: 'OpenViking 团队的技术笔记：Agent、协议、上下文工程，以及背后的系统实现。',
    indexFocus: 'Agent / 协议 / 上下文',
    indexCadence: '一线记录',
    countLabel: (n) => `${n} 篇文章`,
    filterAll: '全部',
    filterMore: '更多',
    filterLess: '收起',
    sortNewest: '最新优先',
    sortOldest: '最早优先',
    sortNewestShort: '新',
    sortOldestShort: '旧',
    backToIndex: '← 所有文章',
    publishedOn: '发布于',
    updatedOn: '更新于',
    readingTime: (m) => `阅读约 ${m} 分钟`,
    by: '作者',
    contents: '目录',
    prev: '上一篇',
    next: '下一篇',
    relatedTitle: '继续阅读',
    notFoundTitle: '这一页，没找到。',
    notFoundBody: '链接可能已经更改，也可能是地址少了几个字。回到文章列表，接着读吧。',
    notFoundLabel: '页面未找到',
    notFoundHome: '返回所有文章',
    notFoundRead: '不妨从这篇开始',
    notFoundNote: '故事还在继续。',
    notFoundDocs: '浏览文档',
    langLabel: '语言',
    followBrowser: '跟随浏览器',
    themeLabel: '主题',
    notAvailableLang: '本文尚未翻译，显示当前可用版本。',
    tags: '标签',
  },
};

export function useShellStrings(lang) {
  return SHELL_STRINGS[lang] || SHELL_STRINGS.en;
}

export function makeFormatDate(lang) {
  return (iso) => {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const locale = lang === 'zh' ? 'zh-CN' : 'en-US';
      return d.toLocaleDateString(locale, { year: 'numeric', month: lang === 'zh' ? 'long' : 'short', day: 'numeric' });
    } catch { return iso; }
  };
}

export function estimateReadingMinutes(text = '', lang = 'en') {
  const compact = String(text)
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!compact) return null;

  const cjkChars = (compact.match(/[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/g) || []).length;
  const latinText = compact.replace(/[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/g, ' ');
  const latinWords = (latinText.match(/[A-Za-z0-9][A-Za-z0-9_'/-]*/g) || []).length;
  const cjkRate = lang === 'zh' ? 500 : 560;
  const latinRate = 230;
  const minutes = (cjkChars / cjkRate) + (latinWords / latinRate);
  return Math.max(1, Math.ceil(minutes));
}

/* ---------- theme: light (纸) / dark (和纸) ---------- */

export const THEME_LIGHT = 'kami';
export const THEME_DARK = 'washi';
const PREFERENCE_COOKIE_KEY = 'openviking-preferences';
const SHARED_THEME_LIGHT = 'light';
const SHARED_THEME_DARK = 'dark';

function isBrowser() {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

function isBlogTheme(value) {
  return value === THEME_LIGHT || value === THEME_DARK;
}

function isSharedTheme(value) {
  return value === SHARED_THEME_LIGHT || value === SHARED_THEME_DARK;
}

export function sharedThemeToBlogTheme(theme) {
  if (theme === SHARED_THEME_DARK) return THEME_DARK;
  if (theme === SHARED_THEME_LIGHT) return THEME_LIGHT;
  return undefined;
}

export function blogThemeToSharedTheme(theme) {
  if (theme === THEME_DARK) return SHARED_THEME_DARK;
  if (theme === THEME_LIGHT) return SHARED_THEME_LIGHT;
  return undefined;
}

function mergePreferences(base, incoming) {
  return {
    theme: incoming.theme ?? base.theme,
  };
}

function readLocalPreferences() {
  if (!isBrowser()) return {};

  let theme;
  try { theme = localStorage.getItem('blog.theme'); } catch { /* Use system theme. */ }
  return {
    theme: isBlogTheme(theme) ? blogThemeToSharedTheme(theme) : undefined,
  };
}

export function readCookiePreferences() {
  if (!isBrowser()) return {};

  let cookies = '';
  try { cookies = document.cookie; } catch { return {}; }
  const cookie = cookies
    .split('; ')
    .find(item => item.startsWith(`${PREFERENCE_COOKIE_KEY}=`));

  if (!cookie) return {};

  try {
    const preference = JSON.parse(decodeURIComponent(cookie.slice(PREFERENCE_COOKIE_KEY.length + 1)));
    return {
      theme: isSharedTheme(preference.theme) ? preference.theme : undefined,
    };
  } catch {
    return {};
  }
}

function cookieDomain() {
  const hostname = window.location.hostname;
  if (hostname === 'localhost' || hostname === '127.0.0.1') return '';
  if (hostname.endsWith('.openviking.ai') || hostname === 'openviking.ai') return 'Domain=.openviking.ai';
  if (hostname.endsWith('.openviking.net') || hostname === 'openviking.net') return 'Domain=.openviking.net';
  return '';
}

export function writeCookiePreferences(preference) {
  if (!isBrowser()) return;

  const nextPreference = mergePreferences(readCookiePreferences(), preference);
  try { document.cookie = [
    `${PREFERENCE_COOKIE_KEY}=${encodeURIComponent(JSON.stringify(nextPreference))}`,
    'Path=/',
    'Max-Age=31536000',
    'SameSite=Lax',
    cookieDomain(),
  ].filter(Boolean).join('; '); } catch { /* Theme remains usable in-page. */ }
}

export function readPersistedPreferences() {
  return mergePreferences(readLocalPreferences(), readCookiePreferences());
}

export function getInitialLang(queryLang) {
  return languagePreference.resolve(queryLang, true);
}

export function getSystemTheme() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? THEME_DARK : THEME_LIGHT;
}

export function getInitialTheme() {
  const sharedTheme = readPersistedPreferences().theme;
  const sharedBlogTheme = sharedThemeToBlogTheme(sharedTheme);
  if (sharedBlogTheme) return sharedBlogTheme;
  return getSystemTheme();
}

export function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.style.colorScheme = theme === THEME_DARK ? 'dark' : 'light';
}

export function isDark(theme) {
  return theme === THEME_DARK;
}
