# OpenViking Blog Agent Notes

Read `design.md` before changing blog content or shell UI. The notes below are the short operational rules that have caused review issues before.

## Post Assets

- Keep post-specific figures inside a topic-scoped folder. Do not scatter a post's screenshots or diagrams across generic public folders.
- Preferred paths:
  - Source-bundled article assets: `src/posts/<slug>/assets/<descriptive-name>.<ext>` and import them from the post component.
  - Public static assets that must keep a stable URL: `public/post/<slug>/images/<descriptive-name>.<ext>`.
  - Shared covers reused across cards or posts: `public/assets/covers/<slug>.<ext>`.
- Use descriptive names or a clearly ordered figure series, such as `figure-01-setup.jpg` or `figure-01.jpg` through `figure-NN.jpg` within that post's own folder.
- Avoid hotlinking source images. Localize, resize, and compress images before opening a PR.
- Every visible figure needs localized `alt` and localized `caption`.
- When turning public materials or local authoring references into a blog post, do not add visible credit/source lines by default. Add public source attribution only when the post explicitly depends on a public source as source material, when the user asks for attribution, or when licensing/compliance requires it.
- Before committing screenshots, check for secrets, API keys, internal domains, local-only URLs, personal paths, user identifiers, and private chat content.

## Public Boundary

- Local references such as `localhost` pages are authoring inputs, not public sources. Do not put them into public metadata, captions, `llm.txt`, or `/llms.txt`.
- Keep the human article and `llm.txt` aligned. The agent-readable file can be plainer, but it must not expose internal details removed from the public article.

## Writing Style

- Chinese copy should sound like clear product/engineering writing, not a literal translation from English.
- Prefer direct sentences such as "部署非常简单" over translated phrases such as "参考部署故意保持简单".
- Avoid repetitive "不是 X，而是 Y" framing when a direct statement is clearer.
