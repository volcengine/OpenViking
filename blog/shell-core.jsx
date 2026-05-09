/* shell-core.jsx — hash router, i18n, theme persistence, registry helpers.
 * Stays small and free of UI; the shell renders by reading state this provides.
 */
(function () {
const { useState, useEffect, useMemo, useCallback } = React;

/* ---------- hash router ---------- */
// Routes:
//   #/                        — index
//   #/post/<slug>             — post detail
// Query (after `?`): lang, theme, tag

function parseHash(hash) {
  const raw = (hash || '').replace(/^#/, '') || '/';
  const [pathPart, queryPart = ''] = raw.split('?');
  const segs = pathPart.split('/').filter(Boolean);
  const query = {};
  for (const kv of queryPart.split('&').filter(Boolean)) {
    const [k, v = ''] = kv.split('=');
    query[decodeURIComponent(k)] = decodeURIComponent(v);
  }
  let route = { name: 'index' };
  if (segs[0] === 'post' && segs[1]) route = { name: 'post', slug: segs[1] };
  return { route, query, raw };
}

function buildHash(route, query) {
  let path = '/';
  if (route.name === 'post') path = `/post/${route.slug}`;
  const qs = Object.entries(query)
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
  return `#${path}${qs ? '?' + qs : ''}`;
}

function useHashRouter() {
  const [state, setState] = useState(() => parseHash(location.hash));
  useEffect(() => {
    const onHash = () => setState(parseHash(location.hash));
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const navigate = useCallback((href) => {
    if (typeof href === 'string') {
      // accept "#/path?…" or "/path?…"
      const h = href.startsWith('#') ? href : '#' + href;
      if (location.hash !== h) location.hash = h;
    } else {
      const h = buildHash(href.route || state.route, { ...state.query, ...(href.query || {}) });
      if (location.hash !== h) location.hash = h;
    }
  }, [state]);
  const setQuery = useCallback((patch) => {
    const next = { ...state.query, ...patch };
    Object.keys(next).forEach(k => { if (next[k] == null || next[k] === '') delete next[k]; });
    const h = buildHash(state.route, next);
    if (location.hash !== h) location.hash = h;
  }, [state]);
  return { ...state, navigate, setQuery };
}

/* ---------- i18n / locale helpers ---------- */

const LANGS = [
  { code: 'en', label: 'English', short: 'EN' },
  { code: 'zh', label: '中文', short: '中' },
];

const SHELL_STRINGS = {
  en: {
    siteName: 'Blog Station',
    siteSub: 'A field guide',
    indexEyebrow: 'Volume 26 / 2026',
    indexTitle: 'Notes on building, slowly.',
    indexLede: 'A small library of essays on engineering, design and the texture of attention. Five themes, two languages, one quiet corner of the web.',
    countLabel: (n) => `${n} essays`,
    filterAll: 'All',
    sortNewest: 'Newest first',
    sortOldest: 'Oldest first',
    backToIndex: '← All essays',
    publishedOn: 'Published',
    updatedOn: 'Updated',
    readingTime: (m) => `${m} min read`,
    by: 'by',
    contents: 'Contents',
    prev: 'Previous',
    next: 'Next',
    relatedTitle: 'Continue reading',
    notFoundTitle: 'Nothing here',
    notFoundBody: 'That essay does not exist. It may have been a dream.',
    langLabel: 'Language',
    themeLabel: 'Theme',
    notAvailableLang: 'This essay is not yet translated. Showing English.',
    tags: 'Tags',
  },
  zh: {
    siteName: '博客站',
    siteSub: '田野手册',
    indexEyebrow: '第 26 卷 / 2026',
    indexTitle: '关于慢慢建造的笔记。',
    indexLede: '一个关于工程、设计与注意力质地的小型文集。五种主题,两种语言,网络的一个安静角落。',
    countLabel: (n) => `${n} 篇文章`,
    filterAll: '全部',
    sortNewest: '最新优先',
    sortOldest: '最早优先',
    backToIndex: '← 所有文章',
    publishedOn: '发布于',
    updatedOn: '更新于',
    readingTime: (m) => `阅读约 ${m} 分钟`,
    by: '作者',
    contents: '目录',
    prev: '上一篇',
    next: '下一篇',
    relatedTitle: '继续阅读',
    notFoundTitle: '此处空空如也',
    notFoundBody: '这篇文章不存在,也许只是一场梦。',
    langLabel: '语言',
    themeLabel: '主题',
    notAvailableLang: '本文尚未翻译,显示英文版本。',
    tags: '标签',
  },
};

function useShellStrings(lang) {
  return SHELL_STRINGS[lang] || SHELL_STRINGS.en;
}

function makeFormatDate(lang) {
  return (iso) => {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const locale = lang === 'zh' ? 'zh-CN' : 'en-US';
      return d.toLocaleDateString(locale, { year: 'numeric', month: lang === 'zh' ? 'long' : 'short', day: 'numeric' });
    } catch { return iso; }
  };
}

/* ---------- theme persistence ---------- */
const THEMES = [
  { id: 'folio',     label: { en: 'Folio',     zh: '文集' }, blurb: { en: 'Editorial · serif', zh: '编辑型 · 衬线' } },
  { id: 'console',   label: { en: 'Console',   zh: '终端' }, blurb: { en: 'Terminal · mono',  zh: '终端型 · 等宽' } },
  { id: 'atlas',     label: { en: 'Atlas',     zh: '图志' }, blurb: { en: 'Magazine · bold',  zh: '杂志型 · 粗体' } },
  { id: 'garden',    label: { en: 'Garden',    zh: '园圃' }, blurb: { en: 'Soft · serif',     zh: '温柔型 · 衬线' } },
  { id: 'brutalist', label: { en: 'Brutalist', zh: '粗野' }, blurb: { en: 'Raw · mono',       zh: '原始型 · 等宽' } },
];

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.style.colorScheme = theme === 'console' ? 'dark' : 'light';
}

/* ---------- registry helpers ---------- */
function getPostBySlug(slug) {
  return (window.Blog?.byId || {})[slug] || null;
}
function getAllPosts() {
  return [...(window.Blog?.posts || [])].sort((a, b) => {
    const ad = a.meta?.publishedAt || '';
    const bd = b.meta?.publishedAt || '';
    return bd.localeCompare(ad);
  });
}
function getAllTags() {
  const set = new Set();
  for (const p of getAllPosts()) (p.meta?.tags || []).forEach(t => set.add(t));
  return Array.from(set);
}
function neighbors(slug) {
  const posts = getAllPosts();
  const i = posts.findIndex(p => p.id === slug);
  if (i < 0) return { prev: null, next: null };
  return { prev: posts[i + 1] || null, next: posts[i - 1] || null };
}

/* expose */
Object.assign(window, {
  parseHash, buildHash, useHashRouter,
  LANGS, THEMES, SHELL_STRINGS, useShellStrings, makeFormatDate, applyTheme,
  getPostBySlug, getAllPosts, getAllTags, neighbors,
});
})();
