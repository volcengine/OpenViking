import type { HTMLAttributes } from 'react';

/**
 * 目录加载子节点时，替换展开箭头显示的转圈。用纯 CSS 画环，不依赖图标资源。
 * 需要换成图标或品牌化的加载态时，改用 `FileTree` 的 `slots.spinner` 覆盖。
 *
 * The spinner shown in place of the expand chevron while a directory loads its
 * children. Drawn with CSS alone, so it needs no icon asset. To swap in an icon
 * or a branded loader, override it with `FileTree`'s `slots.spinner`.
 */
export function FileTreeSpinner({
  className,
  ...rest
}: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      aria-hidden="true"
      {...rest}
      className={['ov-file-tree__spinner', className].filter(Boolean).join(' ')}
    />
  );
}
