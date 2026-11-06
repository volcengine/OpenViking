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
    <div
      className={['ov-module-placeholder', className].filter(Boolean).join(' ')}
      style={style}
    >
      <p className="ov-module-placeholder__title">
        {labels?.title ?? 'File browser'}
      </p>
      <p className="ov-module-placeholder__description">
        {labels?.description ??
          'Placeholder. Functionality is not available yet.'}
      </p>
    </div>
  );
}
