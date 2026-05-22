import React, { useState, useEffect, useMemo, useRef } from 'react';
import {
  BlogContext, ExternalArrowIcon, pickLocale, ReadingProgress, TOC,
  getAllPosts, getAllTags, getPostBySlug, neighbors,
} from './blog-components';
import {
  LANGS, THEME_LIGHT, THEME_DARK,
  useSiteRouter, useShellStrings, makeFormatDate, applyTheme, getInitialTheme, isDark,
  buildPath, postPath, estimateReadingMinutes,
} from './shell-core';
import { ZoukInteractiveBlog } from './zouk-embed';

/* ---------- topbar ---------- */

function Topbar({ lang, theme, onLang, onToggleTheme, onHome, S }) {
  const dark = isDark(theme);
  return (
    <header className="b-topbar">
      <div className="b-topbar__inner">
        <a className="b-brand" href="/" onClick={(e) => { e.preventDefault(); onHome(); }}>
          <img
            className="b-brand__mark"
            src="/assets/logo.png"
            alt="OpenViking"
            loading="eager"
            decoding="async"
            fetchpriority="high"
          />
          <span className="b-brand__name">{S.siteName}</span>
          <span className="b-brand__sub">// {S.siteSub}</span>
        </a>
        <div className="b-topbar__nav">
          <div className="b-seg" role="tablist" aria-label={S.langLabel}>
            {LANGS.map(l => (
              <button key={l.code} className={lang === l.code ? 'is-active' : ''} onClick={() => onLang(l.code)}>{l.short}</button>
            ))}
          </div>
          <button
            className="b-mode-toggle"
            aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
            onClick={onToggleTheme}>
            {dark ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5"/>
                <line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
                <line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
              </svg>
            )}
          </button>
          <div id="zouk-reader-header-slot" className="zouk-reader-header-slot" />
        </div>
      </div>
    </header>
  );
}

/* ---------- index hero + list ---------- */

export function IndexView({ lang, t, theme, navigate, S, formatDate }) {
  const all = useMemo(() => getAllPosts(), []);
  const tags = useMemo(() => getAllTags(), []);
  const [filter, setFilter] = useState('all');
  const [filtersExpanded, setFiltersExpanded] = useState(false);
  const [sort, setSort] = useState('newest');
  const compactFilters = useMemo(() => ['all', ...tags.slice(0, 2)], [tags]);

  const list = useMemo(() => {
    let xs = filter === 'all' ? all : all.filter(p => (p.meta.tags || []).includes(filter));
    if (sort === 'oldest') xs = [...xs].reverse();
    return xs;
  }, [all, filter, sort]);

  const featured = list[0];
  const rest = list.slice(1);
  const selectFilter = (next) => {
    setFilter(next);
    setFiltersExpanded(!compactFilters.includes(next));
  };

  return (
    <main className="b-shell__main">
      <section className="b-hero">
        <div className="b-hero__eyebrow">{S.indexEyebrow}</div>
        <h1 className="b-hero__title">{S.indexTitle}</h1>
        <p className="b-hero__lede">{S.indexLede}</p>
        <div className="b-hero__meta">
          <span className="b-hero__count">{S.countLabel(all.length)}</span>
          <span>{S.indexFocus}</span>
          <span>{S.indexCadence}</span>
        </div>
      </section>

      <section className="b-list">
        <div className="b-list__bar">
          <div className={`b-list__filters ${filtersExpanded ? 'is-expanded' : ''}`}>
            <button className={`b-list__filter ${filter === 'all' ? 'is-active' : ''}`} onClick={() => selectFilter('all')}>{S.filterAll}</button>
            {tags.map(tg => (
              <button key={tg} className={`b-list__filter ${filter === tg ? 'is-active' : ''}`} onClick={() => selectFilter(tg)}>#{tg}</button>
            ))}
          </div>
          <button
            type="button"
            className="b-list__filter b-list__more"
            aria-expanded={filtersExpanded}
            onClick={() => setFiltersExpanded(v => !v)}
          >
            {filtersExpanded ? S.filterLess : S.filterMore}
          </button>
          <div className="b-list__sort">
            <button className="b-list__filter" onClick={() => setSort(sort === 'newest' ? 'oldest' : 'newest')}>
              <span className="b-list__sort-full">{sort === 'newest' ? S.sortNewest : S.sortOldest}</span>
              <span className="b-list__sort-short">{sort === 'newest' ? S.sortNewestShort : S.sortOldestShort}</span> ↕
            </button>
          </div>
        </div>

        <div className="b-card-grid">
          {featured ? <PostCard post={featured} lang={lang} navigate={navigate} S={S} formatDate={formatDate} featured /> : null}
          {rest.map(p => (
            <PostCard key={p.id} post={p} lang={lang} navigate={navigate} S={S} formatDate={formatDate} />
          ))}
        </div>
      </section>
    </main>
  );
}

