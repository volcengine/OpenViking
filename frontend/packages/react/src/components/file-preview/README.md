# FilePreview

English / [中文](README_CN.md)

Multi-format file preview. The host supplies the content and file name; the component handles format detection, renderer loading, the header, tabs, view switching and editing.

`index.ts` maps to `@openviking/react/components/file-preview`. The entry, stylesheet and package exports are reserved; no component or type is exported yet. The implementation is rewritten against this document, and the source is the source of truth for the field list.

## Development entry

Development and build commands are in the [frontend workspace README](../../../../../README.md); module conventions are in the [package guide](../../../README.md).

The standalone style entry is `@openviking/react/components/file-preview/style.css`, which is empty before the implementation lands.
