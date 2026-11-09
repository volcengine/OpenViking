import type { SVGProps } from 'react';

/**
 * 详情区的图标：返回、复制、历史、看板与文件类型。都用当前文字颜色描边，因此
 * 跟随主题。
 *
 * Detail-pane icons: back, copy, history, dashboard and the file type. All stroke
 * with the current text colour, so they follow the theme.
 */

const SIZE = 14;

function iconProps(size = SIZE): SVGProps<SVGSVGElement> {
  return {
    width: size,
    height: size,
    viewBox: '0 0 16 16',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.4,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    'aria-hidden': true,
    focusable: 'false',
  };
}

/** 复制图标。Copy icon. */
export function CopyIcon({ size = 12 }: { size?: number }) {
  return (
    <svg {...iconProps(size)}>
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
      <path d="M10.5 5.5v-1a1.5 1.5 0 0 0-1.5-1.5H4a1.5 1.5 0 0 0-1.5 1.5v5A1.5 1.5 0 0 0 4 11h1" />
    </svg>
  );
}

/** 历史图标。History icon. */
export function HistoryIcon() {
  return (
    <svg {...iconProps()}>
      <path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9" />
      <path d="M2.5 3v2.5H5" />
      <path d="M8 5v3.2l2.2 1.3" />
    </svg>
  );
}

/** 看板图标。Dashboard icon. */
export function DashboardIcon() {
  return (
    <svg {...iconProps(12)}>
      <rect x="2.5" y="2.5" width="4.5" height="5.5" rx="1" />
      <rect x="9" y="2.5" width="4.5" height="3" rx="1" />
      <rect x="2.5" y="10" width="4.5" height="3.5" rx="1" />
      <rect x="9" y="7.5" width="4.5" height="6" rx="1" />
    </svg>
  );
}

/** 下载图标。Download icon. */
export function DownloadIcon() {
  return (
    <svg {...iconProps()}>
      <path d="M8 2.5v8" />
      <path d="M5 7.5 8 10.5l3-3" />
      <path d="M2.5 12.5h11" />
    </svg>
  );
}
