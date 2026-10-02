# Missing pages

The blog renders a missing-page view for unknown paths and missing post slugs.
It shares the blog's language selector, light/dark palette and header, with links
back to all essays, the docs and the latest essay. Recovery links use full-page
navigation so the destination loads its own metadata. The assistant widget is
not mounted on error pages.

`npm run build` emits `dist/404.html` with rendered content and `noindex, follow`.
Its recovery links work without JavaScript. It has no canonical URL or structured
article data and is excluded from the sitemap and LLM article list.

## Hosting contract

The existing workflow uploads all generated HTML, including `404.html`, to TOS.
The bucket's static website error document must be set to `404.html`. Missing
objects must return this body with HTTP **404**, retaining the requested URL;
do not redirect to `/404.html` or rewrite errors to a 200 home page. Preserve
existing index-document and routing settings when updating the bucket config.
If a CDN overrides origin errors, configure it to pass through this response.

The bucket/CDN settings are outside this repository and are not changed by this
PR. A build or Vite preview is not proof that the production error mapping works.
After deployment, check an unknown path and `/post/does-not-exist/` on the public
domain: both should return HTTP 404 with the designed page, including on refresh.
An existing article must still return HTTP 200.

## Checks

Run `npm test`, `npm run build`, and `npm run check:404`. For browser QA, serve
the built files with a server that uses `404.html` as its error document and
preserves status 404; Vite's SPA fallback alone does not test this contract.
Check missing paths/slugs, both languages and themes, mobile widths, keyboard
focus, and each recovery link. Language changes must retain the unknown path.
