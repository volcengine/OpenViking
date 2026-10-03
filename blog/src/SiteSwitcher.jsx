import React, { useEffect, useRef, useState } from 'react';
import './site-switcher.css';

export default function SiteSwitcher({ lang, dark, onHome }) {
  const zh = lang === 'zh';
  const root = useRef(null), trigger = useRef(null), panel = useRef(null);
  const timer = useRef(null), hoverOpened = useRef(false);
  const [open, setOpen] = useState(false);
  const clear = () => clearTimeout(timer.current);
  const close = (restoreFocus = false) => {
    clear(); setOpen(false); hoverOpened.current = false;
    if (restoreFocus) trigger.current?.focus();
  };
  useEffect(() => {
    const outside = event => { if (!root.current?.contains(event.target)) close(); };
    const route = () => close();
    document.addEventListener('pointerdown', outside);
    window.addEventListener('popstate', route);
    window.addEventListener('hashchange', route);
    return () => {
      clear(); document.removeEventListener('pointerdown', outside);
      window.removeEventListener('popstate', route); window.removeEventListener('hashchange', route);
    };
  }, []);
  useEffect(() => close(), [lang]);
  const enter = event => {
    clear();
    if (event.pointerType !== 'mouse' || !matchMedia('(hover: hover) and (pointer: fine)').matches || open) return;
    timer.current = setTimeout(() => { hoverOpened.current = true; setOpen(true); }, 120);
  };
  const leave = () => {
    clear(); timer.current = setTimeout(() => { if (!root.current?.contains(document.activeElement)) close(); }, 180);
  };
  const toggle = () => {
    clear();
    if (hoverOpened.current) { hoverOpened.current = false; return; }
    setOpen(value => !value);
  };
  const keydown = event => {
    if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); close(true); return; }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const links = [...panel.current.querySelectorAll('a')];
    const index = links.indexOf(document.activeElement);
    if (document.activeElement !== trigger.current && index < 0) return;
    event.preventDefault(); clear(); hoverOpened.current = false; setOpen(true);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? links.length - 1
      : index < 0 ? (event.key === 'ArrowUp' ? links.length - 1 : 0)
      : (index + (event.key === 'ArrowDown' ? 1 : -1) + links.length) % links.length;
    requestAnimationFrame(() => links[next]?.focus());
  };
  const goHome = event => { close(); if (onHome) { event.preventDefault(); onHome(); } };
  const sites = [
    { label: zh ? '官网' : 'Website', description: zh ? '认识 OpenViking' : 'Discover OpenViking', href: 'https://www.openviking.ai/', icon: 'globe' },
    { label: 'Docs', description: zh ? '学习、接入与开发' : 'Learn, integrate and build', href: `https://docs.openviking.ai/${zh ? 'zh' : 'en'}/`, icon: 'book' },
    { label: 'Blog', description: zh ? '产品进展与实践' : 'Updates and field notes', href: '/', icon: 'article' },
    { label: 'GitHub', description: zh ? '源码、Issues 与贡献' : 'Source, issues and contributions', href: 'https://github.com/volcengine/OpenViking', icon: 'github' },
  ];
  return <div className="ov-site-switcher" ref={root} onPointerEnter={enter} onPointerLeave={leave}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) close(); }} onKeyDown={keydown}>
    <a className="ov-brand-link" href="/" aria-label={zh ? 'OpenViking 博客首页' : 'OpenViking blog home'} onClick={goHome}>
      <img src={dark ? '/assets/brand-lockup-dark.svg' : '/assets/brand-lockup-light.svg'} alt="OpenViking" width="136" height="26" loading="eager" decoding="async" fetchpriority="high" />
    </a>
    <span className="ov-brand-divider" aria-hidden="true">/</span>
    <button ref={trigger} type="button" className="ov-site-trigger" aria-label={zh ? '切换站点' : 'Switch site'} aria-expanded={open} aria-controls="ov-site-links" onClick={toggle}>
      <span>Blog</span><svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg>
    </button>
    <nav id="ov-site-links" ref={panel} className="ov-site-panel" style={{ display: open ? undefined : 'none' }} aria-label={zh ? 'OpenViking 站点' : 'OpenViking sites'}>
      {sites.map(site => <a key={site.icon} href={site.href} className={`ov-site-option${site.icon === 'article' ? ' is-current' : ''}`} aria-current={site.icon === 'article' ? 'true' : undefined} onClick={site.icon === 'article' ? goHome : () => close()}>
        <span className="ov-site-icon" aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          {site.icon === 'github' ? <path fill="currentColor" stroke="none" d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61-.546-1.385-1.335-1.755-1.335-1.755-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 21.795 24 17.295 24 12c0-6.63-5.37-12-12-12z"/> : site.icon === 'globe' ? <><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z"/></> : site.icon === 'book' ? <path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15"/> : <><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/></>}
        </svg></span>
        <span className="ov-site-copy"><span className="ov-site-name">{site.label}</span><span className="ov-site-description">{site.description}</span></span>
        {site.icon === 'article' ? <svg className="ov-site-status" width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m4 10 4 4 8-8"/></svg> : <span className="ov-site-arrow" aria-hidden="true">↗</span>}
      </a>)}
    </nav>
  </div>;
}
