# OpenViking Blog

This directory contains the Valaxy site for `blog.openviking.dev`.

## Local development

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
```

The production output is written to `blog/dist`.

## TOS deployment

`.github/workflows/blog-tos.yml` builds this site and uploads `blog/dist` to TOS.

Required GitHub secret:

- `BLOG_TOS_BUCKET`

Shared with the docs deployment:

- `TOS_ACCESS_KEY`
- `TOS_SECRET_KEY`
- `TOS_REGION`
- `TOS_ENDPOINT`

Optional blog-specific overrides:

- `BLOG_TOS_REGION`
- `BLOG_TOS_ENDPOINT`
