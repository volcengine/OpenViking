import { useEffect } from 'react';
import type { ReactNode } from 'react';

/**
 * 底座的加载插槽只在它自己显示加载态时挂载，卸载即表示加载结束。用挂载与卸载
 * 上报这一对起止，比读 DOM 更直接：底座把内容包在 `visibility: hidden` 里，光看
 * 外壳状态会以为已经就绪——实测外壳 `ready` 之后，渲染器自己的遮罩还会再停约
 * 两帧（Markdown 解析、图片解码）。
 *
 * The base mounts its loading slot only while it shows a loading state, and unmounting
 * means it ended. Reporting that pair is more direct than reading the DOM: the base
 * keeps content under `visibility: hidden`, so the shell state alone looks ready — in
 * practice the renderer's own overlay lingers about two frames after the shell reaches
 * `ready` (Markdown parsing, image decoding).
 */
export function FilePreviewLoadingReporter({
  onLoadingChange,
  children,
}: {
  onLoadingChange: (loading: boolean) => void;
  children: ReactNode;
}) {
  // useEffect 必要性：挂载与卸载是提交后的生命周期事件，渲染期间不能上报——那会
  // 在父组件渲染阶段更新它的 state。清理函数把「已结束」补报出去。
  //
  // Mount and unmount are post-commit lifecycle events; reporting during render would
  // update the parent's state mid-render. The cleanup reports the end.
  useEffect(() => {
    onLoadingChange(true);
    return () => onLoadingChange(false);
  }, [onLoadingChange]);
  return <>{children}</>;
}
