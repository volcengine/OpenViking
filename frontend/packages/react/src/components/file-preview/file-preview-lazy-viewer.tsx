import { LazyContent } from '../../shared/lazy-content.js';

import { LoadingState } from './file-preview-state.js';
import type { FilePreviewViewerProps } from './file-preview-viewer.js';

/** 重型视图按需加载，减少控制台首屏下载与解析开销。 */
const load = async () => {
  const { FilePreviewViewer } = await import('./file-preview-viewer.js');
  return { default: FilePreviewViewer };
};

/** 懒加载正文：先渲染加载态，模块就绪后交给底座。 */
export function LazyFilePreviewViewer(props: FilePreviewViewerProps) {
  return (
    <LazyContent
      monitoringTarget="file_preview_viewer_load"
      load={load}
      contentProps={props}
      fallback={<LoadingState text={props.text} />}
    />
  );
}