function PostCard({ post, lang, navigate, S, formatDate, featured }) {
  const m = post.meta;
  const title = pickLocale(m.title, lang);
  const excerpt = pickLocale(m.description, lang);
  const readingTime = pickLocale(m.readingTime, lang);
  const cover = m.cardCover || m.cover;
  const author = (m.authors || [])[0];
  const href = postPath(post.id);
  const open = (e) => {
    e.preventDefault();
    navigate(href);
  };
  return (
    <a className={`b-card ${featured ? 'b-card--featured' : ''}`} href={href} onClick={open}>
      {cover ? (
        <div className="b-card__cover">
          <img src={cover} alt="" loading="lazy" decoding="async" />
        </div>
      ) : null}
      <div className="b-card__body">
        <div className="b-card__meta">
          <span>{formatDate(m.publishedAt)}</span>
          {readingTime ? <span>{S.readingTime(readingTime)}</span> : null}
          {m.category ? <span>{pickLocale(m.category, lang)}</span> : null}
        </div>
        <h2 className="b-card__title">{title}</h2>
        {excerpt ? <p className="b-card__excerpt">{excerpt}</p> : null}
        <div className="b-card__foot">
          {author ? (
            <div className="b-card__author">
              {author.avatar ? (
                <img src={author.avatar} className="b-card__avatar" alt="" loading="lazy" decoding="async" />
              ) : null}
              <span>{author.name}</span>
            </div>
          ) : <span/>}
          {m.tags?.length ? (
            <div className="b-card__tags">
              {m.tags.slice(0, 2).map(t => <span key={t} className="b-tag">#{t}</span>)}
            </div>
          ) : null}
        </div>
      </div>
    </a>
  );
}

/* ---------- post view ---------- */

export function PostView({ slug, lang, theme, navigate, S, formatDate, t }) {
  const post = getPostBySlug(slug);
  if (!post) return <NotFound S={S} navigate={navigate} />;
  const m = post.meta;
  const bodyRef = useRef(null);
  const supported = m.languages || ['en'];
  const effectiveLang = supported.includes(lang) ? lang : (supported[0] || 'en');
  const langMissing = effectiveLang !== lang;
  const fallbackReadingTime = pickLocale(m.readingTime, effectiveLang) || null;
  const [readingTime, setReadingTime] = useState(fallbackReadingTime);

  useEffect(() => {
    setReadingTime(fallbackReadingTime);
  }, [fallbackReadingTime, slug, effectiveLang]);

  useEffect(() => {
    const text = bodyRef.current?.innerText || '';
    const next = estimateReadingMinutes(text, effectiveLang);
    if (next) setReadingTime(next);
  }, [slug, effectiveLang]);

  const Component = post.Component;
  const postS = useShellStrings(effectiveLang);
  const ctx = {
    lang: effectiveLang,
    theme,
    fallbackLang: 'en',
    t: (m) => pickLocale(m, effectiveLang),
    formatDate: makeFormatDate(effectiveLang),
    navigate,
    postSlug: slug,
  };

  const { prev, next } = neighbors(slug);

  return (
    <main className="b-shell__main b-post">
      <ReadingProgress />

      <div className="b-post__hero">
        {m.cover ? (
          <div className="b-post__cover">
            <img src={m.cover} alt="" loading="eager" decoding="async" fetchpriority="high" />
          </div>
        ) : null}
      </div>

      <header className="b-post__head">
        <div className="b-post__eyebrow">
          <a className="b-a" href="/" onClick={(e) => { e.preventDefault(); navigate('/'); }}>{S.backToIndex}</a>
          {m.category ? <span>· {pickLocale(m.category, lang)}</span> : null}
        </div>
        <h1 className="b-post__title">{pickLocale(m.title, effectiveLang)}</h1>
        {m.description ? <p className="b-post__sub">{pickLocale(m.description, effectiveLang)}</p> : null}
        <div className="b-post__byline">
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {(m.authors || []).map(a => (
              <div key={a.name} className="b-author">
                {a.avatar ? (
                  <img src={a.avatar} className="b-author__avatar" alt="" loading="lazy" decoding="async" />
                ) : null}
                <div>
                  <div className="b-author__name">
                    {a.github ? <a href={`https://github.com/${a.github}`} target="_blank" rel="noreferrer">{a.name}<ExternalArrowIcon /></a> : a.name}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div className="b-post__times">
            <span><b>{S.publishedOn}</b> {formatDate(m.publishedAt)}</span>
            {m.updatedAt ? <span><b>{S.updatedOn}</b> {formatDate(m.updatedAt)}</span> : null}
            {readingTime ? <span>{S.readingTime(readingTime)}</span> : null}
          </div>
        </div>
        {langMissing ? (
          <div className="b-callout b-callout--info" style={{ marginBottom: 32 }}>
            <div className="b-callout__label">i18n</div>
            <div className="b-callout__body"><p className="b-p">{S.notAvailableLang}</p></div>
          </div>
        ) : null}
      </header>

      <BlogContext.Provider value={ctx}>
        <div className="b-post__layout">
          <aside className="b-post__sidebar">
            <TOC key={`${slug}:${effectiveLang}`} title={postS.contents} lang={effectiveLang} foldable={false} />
          </aside>
          <div className="b-post__body" ref={bodyRef}>
            <Component {...ctx} />
          </div>
        </div>
      </BlogContext.Provider>

      <footer className="b-post__foot">
        {m.tags?.length ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
            <span style={{ fontFamily: 'var(--th-font-mono)', fontSize: 11, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--th-mute)' }}>{S.tags}</span>
            {m.tags.map(tg => <span key={tg} className="b-tag">#{tg}</span>)}
          </div>
        ) : null}
        <div className="b-post__nav">
          {prev ? <NavCard post={prev} dir="prev" lang={effectiveLang} S={S} navigate={navigate} /> : <div/>}
          {next ? <NavCard post={next} dir="next" lang={effectiveLang} S={S} navigate={navigate} /> : <div/>}
        </div>
      </footer>
    </main>
  );
}

function NavCard({ post, dir, lang, S, navigate }) {
  const href = postPath(post.id);
  return (
    <a className={`b-post__navcard b-post__navcard--${dir}`} href={href}
       onClick={(e) => { e.preventDefault(); navigate(href); }}>
      <div className="b-post__navcard__dir">{dir === 'prev' ? `← ${S.prev}` : `${S.next} →`}</div>
      <div className="b-post__navcard__title">{pickLocale(post.meta.title, lang)}</div>
    </a>
  );
}

function NotFound({ S, navigate }) {
  return (
    <main className="b-shell__main">
      <section className="b-hero">
        <div className="b-hero__eyebrow">404</div>
        <h1 className="b-hero__title">{S.notFoundTitle}</h1>
        <p className="b-hero__lede">{S.notFoundBody}</p>
        <a className="b-a" href="/" onClick={(e) => { e.preventDefault(); navigate('/'); }}>{S.backToIndex}</a>
      </section>
    </main>
  );
}

/* ---------- footer ---------- */

function Footer({ S }) {
  return (
    <footer className="b-footer">
      <span>© 2026 {S.siteName}</span>
      <div className="b-footer__icons">
        <a href="https://github.com/volcengine/openviking" target="_blank" rel="noreferrer" aria-label="GitHub">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61-.546-1.385-1.335-1.755-1.335-1.755-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 21.795 24 17.295 24 12c0-6.63-5.37-12-12-12z"/></svg>
        </a>
        <a href="https://x.com/openvikingai" target="_blank" rel="noreferrer" aria-label="X">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>
        </a>
      </div>
    </footer>
  );
}

/* ---------- root app ---------- */

export function BlogShell({ router, lang, theme, onLang = () => {}, onToggleTheme = () => {}, S, formatDate, t }) {
  const onHome = () => router.navigate(buildPath({ name: 'index' }, router.query));

  return (
    <div className="b-shell">
      <Topbar lang={lang} theme={theme} onLang={onLang} onToggleTheme={onToggleTheme} onHome={onHome} S={S} />
      {router.route.name === 'index'
        ? <IndexView lang={lang} t={t} theme={theme} navigate={router.navigate} S={S} formatDate={formatDate} />
        : <PostView slug={router.route.slug} lang={lang} theme={theme} navigate={router.navigate} S={S} formatDate={formatDate} t={t} />}
      <Footer S={S} />
      <ZoukInteractiveBlog route={router.route} />
    </div>
  );
}

export default function App() {
  const router = useSiteRouter();
  const initialLang = router.query.lang || localStorage.getItem('blog.lang') || 'en';
  const [lang, setLang] = useState(LANGS.some(l => l.code === initialLang) ? initialLang : 'en');
  const [theme, setTheme] = useState(getInitialTheme);

  useEffect(() => { applyTheme(theme); localStorage.setItem('blog.theme', theme); }, [theme]);
  useEffect(() => { document.documentElement.lang = lang; localStorage.setItem('blog.lang', lang); }, [lang]);

  useEffect(() => {
    if (router.query.lang && router.query.lang !== lang) setLang(router.query.lang);
  }, [router.query.lang]);

  const onLang = (code) => { setLang(code); router.setQuery({ lang: code }); };
  const onToggleTheme = () => setTheme(t => t === THEME_LIGHT ? THEME_DARK : THEME_LIGHT);

  useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' }); }, [router.route.name, router.route.slug]);

  const S = useShellStrings(lang);
  const formatDate = useMemo(() => makeFormatDate(lang), [lang]);
  const t = (m) => pickLocale(m, lang);

  return <BlogShell router={router} lang={lang} theme={theme} onLang={onLang} onToggleTheme={onToggleTheme} S={S} formatDate={formatDate} t={t} />;
}
