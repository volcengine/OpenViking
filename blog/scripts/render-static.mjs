import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { getPageMeta, getStaticRoutes, renderRoute, SITE_URL } from '../dist-ssr/ssg.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const blogRoot = path.resolve(here, '..');
const distDir = path.join(blogRoot, 'dist');
const ssrDir = path.join(blogRoot, 'dist-ssr');
const templatePath = path.join(distDir, 'index.html');
const template = await readFile(templatePath, 'utf8');

function escapeHtml(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function escapeAttr(value = '') {
  return escapeHtml(value).replaceAll('\n', ' ');
}

function jsonLd(meta) {
  if (meta.type === 'article') {
    const headline = meta.title.replace(/ \| OpenViking (Blog|博客)$/, '');
    return {
      '@context': 'https://schema.org',
      '@type': 'Article',
      headline,
      description: meta.description,
      image: meta.image,
      datePublished: meta.publishedAt,
      dateModified: meta.updatedAt || meta.publishedAt,
      author: meta.authors.map(author => ({
        '@type': 'Person',
        name: author.name,
        url: author.github ? `https://github.com/${author.github}` : undefined,
      })),
      publisher: {
        '@type': 'Organization',
        name: 'OpenViking',
        url: 'https://github.com/volcengine/OpenViking',
      },
      mainEntityOfPage: meta.canonical,
      keywords: meta.tags.join(', '),
    };
  }

  return {
    '@context': 'https://schema.org',
    '@type': 'Blog',
    name: 'OpenViking Blog',
    description: meta.description,
    url: meta.canonical,
    blogPost: meta.posts.map(post => ({
      '@type': 'BlogPosting',
      headline: post.title,
      description: post.description,
      url: post.url,
      datePublished: post.publishedAt,
    })),
  };
}

function managedHead(meta) {
  const title = escapeHtml(meta.title);
  const description = escapeAttr(meta.description);
  const image = escapeAttr(meta.image);
  const imageWidth = Number(meta.imageWidth) || 0;
  const imageHeight = Number(meta.imageHeight) || 0;
  const canonical = escapeAttr(meta.canonical);
  const llmUrl = meta.llmPath ? `${SITE_URL}${meta.llmPath}` : '';
  const ld = JSON.stringify(jsonLd(meta)).replaceAll('</script', '<\\/script');

  const tags = [
    `<title>${title}</title>`,
    `<meta name="description" content="${description}" />`,
    `<link rel="canonical" href="${canonical}" />`,
    `<meta property="og:type" content="${meta.type === 'article' ? 'article' : 'website'}" />`,
    `<meta property="og:site_name" content="OpenViking Blog" />`,
    `<meta property="og:title" content="${escapeAttr(meta.title)}" />`,
    `<meta property="og:description" content="${description}" />`,
    `<meta property="og:url" content="${canonical}" />`,
    `<meta property="og:image" content="${image}" />`,
    imageWidth ? `<meta property="og:image:width" content="${imageWidth}" />` : '',
    imageHeight ? `<meta property="og:image:height" content="${imageHeight}" />` : '',
    `<meta name="twitter:card" content="summary_large_image" />`,
    `<meta name="twitter:title" content="${escapeAttr(meta.title)}" />`,
    `<meta name="twitter:description" content="${description}" />`,
    `<meta name="twitter:image" content="${image}" />`,
    imageWidth ? `<meta name="twitter:image:width" content="${imageWidth}" />` : '',
    imageHeight ? `<meta name="twitter:image:height" content="${imageHeight}" />` : '',
  ].filter(Boolean);

  if (meta.llmPath) {
    tags.push(`<link rel="alternate" type="text/markdown" title="llm.txt" href="${escapeAttr(llmUrl)}" />`);
    tags.push(`<meta name="llm:content" content="${escapeAttr(llmUrl)}" />`);
  }

  if (meta.sourceUrl) {
    tags.push(`<meta name="source:lark" content="${escapeAttr(meta.sourceUrl)}" />`);
  }

  tags.push(`<script type="application/ld+json">${ld}</script>`);
  return tags.join('\n  ');
}

function injectPage({ html, meta, body }) {
  let out = html
    .replace(
      /<html[^>]*>/,
      `<html lang="${escapeAttr(meta.lang)}" data-theme="kami" class="blog-preference-booting">`
    )
    .replace(/\s*<title>[\s\S]*?<\/title>/, '')
    .replace(/\s*<meta name="description" content="[\s\S]*?" \/>/, '');

  out = out.replace(
    /(<meta name="viewport"[^>]*\/>)/,
    `$1\n  ${managedHead(meta)}`
  );

  out = out.replace(
    /<div id="root"><\/div>/,
    `<div id="root">${body}</div>`
  );

  return out;
}

function outputPath(routePath) {
  if (routePath === '/') return path.join(distDir, 'index.html');
  return path.join(distDir, routePath.replace(/^\/+/, ''), 'index.html');
}

async function writeRoute(routeConfig) {
  const meta = getPageMeta(routeConfig);
  const body = renderRoute(routeConfig);
  const page = injectPage({ html: template, meta, body });
  const file = outputPath(routeConfig.path);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, page);
}

async function writeSitemap(routes) {
  const urls = routes.map(route => {
    const meta = getPageMeta(route);
    return [
      '  <url>',
      `    <loc>${escapeHtml(meta.canonical)}</loc>`,
      route.route.name === 'post' && meta.updatedAt ? `    <lastmod>${escapeHtml(meta.updatedAt)}</lastmod>` : '',
      '  </url>',
    ].filter(Boolean).join('\n');
  }).join('\n');

  await writeFile(path.join(distDir, 'sitemap.xml'), `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`);
}

async function writeRobots() {
  await writeFile(path.join(distDir, 'robots.txt'), `User-agent: *\nAllow: /\nSitemap: ${SITE_URL}/sitemap.xml\n`);
}

async function writeLlms(routes) {
  const postRoutes = routes.filter(route => route.route.name === 'post');
  const lines = [
    '# OpenViking Blog',
    '',
    'Technical notes from the OpenViking team on agents, protocols, and systems.',
    '',
    '## Posts',
    '',
    ...postRoutes.map(route => {
      const meta = getPageMeta(route);
      const title = meta.title.replace(/ \| OpenViking (Blog|博客)$/, '');
      const llm = meta.llmPath ? ` Agent-readable: ${SITE_URL}${meta.llmPath}.` : '';
      const source = meta.sourceUrl ? ` Source: ${meta.sourceUrl}.` : '';
      return `- [${title}](${meta.canonical}) - ${meta.description}${llm}${source}`;
    }),
    '',
  ];
  await writeFile(path.join(distDir, 'llms.txt'), lines.join('\n'));
}

const routes = getStaticRoutes();
for (const route of routes) await writeRoute(route);
await writeSitemap(routes);
await writeRobots();
await writeLlms(routes);
await rm(ssrDir, { recursive: true, force: true });

console.log(`Rendered ${routes.length} static blog routes.`);
