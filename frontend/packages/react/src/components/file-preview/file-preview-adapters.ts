import { fenceMarkdownFrontmatter } from '../../shared/markdown/preview-frontmatter.js';
import { code } from '@ark-design/ui/file-viewer/adapters/code';
import { diff } from '@ark-design/ui/file-viewer/adapters/diff';
import { html } from '@ark-design/ui/file-viewer/adapters/html';
import { image } from '@ark-design/ui/file-viewer/adapters/image';
import { markdown } from '@ark-design/ui/file-viewer/adapters/markdown';
import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';
import { media } from '@ark-design/ui/file-viewer/adapters/media';
import { pdf } from '@ark-design/ui/file-viewer/adapters/pdf';
import { powerpoint } from '@ark-design/ui/file-viewer/adapters/powerpoint';
import { table } from '@ark-design/ui/file-viewer/adapters/table';
import { text } from '@ark-design/ui/file-viewer/adapters/text';
import { word } from '@ark-design/ui/file-viewer/adapters/word';
import type { FileViewerCoreProps } from '@ark-design/ui/file-viewer/core';
import { DirectoryHeading } from './directory-heading.js';
import { sessionJsonAdapter } from './session-json-adapter.js';

import {
  originalImageTarget,
  preserveVikingImages,
  recoverImportedMarkdownImages,
} from './file-preview-markdown-images.js';

export type DirectoryLinkMode = 'resources' | 'anchors';

/**
 * 组件内置的格式集合：文本、文档、图片与音视频。
 *
 * 图片是内容式 Adapter，音视频是引用式 Adapter；底座会按 Adapter 声明的读取
 * 方式自动转换 `source`，宿主只需给出内容、文件或链接，不用区分。
 *
 * CAD 与 3D 需要额外的引擎与伴随资源，不放进公共组件。
 *
 * 本模块会静态引入底座，因此只能被懒加载的正文模块引用，不能进入包的顶层
 * 导入链，减少控制台首屏加载体积。
 *
 * The formats the component ships: text, documents, images and media. The image
 * adapter reads content while the media adapter reads a reference; the base
 * converts `source` according to the adapter's declared mode, so the host only
 * supplies content, a file or a URL. CAD and 3D need extra engines and companion
 * resources, so they stay out of the shared component. This module statically
 * imports the base, so only the lazily loaded body may reference it: the base
 * pulls in browser-only packages, and a top-level import would break server
 * rendering.
 */
export function createFilePreviewAdapterList({
  onLinkClick,
  resolveImage,
  directoryLinks,
  sessionMessages = false,
}: {
  /**
   * 接管 Markdown 正文里的链接；目录正文通过内部模式保留外链原生导航。
   *
   * Handles Markdown links; directory bodies use an internal mode to preserve native external links.
   */
  onLinkClick?: MarkdownAdapterOptions['onLinkClick'];
  resolveImage?: (src: string) => Promise<string>;
  directoryLinks?: DirectoryLinkMode;
  sessionMessages?: boolean;
} = {}): FileViewerCoreProps['adapters'] {
  return [
    code({ showLineNumbers: true }),
    text({ showLineNumbers: true }),
    sessionJsonAdapter(sessionMessages),
    markdown({
      onLinkClick: directoryLinks ? undefined : onLinkClick,
      allowedLinkProtocols:
        directoryLinks === 'resources' ? ['viking'] : undefined,
      rendererProps: {
        slots: directoryLinks ? { Header: DirectoryHeading } : undefined,
        linkPolicy: directoryLinks
          ? (target) => {
              const url = target.trim();
              const scheme = /^([a-z][a-z\d+.-]*):/i.exec(url)?.[1];
              return url.startsWith('#') ||
                (directoryLinks === 'resources' &&
                  url &&
                  !url.startsWith('//') &&
                  (!scheme || scheme.toLowerCase() === 'viking'))
                ? { render: true, callback: true, navigation: 'callback' }
                : true;
            }
          : undefined,
        eventCallbacks: {
          onLinkClick:
            directoryLinks && onLinkClick
              ? (event, link) =>
                  onLinkClick(event, { url: link.url, fileType: 'markdown' })
              : undefined,
        },
        // Parse raw HTML, then keep Ark's sanitizer in charge of tags and attributes.
        parsing: { enabledHtmlTags: true },
        sourcePlugins: (current) => [
          ...current,
          {
            name: 'ov-yaml-frontmatter',
            config: { priority: -1 },
            modifier: ({ source }) => fenceMarkdownFrontmatter(source),
            before: () => {},
            after: () => {},
            beforeEach: () => {},
            afterEach: () => {},
          },
        ],
        astPlugins: (current) => [
          ...current,
          {
            name: 'ov-viking-images',
            config: { priority: 2 },
            modifier: (root) => {
              recoverImportedMarkdownImages(root);
              preserveVikingImages(root);
            },
            before: () => {},
            after: () => {},
            beforeEach: () => {},
            afterEach: () => {},
          },
        ],
        imageOptions: {
          processSrcBeforeLoad: resolveImage
            ? (src) => resolveImage(originalImageTarget(src))
            : undefined,
          forceHttps: false,
          objectFit: 'contain',
          responsiveNaturalSize: true,
        },
      },
    }),
    html(),
    table(),
    pdf({ initialFit: 'fit-width' }),
    word({ initialFit: 'fit-width' }),
    powerpoint({ initialFit: 'fit-width' }),
    image(),
    media(),
    diff(),
  ];
}
