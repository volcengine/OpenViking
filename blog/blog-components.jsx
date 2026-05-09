/* blog-components.jsx — primitives every post composes from.
 * Classes carry the visual contract; themes.css restyles them per theme.
 * Posts NEVER write theme-specific styling; they assemble these primitives.
 *
 * Wrapped in IIFE so React-hook destructures don't collide with other files.
 */
(function () {
const { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } = React;

/* ---------- locale + context ---------- */

function pickLocale(v, lang, fb = 'en') {
  if (v == null) return '';
  if (typeof v === 'string' || typeof v === 'number') return v;
  if (Array.isArray(v)) return v;
  if (v[lang] != null) return v[lang];
  if (v[fb] != null) return v[fb];
  const k = Object.keys(v)[0];
  return k ? v[k] : '';
}

const BlogContext = createContext({
  lang: 'en',
  theme: 'folio',
  fallbackLang: 'en',
  t: (m) => pickLocale(m, 'en'),
  formatDate: (iso) => iso,
  navigate: (href) => { location.hash = href; },
  postSlug: null,
});

const useBlog = () => useContext(BlogContext);
const useLang = () => useContext(BlogContext).lang;
const useT = () => useContext(BlogContext).t;
const useTheme = () => useContext(BlogContext).theme;
const useFormatDate = () => useContext(BlogContext).formatDate;

/* ---------- ids + slugs ---------- */

function slugify(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[^\w\u4e00-\u9fff\u00c0-\u017f\s-]/g, '')
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

function Article({ children, className = '' }) {
  return <article className={`b-article ${className}`}>{children}</article>;
}

function Section({ children, tone, className = '' }) {
  return <section className={`b-section ${tone ? `b-section--${tone}` : ''} ${className}`}>{children}</section>;
}

function Spacer({ h = 'md' }) {
  return <div className={`b-spacer b-spacer--${h}`} aria-hidden="true" />;
}

function Hr({ ornament }) {
  return <hr className={`b-hr ${ornament ? 'b-hr--ornament' : ''}`} />;
}

/* ---------- headings ---------- */

function Heading({ level, children, id, eyebrow }) {
  const Tag = `h${level}`;
  const text = flattenChildren(children);
  const autoId = id || slugify(text);
  return (
    <Tag id={autoId} className={`b-h b-h${level}`} data-toc={level <= 3 ? 'true' : undefined}>
      {eyebrow ? <span className="b-eyebrow">{eyebrow}</span> : null}
      {children}
    </Tag>
  );
}
const H1 = (p) => <Heading {...p} level={1} />;
const H2 = (p) => <Heading {...p} level={2} />;
const H3 = (p) => <Heading {...p} level={3} />;
const H4 = (p) => <Heading {...p} level={4} />;

/* ---------- text ---------- */

function P({ children, dropCap, className = '' }) {
  return <p className={`b-p ${dropCap ? 'b-p--drop' : ''} ${className}`}>{children}</p>;
}
function Lead({ children }) {
  return <p className="b-lead">{children}</p>;
}
function Small({ children }) {
  return <span className="b-small">{children}</span>;
}
const Strong = ({ children }) => <strong className="b-strong">{children}</strong>;
const Em = ({ children }) => <em className="b-em">{children}</em>;
const InlineCode = ({ children }) => <code className="b-code">{children}</code>;
const Kbd = ({ children }) => <kbd className="b-kbd">{children}</kbd>;
const Mark = ({ children }) => <mark className="b-mark">{children}</mark>;

/* ---------- links ---------- */

function A({ href = '#', children, external }) {
  const isExt = external ?? /^(https?:|mailto:)/.test(href);
  const { navigate } = useBlog();
  const onClick = isExt ? undefined : (e) => {
    if (href.startsWith('#/')) {
      e.preventDefault();
      navigate(href);
    }
  };
  return (
    <a className={`b-a ${isExt ? 'b-a--ext' : ''}`} href={href} onClick={onClick}
       target={isExt ? '_blank' : undefined} rel={isExt ? 'noreferrer' : undefined}>
      {children}{isExt ? <span className="b-a__ext" aria-hidden="true">↗</span> : null}
    </a>
  );
}

/* ---------- lists ---------- */

const Ul = ({ children, marker }) => <ul className={`b-ul ${marker ? `b-ul--${marker}` : ''}`}>{children}</ul>;
const Ol = ({ children }) => <ol className="b-ol">{children}</ol>;
const Li = ({ children }) => <li className="b-li">{children}</li>;
const Dl = ({ children }) => <dl className="b-dl">{children}</dl>;
const Dt = ({ children }) => <dt className="b-dt">{children}</dt>;
const Dd = ({ children }) => <dd className="b-dd">{children}</dd>;

/* ---------- code blocks ---------- */

// Tiny tokenizer — handles strings, comments, numbers, keywords for js/jsx/ts/tsx/css.
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

function Pre({ children, lang = 'js', filename, lineNumbers = true }) {
  const src = typeof children === 'string' ? children : flattenChildren(children);
  const trimmed = src.replace(/^\n+|\n+$/g, '');
  const tokens = useMemo(() => tokenize(trimmed, lang), [trimmed, lang]);
  // group tokens into lines for line-number rendering
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

function Quote({ children, cite }) {
  return (
    <blockquote className="b-quote">
      <div className="b-quote__body">{children}</div>
      {cite ? <footer className="b-quote__cite">— {cite}</footer> : null}
    </blockquote>
  );
}

function Pull({ children, side = 'right' }) {
  return <aside className={`b-pull b-pull--${side}`}>{children}</aside>;
}

/* ---------- figures ---------- */

function Figure({ src, alt, caption, credit, frame = 'plain', size = 'md' }) {
  const isSvg = src && src.endsWith('.svg');
  return (
    <figure className={`b-figure b-figure--${size} b-figure--${frame}`}>
      <div className="b-figure__media">
        {src ? <img src={src} alt={alt || (caption ? flattenChildren(caption) : '')} /> : <div className="b-figure__ph">image</div>}
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

function Callout({ type = 'note', title, children }) {
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

function Aside({ children }) {
  return <aside className="b-aside">{children}</aside>;
}

/* ---------- table of contents (auto from rendered headings) ---------- */

function TOC({ minLevel = 2, maxLevel = 3, title }) {
  const { lang } = useBlog();
  const [items, setItems] = useState([]);
  const [active, setActive] = useState(null);
  useEffect(() => {
    const article = document.querySelector('.b-article');
    if (!article) return;
    const sel = Array.from({ length: maxLevel - minLevel + 1 }, (_, i) => `h${minLevel + i}[id]`).join(',');
    const update = () => {
      const found = Array.from(article.querySelectorAll(sel))
        .filter(el => !el.closest('.b-toc'))
        .map(el => ({ id: el.id, text: el.textContent.trim(), level: +el.tagName[1] }));
      setItems(found);
    };
    update();
    const mo = new MutationObserver(update);
    mo.observe(article, { childList: true, subtree: true });
    return () => mo.disconnect();
  }, [minLevel, maxLevel]);

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
  const heading = title ?? pickLocale({ en: 'Contents', zh: '目录' }, lang);
  return (
    <nav className="b-toc" aria-label={heading}>
      <div className="b-toc__title">{heading}</div>
      <ol className="b-toc__list">
        {items.map((it, i) => (
          <li key={it.id} className={`b-toc__item b-toc__item--l${it.level} ${active === it.id ? 'is-active' : ''}`}>
            <a href={`#${it.id}`} onClick={(e) => {
              e.preventDefault();
              const el = document.getElementById(it.id);
              if (el) {
                const y = el.getBoundingClientRect().top + window.scrollY - 80;
                window.scrollTo({ top: y, behavior: 'smooth' });
                history.replaceState(null, '', `${location.pathname}${location.hash.split('#')[1] ? '#' + location.hash.split('#')[1] : ''}`);
              }
            }}>
              <span className="b-toc__num">{String(i+1).padStart(2,'0')}</span>
              <span className="b-toc__text">{it.text}</span>
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}

/* ---------- columns / grid ---------- */

const Cols = ({ children, count = 2 }) => <div className={`b-cols b-cols--${count}`}>{children}</div>;
const Col = ({ children, span = 1 }) => <div className="b-col" style={{ '--col-span': span }}>{children}</div>;

/* ---------- tag ---------- */

const Tag = ({ children, href, tone }) => {
  const { navigate } = useBlog();
  const Cmp = href ? 'a' : 'span';
  return (
    <Cmp className={`b-tag ${tone ? `b-tag--${tone}` : ''}`} href={href}
      onClick={href ? (e) => { e.preventDefault(); navigate(href); } : undefined}>
      {children}
    </Cmp>
  );
};

/* ---------- definition / data table ---------- */

function Table({ headers, rows, caption }) {
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

/* ---------- progress + reveal ---------- */

function ReadingProgress() {
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

/* ---------- registry helpers ---------- */

window.Blog = window.Blog || {
  posts: [],
  byId: {},
  register(post) {
    if (!post || !post.id) return;
    if (this.byId[post.id]) return;
    this.byId[post.id] = post;
    this.posts.push(post);
  },
};

/* expose primitives globally for posts to consume */
Object.assign(window, {
  BlogContext, useBlog, useLang, useT, useTheme, useFormatDate, pickLocale, slugify,
  Article, Section, Spacer, Hr,
  H1, H2, H3, H4,
  P, Lead, Small, Strong, Em, InlineCode, Kbd, Mark,
  A, Ul, Ol, Li, Dl, Dt, Dd,
  Pre, Quote, Pull, Figure, Callout, Aside, TOC,
  Cols, Col, Tag, Table, ReadingProgress,
});
})();
