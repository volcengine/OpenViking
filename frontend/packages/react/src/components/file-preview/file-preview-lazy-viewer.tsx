import { LazyContent } from '../../shared/lazy-content.js';

import { LoadingState } from './file-preview-state.js';
import type { FilePreviewViewerProps } from './file-preview-viewer.js';

/**
 * 懒加载的正文入口：底座与 Adapter 只在真正渲染文件时下载。
 *
 * 直接静态导入会让包的顶层导入链拉进浏览器专用依赖，破坏服务端渲染与
 * 无 `window` 环境下的模块解析，因此这里用动态导入隔开。
 *
 * Lazy entry of the body: the base and its adapters download only when a file
 * actually renders. A static import would pull browser-only dependencies into the
 * package's top-level import chain, breaking server rendering and module
 * resolution without `window`.
 */
const load = async () => {
  const { FilePreviewViewer } = await import('./file-preview-viewer.js');
  return { default: FilePreviewViewer };
};

/** 懒加载正文：先渲染加载态，模块就绪后交给底座。 */
export function LazyFilePreviewViewer(props: FilePreviewViewerProps) {
  return (
    <LazyContent
      load={load}
      contentProps={props}
      fallback={<LoadingState text={props.text} />}
    />
  );
}
