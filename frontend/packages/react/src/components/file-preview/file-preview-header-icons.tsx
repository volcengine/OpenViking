import type { ReactNode } from 'react';

/**
 * 图标按钮：方形、只有图标，无障碍名称通过 `aria-label` 提供。
 * `outlined` 用于动作组，带描边与浅色底槽；路径旁的复制按钮不带。
 *
 * An icon-only square button, named through `aria-label`. `outlined` is for the
 * action group, adding a border and a tinted surface; the copy button next to
 * the path leaves it off.
 */
export function IconButton({
  label,
  disabled,
  outlined,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  outlined?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={[
        'ov-file-preview__action',
        outlined ? 'ov-file-preview__action--outlined' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

/**
 * 复制图标。用内联 SVG 而不是 `⧉`/`⎘` 这类字符：字符的实际字形取决于宿主
 * 字体，不同环境下大小、粗细和基线都不一样。
 *
 * A copy icon. Inline SVG rather than a character such as `⧉`/`⎘`: a character's
 * glyph depends on the host font, so its size, weight and baseline differ between
 * environments.
 */
export function CopyIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="5.5" y="2.5" width="8" height="8" rx="1.5" />
      <path d="M10.5 13.5h-5a2 2 0 0 1-2-2v-5" />
    </svg>
  );
}

/** 下载图标，与复制图标同一套线条参数。 A download icon on the same stroke grid. */
export function DownloadIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M8 2.5v7" />
      <path d="M5 7l3 3 3-3" />
      <path d="M3 12.5h10" />
    </svg>
  );
}
