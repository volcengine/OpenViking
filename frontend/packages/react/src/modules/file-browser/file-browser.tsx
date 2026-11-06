import { FileBrowserToolbarButton } from './file-browser-toolbar-button.js';
import type { FileBrowserProps } from './types.js';

export { FileBrowserToolbarButton };

export type {
  FileBrowserLabels,
  FileBrowserProps,
  FileBrowserToolbarButtonProps,
} from './types.js';

/** 占位组件：渲染一个带模块名的空容器，真实实现随模块 Hook 与组合视图迁入。 */
export function FileBrowser({ className, style, labels }: FileBrowserProps) {
  return (
    <div className={className} style={style}>
      {labels?.title ?? 'File browser'}
    </div>
  );
}
