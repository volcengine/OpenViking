/* shell-ui.jsx — visual shell: topbar, hero, list, post view, footer.
 * Reads from registry + router via shell-core.
 */
(function () {
const { useState, useEffect, useMemo, useCallback } = React;

/* ---------- topbar ---------- */
function Topbar({ lang, theme, onLang, onTheme, onHome, S }) {
  const [open, setOpen] = useState(false);
  const ref = React.useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open]);
  const current = THEMES.find(t => t.id === theme) || THEMES[0];
  return (
    <header className="b-topbar">
      <div className="b-topbar__inner">
        <div className="b-brand" onClick={onHome} role="button" tabIndex={0}>
          <div className="b-brand__mark">B</div>
          <span className="b-brand__name">{S.siteName}</span>
          <span className="b-brand__sub">// {S.siteSub}</span>
        </div>
        <div className="b-topbar__nav">
          <div className="b-seg" role="tablist" aria-label={S.langLabel}>
            {LANGS.map(l => (
              <button key={l.code} className={lang === l.code ? 'is-active' : ''} onClick={() => onLang(l.code)}>{l.short}</button>
            ))}
          </div>
          <div className="b-tm" ref={ref}>
            <button
              className="b-tm__trigger"
              aria-label={S.themeLabel}
              aria-haspopup="listbox"
              aria-expanded={open}
              title={pickLocale(current.label, lang)}
              onClick={() => setOpen(o => !o)}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 22a10 10 0 1 1 10-10c0 2.5-2 4-4 4h-2a2 2 0 0 0-1.5 3.3A2 2 0 0 1 12 22z"/>
                <circle cx="7.5" cy="10.5" r="1.2" fill="currentColor" stroke="none"/>
                <circle cx="12" cy="7" r="1.2" fill="currentColor" stroke="none"/>
                <circle cx="16.5" cy="10.5" r="1.2" fill="currentColor" stroke="none"/>
              </svg>
              <span className="b-tm__label">{pickLocale(current.label, lang)}</span>
              <svg className="b-tm__caret" width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                <path d="M2 3.5 L5 7 L8 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            {open && (
              <div className="b-tm__menu" role="listbox" aria-label={S.themeLabel}>
                {THEMES.map(t => (
                  <button
                    key={t.id}
                    className={`b-tm__item b-tm__item--${t.id} ${theme === t.id ? 'is-active' : ''}`}
                    role="option"
                    aria-selected={theme === t.id}
                    onClick={() => { onTheme(t.id); setOpen(false); }}>
                    <span className={`b-tm__sw b-tm__sw--${t.id}`} aria-hidden="true" />
                    <span className="b-tm__txt">
                      <span className="b-tm__name">{pickLocale(t.label, lang)}</span>
                      <span className="b-tm__blurb">{pickLocale(t.blurb, lang)}</span>
                    </span>
                    {theme === t.id && (
                      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                        <path d="M2.5 6.5 L5 9 L9.5 3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}

/* ---------- index hero + list ---------- */
function IndexView({ lang, t, theme, navigate, S, formatDate }) {
  const all = useMemo(() => getAllPosts(), []);
  const tags = useMemo(() => getAllTags(), []);
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('newest');

  const list = useMemo(() => {
    let xs = filter === 'all' ? all : all.filter(p => (p.meta.tags || []).includes(filter));
    if (sort === 'oldest') xs = [...xs].reverse();
    return xs;
  }, [all, filter, sort]);

  const featured = list[0];
  const rest = list.slice(1);

  return (
    <main className="b-shell__main">
      <section className="b-hero">
        <div className="b-hero__eyebrow">{S.indexEyebrow}</div>
        <h1 className="b-hero__title">{S.indexTitle}</h1>
        <p className="b-hero__lede">{S.indexLede}</p>
        <div className="b-hero__meta">
          <span className="b-hero__count">{S.countLabel(all.length)}</span>
          <span>{LANGS.length} {lang === 'zh' ? '语言' : 'languages'}</span>
          <span>{THEMES.length} {lang === 'zh' ? '主题' : 'themes'}</span>
        </div>
      </section>

      <section className="b-list">
        <div className="b-list__bar">
          <div className="b-list__filters">
            <button className={`b-list__filter ${filter === 'all' ? 'is-active' : ''}`} onClick={() => setFilter('all')}>{S.filterAll}</button>
            {tags.map(tg => (
              <button key={tg} className={`b-list__filter ${filter === tg ? 'is-active' : ''}`} onClick={() => setFilter(tg)}>#{tg}</button>
            ))}
          </div>
          <div className="b-list__sort">
            <button className="b-list__filter" onClick={() => setSort(sort === 'newest' ? 'oldest' : 'newest')}>
              {sort === 'newest' ? S.sortNewest : S.sortOldest} ↕
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
  const cover = m.cover;
  const author = (m.authors || [])[0];
  const open = () => navigate(`#/post/${post.id}`);
  return (
    <article className={`b-card ${featured ? 'b-card--featured' : ''}`} onClick={open} role="button" tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') open(); }}>
      {cover ? <div className="b-card__cover"><img src={cover} alt="" /></div> : null}
      <div className="b-card__body">
        <div className="b-card__meta">
          <span>{formatDate(m.publishedAt)}</span>
          {m.readingTime ? <span>{S.readingTime(m.readingTime)}</span> : null}
          {m.category ? <span>{pickLocale(m.category, lang)}</span> : null}
        </div>
        <h2 className="b-card__title">{title}</h2>
        {excerpt ? <p className="b-card__excerpt">{excerpt}</p> : null}
        <div className="b-card__foot">
          {author ? (
            <div className="b-card__author">
              {author.avatar ? <img src={author.avatar} className="b-card__avatar" alt="" /> : null}
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
    </article>
  );
}

/* ---------- post view ---------- */
function PostView({ slug, lang, theme, navigate, S, formatDate, t }) {
  const post = getPostBySlug(slug);
  if (!post) return <NotFound S={S} navigate={navigate} />;
  const m = post.meta;
  const supported = m.languages || ['en'];
  const effectiveLang = supported.includes(lang) ? lang : (supported[0] || 'en');
  const langMissing = effectiveLang !== lang;

  const Component = post.Component;
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
        {m.cover ? <div className="b-post__cover"><img src={m.cover} alt="" /></div> : null}
      </div>

      <header className="b-post__head">
        <div className="b-post__eyebrow">
          <a className="b-a" href="#/" onClick={(e) => { e.preventDefault(); navigate('#/'); }}>{S.backToIndex}</a>
          {m.category ? <span>· {pickLocale(m.category, lang)}</span> : null}
        </div>
        <h1 className="b-post__title">{pickLocale(m.title, effectiveLang)}</h1>
        {m.description ? <p className="b-post__sub">{pickLocale(m.description, effectiveLang)}</p> : null}
        <div className="b-post__byline">
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {(m.authors || []).map(a => (
              <div key={a.name} className="b-author">
                {a.avatar ? <img src={a.avatar} className="b-author__avatar" alt="" /> : null}
                <div>
                  <div className="b-author__name">
                    {a.github ? <a href={`https://github.com/${a.github}`} target="_blank" rel="noreferrer">{a.name} ↗</a> : a.name}
                  </div>
                  {a.role ? <div className="b-author__role">{pickLocale(a.role, effectiveLang)}</div> : null}
                </div>
              </div>
            ))}
          </div>
          <div className="b-post__times">
            <span><b>{S.publishedOn}</b> {formatDate(m.publishedAt)}</span>
            {m.updatedAt ? <span><b>{S.updatedOn}</b> {formatDate(m.updatedAt)}</span> : null}
            {m.readingTime ? <span>{S.readingTime(m.readingTime)}</span> : null}
          </div>
        </div>
        {langMissing ? (
          <div className="b-callout b-callout--info" style={{ marginBottom: 32 }}>
            <div className="b-callout__label">i18n</div>
            <div className="b-callout__body"><p className="b-p">{S.notAvailableLang}</p></div>
          </div>
        ) : null}
      </header>

      <div className="b-post__layout">
        <aside className="b-post__sidebar">
          <TOC title={S.contents} />
        </aside>
        <div className="b-post__body">
          <BlogContext.Provider value={ctx}>
            <Component {...ctx} />
          </BlogContext.Provider>
        </div>
      </div>

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
  return (
    <a className={`b-post__navcard b-post__navcard--${dir}`} href={`#/post/${post.id}`}
       onClick={(e) => { e.preventDefault(); navigate(`#/post/${post.id}`); }}>
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
        <a className="b-a" href="#/" onClick={(e) => { e.preventDefault(); navigate('#/'); }}>{S.backToIndex}</a>
      </section>
    </main>
  );
}

/* ---------- footer ---------- */
function Footer({ lang, S }) {
  return (
    <footer className="b-footer">
      <div className="b-footer__cols">
        <span>© 2026 {S.siteName}</span>
        <span>·</span>
        <span>{lang === 'zh' ? '使用 React + 五种主题搭建' : 'Built with React + five themes'}</span>
      </div>
      <div className="b-footer__cols">
        <a className="b-a" href="#/">{S.backToIndex.replace('← ', '')}</a>
        <span>{lang === 'zh' ? '订阅' : 'Subscribe'}</span>
        <span>RSS</span>
      </div>
    </footer>
  );
}

/* ---------- root app ---------- */
function App() {
  const router = useHashRouter();
  // Lang/theme: query > localStorage > default
  const initialLang = router.query.lang || localStorage.getItem('blog.lang') || 'en';
  const initialTheme = router.query.theme || localStorage.getItem('blog.theme') || 'folio';
  const [lang, setLang] = useState(LANGS.some(l => l.code === initialLang) ? initialLang : 'en');
  const [theme, setTheme] = useState(THEMES.some(t => t.id === initialTheme) ? initialTheme : 'folio');

  useEffect(() => { applyTheme(theme); localStorage.setItem('blog.theme', theme); }, [theme]);
  useEffect(() => { document.documentElement.lang = lang; localStorage.setItem('blog.lang', lang); }, [lang]);

  // Sync URL ↔ state when query changes externally
  useEffect(() => {
    if (router.query.lang && router.query.lang !== lang) setLang(router.query.lang);
    if (router.query.theme && router.query.theme !== theme) setTheme(router.query.theme);
  }, [router.query.lang, router.query.theme]);

  // When user changes lang/theme, reflect in URL (so deep-links work)
  const onLang = (code) => { setLang(code); router.setQuery({ lang: code }); };
  const onTheme = (id) => { setTheme(id); router.setQuery({ theme: id }); };
  const onHome = () => router.navigate('#/');

  // Scroll to top on route change
  useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' }); }, [router.route.name, router.route.slug]);

  const S = useShellStrings(lang);
  const formatDate = useMemo(() => makeFormatDate(lang), [lang]);
  const t = (m) => pickLocale(m, lang);

  return (
    <div className="b-shell">
      <Topbar lang={lang} theme={theme} onLang={onLang} onTheme={onTheme} onHome={onHome} S={S} />
      {router.route.name === 'index'
        ? <IndexView lang={lang} t={t} theme={theme} navigate={router.navigate} S={S} formatDate={formatDate} />
        : <PostView slug={router.route.slug} lang={lang} theme={theme} navigate={router.navigate} S={S} formatDate={formatDate} t={t} />}
      <Footer lang={lang} S={S} />
    </div>
  );
}

window.BlogApp = App;
})();
