import { code } from '@ark-design/ui/file-viewer/adapters/code';
import { diff } from '@ark-design/ui/file-viewer/adapters/diff';
import { html } from '@ark-design/ui/file-viewer/adapters/html';
import { image } from '@ark-design/ui/file-viewer/adapters/image';
import { json } from '@ark-design/ui/file-viewer/adapters/json';
import { markdown } from '@ark-design/ui/file-viewer/adapters/markdown';
import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';
import { media } from '@ark-design/ui/file-viewer/adapters/media';
import { pdf } from '@ark-design/ui/file-viewer/adapters/pdf';
import { powerpoint } from '@ark-design/ui/file-viewer/adapters/powerpoint';
import { table } from '@ark-design/ui/file-viewer/adapters/table';
import { text } from '@ark-design/ui/file-viewer/adapters/text';
import { word } from '@ark-design/ui/file-viewer/adapters/word';
import type { FileViewerCoreProps } from '@ark-design/ui/file-viewer/core';

/**
 * 组件内置的格式集合：文本、文档、图片与音视频。
 *
 * 图片是内容式 Adapter，音视频是引用式 Adapter；底座会按 Adapter 声明的读取
 * 方式自动转换 `source`，宿主只需给出内容、文件或链接，不用区分。
 *
 * CAD 与 3D 需要额外的引擎与伴随资源，不放进公共组件。
 *
 * 本模块会静态引入底座，因此只能被懒加载的正文模块引用，不能进入包的顶层
 * 导入链——底座会静态引入浏览器专用组件，顶层导入会破坏服务端渲染。
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
}: {
  /**
   * Markdown 正文里的链接被点击时调用。传入后由回调接管导航，底座不再自行
   * 跳转；只有 Markdown Adapter 支持这个回调，其余格式忽略它。
   *
   * Called when a link inside a Markdown body is clicked. Supplying it hands
   * navigation to the callback and the base no longer jumps on its own. Only the
   * Markdown adapter supports this; other formats ignore it.
   */
  onLinkClick?: MarkdownAdapterOptions['onLinkClick'];
} = {}): FileViewerCoreProps['adapters'] {
  return [
    code({ showLineNumbers: true }),
    text({ showLineNumbers: true }),
    json({ defaultExpandDepth: 4 }),
    markdown({ onLinkClick }),
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
