import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import './posts/index.js';
import { BlogShell } from './shell-ui';
import { getAllPosts, getPostBySlug, pickLocale } from './blog-components';
import { SHELL_STRINGS, THEME_LIGHT, buildPath, makeFormatDate } from './shell-core';

export const SITE_URL = 'https://blog.openviking.ai';
export const SITE_SOCIAL_IMAGE = '/assets/covers/openviking-blog-social.png';
export const SITE_SOCIAL_IMAGE_WIDTH = 1200;
export const SITE_SOCIAL_IMAGE_HEIGHT = 630;

function noop() {}

function staticRouter(route, query = {}) {
  return {
    route,
    query,
    raw: buildPath(route, query),
    navigate: noop,
    setQuery: noop,
  };
}

function effectiveLangForPost(post, lang) {
  const supported = post?.meta?.languages || ['en'];
  return supported.includes(lang) ? lang : (supported[0] || 'en');
}

export function getStaticRoutes() {
  const routes = [{ path: '/', route: { name: 'index' }, lang: 'en', query: {} }];
  for (const post of getAllPosts()) {
    const route = { name: 'post', slug: post.id };
    routes.push({ path: buildPath(route), route, lang: effectiveLangForPost(post, 'en'), query: {} });
  }
  return routes;
}

export function renderRoute({ route, lang = 'en', query = {} }) {
  const S = SHELL_STRINGS[lang] || SHELL_STRINGS.en;
  const formatDate = makeFormatDate(lang);
  const t = (value) => pickLocale(value, lang);

  return renderToStaticMarkup(
    <BlogShell
      router={staticRouter(route, query)}
      lang={lang}
      theme={THEME_LIGHT}
      S={S}
      formatDate={formatDate}
      t={t}
    />
  );
}

export function getPageMeta({ route, lang = 'en' }) {
  const siteName = pickLocale({ en: 'OpenViking Blog', zh: 'OpenViking 博客' }, lang);
  const canonicalPath = buildPath(route, {});

  if (route.name === 'post') {
    const post = getPostBySlug(route.slug);
    const effectiveLang = effectiveLangForPost(post, lang);
    const meta = post?.meta || {};
    const title = pickLocale(meta.title, effectiveLang);
    const description = pickLocale(meta.description, effectiveLang);
    const cover = meta.cover || '/assets/logo.png';
    return {
      lang: effectiveLang,
      type: 'article',
      title: `${title} | ${siteName}`,
      description,
      canonical: `${SITE_URL}${canonicalPath}`,
      image: cover.startsWith('http') ? cover : `${SITE_URL}${cover}`,
      publishedAt: meta.publishedAt,
      updatedAt: meta.updatedAt,
      tags: meta.tags || [],
      authors: meta.authors || [],
      llmPath: meta.llmPath,
      sourceUrl: meta.sourceUrl,
      sourceTitle: meta.sourceTitle,
      sourceUpdatedAt: meta.sourceUpdatedAt,
    };
  }

  return {
    lang,
    type: 'website',
    title: siteName,
    description: pickLocale({
      en: 'Technical notes from the OpenViking team.',
      zh: 'OpenViking 团队的技术笔记。',
    }, lang),
    canonical: `${SITE_URL}/`,
    image: `${SITE_URL}${SITE_SOCIAL_IMAGE}`,
    imageWidth: SITE_SOCIAL_IMAGE_WIDTH,
    imageHeight: SITE_SOCIAL_IMAGE_HEIGHT,
    posts: getAllPosts().map(post => ({
      title: pickLocale(post.meta.title, lang),
      description: pickLocale(post.meta.description, lang),
      url: `${SITE_URL}${buildPath({ name: 'post', slug: post.id })}`,
      publishedAt: post.meta.publishedAt,
    })),
  };
}
