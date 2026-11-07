import type { FileBrowserToolbarButtonProps } from './types.js';

/**
 * 工具栏按钮。模块自己的搜索与刷新用它渲染，宿主在 `toolbarActions` 里也用它
 * 组合入口，两边的外观因此保持一致。
 *
 * A toolbar button. The module renders its own search and refresh with it, and
 * the host composes its entries with it inside `toolbarActions`, so both sides
 * look the same.
 */
export function FileBrowserToolbarButton({
  icon,
  children,
  onClick,
  disabled,
  className,
  ...rest
}: FileBrowserToolbarButtonProps) {
  return (
    <button
      {...rest}
      type="button"
      disabled={disabled}
      className={['ov-file-browser__toolbar-button', className]
        .filter(Boolean)
        .join(' ')}
      onClick={onClick}
    >
      <span className="ov-file-browser__toolbar-button-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="ov-file-browser__toolbar-button-label">{children}</span>
    </button>
  );
}
