import React, { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';

/* ---------- locale + context ---------- */

export function pickLocale(v, lang, fb = 'en') {
  if (v == null) return '';
  if (typeof v === 'string' || typeof v === 'number') return v;
  if (Array.isArray(v)) return v;
  if (v[lang] != null) return v[lang];
  if (v[fb] != null) return v[fb];
  const k = Object.keys(v)[0];
  return k ? v[k] : '';
}

export const BlogContext = createContext({
  lang: 'en',
  theme: 'folio',
  fallbackLang: 'en',
  t: (m) => pickLocale(m, 'en'),
  formatDate: (iso) => iso,
  navigate: (href) => { location.href = href; },
  postSlug: null,
});

export const useBlog = () => useContext(BlogContext);
export const useLang = () => useContext(BlogContext).lang;
export const useT = () => useContext(BlogContext).t;
export const useTheme = () => useContext(BlogContext).theme;
export const useFormatDate = () => useContext(BlogContext).formatDate;

/* ---------- ids + slugs ---------- */

export function slugify(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[^\w一-鿿À-ſ\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
    .slice(0, 80) || 'h';
}

function flattenChildren(children) {
  let out = '';
  React.Children.forEach(children, (c) => {
    if (c == null || c === false) return;
    if (typeof c === 'string' || typeof c === 'number') out += c;
    else if (c.props && c.props.children) out += flattenChildren(c.props.children);
  });
  return out;
}

/* ---------- structure ---------- */

export function Article({ children, className = '' }) {
  return <article className={`b-article ${className}`}>{children}</article>;
}

export function Section({ children, tone, className = '' }) {
  return <section className={`b-section ${tone ? `b-section--${tone}` : ''} ${className}`}>{children}</section>;
}

export function Spacer({ h = 'md' }) {
  return <div className={`b-spacer b-spacer--${h}`} aria-hidden="true" />;
}

export function Hr({ ornament }) {
  return <hr className={`b-hr ${ornament ? 'b-hr--ornament' : ''}`} />;
}

/* ---------- headings ---------- */

function Heading({ level, children, id, eyebrow, toc }) {
  const Tag = `h${level}`;
  const text = flattenChildren(children);
  const autoId = id || slugify(text);
  const includeInToc = toc ?? level <= 3;
  return (
    <Tag id={autoId} className={`b-h b-h${level}`} data-toc={includeInToc ? 'true' : undefined}>
      {eyebrow ? <span className="b-eyebrow">{eyebrow}</span> : null}
      {children}
    </Tag>
  );
}
export const H1 = (p) => <Heading {...p} level={1} />;
export const H2 = (p) => <Heading {...p} level={2} />;
export const H3 = (p) => <Heading {...p} level={3} />;
export const H4 = (p) => <Heading {...p} level={4} />;

/* ---------- text ---------- */

export function P({ children, dropCap, className = '' }) {
  const { lang } = useBlog();
  const dropCapMode = dropCap === true ? 'auto' : dropCap;

  const shouldDropCap =
    dropCapMode === 'always' ||
    ((dropCapMode === 'auto' || dropCapMode === 'lang') && lang === 'en');

  return <p className={`b-p ${shouldDropCap ? 'b-p--drop' : ''} ${className}`}>{children}</p>;
}
export function Lead({ children }) {
  return <p className="b-lead">{children}</p>;
}
export function Small({ children }) {
  return <span className="b-small">{children}</span>;
}
export const Strong = ({ children }) => <strong className="b-strong">{children}</strong>;
export const Em = ({ children }) => <em className="b-em">{children}</em>;
export const InlineCode = ({ children }) => <code className="b-code">{children}</code>;
export const Kbd = ({ children }) => <kbd className="b-kbd">{children}</kbd>;
export const Mark = ({ children }) => <mark className="b-mark">{children}</mark>;

/* ---------- links ---------- */

export function ExternalArrowIcon() {
  return (
    <svg className="b-a__ext" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      <path d="M3.2 8.8 8.8 3.2" />
      <path d="M5.25 3.2H8.8v3.55" />
    </svg>
  );
}

export function A({ href = '#', children, external }) {
  const isExt = external ?? /^(https?:|mailto:)/.test(href);
  const { navigate } = useBlog();
  const onClick = isExt ? undefined : (e) => {
    if (href.startsWith('#/') || href.startsWith('/')) {
      e.preventDefault();
      navigate(href);
    }
  };
  return (
    <a className={`b-a ${isExt ? 'b-a--ext' : ''}`} href={href} onClick={onClick}
       target={isExt ? '_blank' : undefined} rel={isExt ? 'noreferrer' : undefined}>
      {children}{isExt ? <ExternalArrowIcon /> : null}
    </a>
  );
}

/* ---------- lists ---------- */

export const Ul = ({ children, marker }) => <ul className={`b-ul ${marker ? `b-ul--${marker}` : ''}`}>{children}</ul>;
export const Ol = ({ children }) => <ol className="b-ol">{children}</ol>;
export const Li = ({ children }) => <li className="b-li">{children}</li>;
export const Dl = ({ children }) => <dl className="b-dl">{children}</dl>;
export const Dt = ({ children }) => <dt className="b-dt">{children}</dt>;
export const Dd = ({ children }) => <dd className="b-dd">{children}</dd>;

/* ---------- code blocks ---------- */

const KW = {
  js: 'const let var function return if else for while do switch case break continue new class extends import export from default as async await try catch finally throw typeof instanceof in of yield void this super null undefined true false static get set'.split(' '),
  css: 'and or not only from to important inherit initial unset auto none'.split(' '),
};
function tokenize(src, lang = 'js') {
  const out = [];
  let i = 0, n = src.length;
  const push = (cls, text) => out.push({ cls, text });
  const isAlpha = (c) => /[A-Za-z_$]/.test(c);
  const isAlnum = (c) => /[A-Za-z0-9_$]/.test(c);
  const kwSet = new Set(KW[lang] || KW.js);
  while (i < n) {
    const c = src[i], c2 = src[i] + (src[i+1] || '');
    if (c2 === '//') { let j = i; while (j < n && src[j] !== '\n') j++; push('cm', src.slice(i,j)); i = j; continue; }
    if (c2 === '/*') { let j = i+2; while (j < n && !(src[j]==='*'&&src[j+1]==='/')) j++; j = Math.min(n, j+2); push('cm', src.slice(i,j)); i = j; continue; }
    if (c === '"' || c === "'" || c === '`') {
      const q = c; let j = i+1;
      while (j < n) { if (src[j] === '\\') j+=2; else if (src[j] === q) { j++; break; } else j++; }
      push('st', src.slice(i,j)); i = j; continue;
    }
    if (/[0-9]/.test(c)) {
      let j = i; while (j < n && /[0-9.xXa-fA-F]/.test(src[j])) j++;
      push('nu', src.slice(i,j)); i = j; continue;
    }
    if (isAlpha(c)) {
      let j = i; while (j < n && isAlnum(src[j])) j++;
      const w = src.slice(i,j);
      push(kwSet.has(w) ? 'kw' : (/^[A-Z]/.test(w) ? 'ty' : 'id'), w);
      i = j; continue;
    }
    if (/[{}()[\];,.:?]/.test(c)) { push('pn', c); i++; continue; }
    if (/[=+\-*/%<>!&|^~]/.test(c)) {
      let j = i; while (j < n && /[=+\-*/%<>!&|^~]/.test(src[j])) j++;
      push('op', src.slice(i,j)); i = j; continue;
    }
    push('tx', c); i++;
  }
  return out;
}

export function Pre({ children, lang = 'js', filename, lineNumbers = true }) {
  const src = typeof children === 'string' ? children : flattenChildren(children);
  const trimmed = src.replace(/^\n+|\n+$/g, '');
  const tokens = useMemo(() => tokenize(trimmed, lang), [trimmed, lang]);
  const lines = useMemo(() => {
    const ls = [[]];
    for (const t of tokens) {
      const parts = t.text.split('\n');
      parts.forEach((p, idx) => {
        if (p.length) ls[ls.length - 1].push({ cls: t.cls, text: p });
        if (idx < parts.length - 1) ls.push([]);
      });
    }
    return ls;
  }, [tokens]);
  return (
    <div className="b-pre">
      {filename ? <div className="b-pre__bar"><span className="b-pre__file">{filename}</span><span className="b-pre__lang">{lang}</span></div> : null}
      <pre className="b-pre__code"><code>
        {lines.map((line, i) => (
          <span className="b-pre__line" key={i}>
            {lineNumbers ? <span className="b-pre__ln" aria-hidden="true">{String(i+1).padStart(2, ' ')}</span> : null}
            <span className="b-pre__lc">{line.length === 0 ? <span> </span> : line.map((t, j) => (
              <span className={`tk-${t.cls}`} key={j}>{t.text}</span>
            ))}</span>
          </span>
        ))}
      </code></pre>
    </div>
  );
}

/* ---------- quotes ---------- */

export function Quote({ children, cite }) {
  return (
    <blockquote className="b-quote">
      <div className="b-quote__body">{children}</div>
      {cite ? <footer className="b-quote__cite">— {cite}</footer> : null}
    </blockquote>
  );
}

export function Pull({ children, side = 'right' }) {
  return <aside className={`b-pull b-pull--${side}`}>{children}</aside>;
}

/* ---------- figures ---------- */

export function Figure({ src, alt, caption, credit, frame = 'plain', size = 'md' }) {
  return (
    <figure className={`b-figure b-figure--${size} b-figure--${frame}`}>
      <div className="b-figure__media">
        {src ? (
          <img
            src={src}
            alt={alt || (caption ? flattenChildren(caption) : '')}
            loading="lazy"
            decoding="async"
          />
        ) : <div className="b-figure__ph">image</div>}
      </div>
      {caption || credit ? (
        <figcaption className="b-figure__cap">
          {caption ? <span className="b-figure__cap-text">{caption}</span> : null}
          {credit ? <span className="b-figure__cap-credit">{credit}</span> : null}
        </figcaption>
      ) : null}
    </figure>
  );
}

/* ---------- callouts ---------- */

const CALLOUT_LABEL = {
  note:  { en: 'Note',     zh: '注释' },
  tip:   { en: 'Tip',      zh: '提示' },
  warn:  { en: 'Warning',  zh: '注意' },
  info:  { en: 'Info',     zh: '说明' },
  quote: { en: 'Quote',    zh: '引述' },
};

export function Callout({ type = 'note', title, children }) {
  const { lang } = useBlog();
  const label = title ?? pickLocale(CALLOUT_LABEL[type] || CALLOUT_LABEL.note, lang);
  return (
    <aside className={`b-callout b-callout--${type}`}>
      <div className="b-callout__label">{label}</div>
      <div className="b-callout__body">{children}</div>
    </aside>
  );
}

/* ---------- aside (margin note) ---------- */

export function Aside({ children }) {
  return <aside className="b-aside">{children}</aside>;
}

/* ---------- table of contents ---------- */

export function TOC({ minLevel = 2, maxLevel = 3, title, lang, foldable = false }) {
  const { lang: contextLang } = useBlog();
  const tocLang = lang || contextLang;
  const [items, setItems] = useState([]);
  const [active, setActive] = useState(null);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    const root = document.querySelector('.b-post__body');
    if (!root) return;
    setActive(null);
    setExpanded(false);
    const sel = Array.from({ length: maxLevel - minLevel + 1 }, (_, i) => `h${minLevel + i}[id][data-toc="true"]`).join(',');
    const update = () => {
      const article = root.querySelector('.b-article');
      if (!article) {
        setItems([]);
        return;
      }
      const found = Array.from(article.querySelectorAll(sel))
        .filter(el => !el.closest('.b-toc'))
        .map(el => ({ id: el.id, text: el.textContent.trim(), level: +el.tagName[1] }));
      setItems(found);
    };
    update();
    const mo = new MutationObserver(update);
    mo.observe(root, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, [minLevel, maxLevel, tocLang]);

  useEffect(() => {
    if (!items.length) return;
    const onScroll = () => {
      let cur = items[0]?.id;
      for (const it of items) {
        const el = document.getElementById(it.id);
        if (!el) continue;
        if (el.getBoundingClientRect().top < 120) cur = it.id;
        else break;
      }
      setActive(cur);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, [items]);

  if (!items.length) return null;
  const heading = pickLocale(title ?? { en: 'Contents', zh: '目录' }, tocLang);
  const longToc = foldable && items.length > 18;
  let currentParent = null;
  const normalized = items.map((it, index) => {
    if (it.level === minLevel) currentParent = it.id;
    return {
      ...it,
      index,
      parentId: it.level === minLevel ? it.id : currentParent,
    };
  });
  const activeItem = normalized.find(it => it.id === active);
  const activeParent = activeItem?.parentId || normalized.find(it => it.level === minLevel)?.id;
  const visibleItems = longToc && !expanded
    ? normalized.filter(it => it.level === minLevel || it.parentId === activeParent)
    : normalized;
  const hiddenCount = Math.max(0, normalized.length - visibleItems.length);
  const toggleLabel = expanded
    ? pickLocale({ en: 'Collapse', zh: '折叠' }, tocLang)
    : pickLocale({ en: 'Expand', zh: '展开' }, tocLang);
  return (
    <nav className="b-toc" aria-label={heading}>
      <div className="b-toc__head">
        <div>
          <div className="b-toc__title">{heading}</div>
          {longToc && !expanded ? (
            <div className="b-toc__hint">
              {pickLocale({ en: `${hiddenCount} folded`, zh: `${hiddenCount} 项已折叠` }, tocLang)}
            </div>
          ) : null}
        </div>
        {longToc ? (
          <button
            type="button"
            className="b-toc__toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded(v => !v)}
          >
            {toggleLabel}
          </button>
        ) : null}
      </div>
      <ol className="b-toc__list">
        {visibleItems.map((it, i) => (
          <li key={it.id} className={`b-toc__item b-toc__item--l${it.level} ${active === it.id ? 'is-active' : ''}`}>
            <a href={`#${it.id}`} onClick={(e) => {
              e.preventDefault();
              const el = document.getElementById(it.id);
              if (el) {
                const y = el.getBoundingClientRect().top + window.scrollY - 80;
                window.scrollTo({ top: y, behavior: 'smooth' });
                history.replaceState(null, '', `${location.pathname}${location.search}#${it.id}`);
              }
            }} aria-label={it.text}>
              <span className="b-toc__text">
                <span className="b-toc__text-label">{it.text}</span>
                <span className="b-toc__popover" aria-hidden="true">{it.text}</span>
              </span>
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}

/* ---------- columns / grid ---------- */

export const Cols = ({ children, count = 2 }) => <div className={`b-cols b-cols--${count}`}>{children}</div>;
export const Col = ({ children, span = 1 }) => <div className="b-col" style={{ '--col-span': span }}>{children}</div>;

/* ---------- tag ---------- */

export const Tag = ({ children, href, tone }) => {
  const { navigate } = useBlog();
  const Cmp = href ? 'a' : 'span';
  return (
    <Cmp className={`b-tag ${tone ? `b-tag--${tone}` : ''}`} href={href}
      onClick={href ? (e) => { e.preventDefault(); navigate(href); } : undefined}>
      {children}
    </Cmp>
  );
};

/* ---------- table ---------- */

export function Table({ headers, rows, caption }) {
  return (
    <div className="b-table-wrap">
      <table className="b-table">
        {caption ? <caption className="b-table__cap">{caption}</caption> : null}
        {headers ? <thead><tr>{headers.map((h, i) => <th key={i}>{h}</th>)}</tr></thead> : null}
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------- progress ---------- */

export function ReadingProgress() {
  const ref = useRef(null);
  useEffect(() => {
    const onScroll = () => {
      const article = document.querySelector('.b-article');
      if (!article || !ref.current) return;
      const rect = article.getBoundingClientRect();
      const total = article.offsetHeight - window.innerHeight;
      const scrolled = Math.max(0, -rect.top);
      const pct = total > 0 ? Math.min(1, scrolled / total) : 0;
      ref.current.style.setProperty('--progress', String(pct));
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);
  return <div ref={ref} className="b-progress" aria-hidden="true" />;
}

/* ---------- post registry ---------- */

const registry = { posts: [], byId: {} };

export function registerPost(post) {
  if (!post || !post.id || registry.byId[post.id]) return;
  registry.byId[post.id] = post;
  registry.posts.push(post);
}

export function getPostBySlug(slug) {
  return registry.byId[slug] || null;
}

export function getAllPosts() {
  return [...registry.posts].sort((a, b) => {
    const ad = a.meta?.publishedAt || '';
    const bd = b.meta?.publishedAt || '';
    return bd.localeCompare(ad);
  });
}

export function getAllTags() {
  const set = new Set();
  for (const p of getAllPosts()) (p.meta?.tags || []).forEach(t => set.add(t));
  return Array.from(set);
}

export function neighbors(slug) {
  const posts = getAllPosts();
  const i = posts.findIndex(p => p.id === slug);
  if (i < 0) return { prev: null, next: null };
  return { prev: posts[i + 1] || null, next: posts[i - 1] || null };
}
