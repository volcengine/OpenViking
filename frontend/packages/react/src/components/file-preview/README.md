# FilePreview

English / [中文](README_CN.md)

Console-style file tabs, breadcrumbs, content levels, image preview, source view and editing. Import `FilePreview` and its types from `@openviking/react/components/file-preview`; import the standalone CSS explicitly from `@openviking/react/components/file-preview/style.css`.

File tabs accept `source`, optional `levels` (L0/L1/L2 or host-defined labels), and `resolveImage(src, source)` for authenticated or relative Markdown images. Each level owns its source and may override companion resources or editing. The host loads content and owns returned URL lifetimes. Use binary `file` or an accessible `url` for images. Actions and permission badges are host slots; the footer accepts modification time and attribution.

The viewer is lazy-loaded and the shell is SSR-safe. See the bilingual component documentation and runnable console example in `frontend/docs/docs/` for the full contract.
