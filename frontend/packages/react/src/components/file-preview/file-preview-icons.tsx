import type { ReactNode } from 'react';

import {
  AudioIcon,
  PdfIcon,
  PptIcon,
  VideoIcon,
  WordIcon,
} from '../../icons/index.js';

/** 页签行图标的渲染尺寸。Rendered size of a tab-row icon. */
const ICON_SIZE = 14;

/** 图标集里已有的文件类型，其余格式回退到通用图标。 */
const ICON_BY_EXTENSION: Readonly<Record<string, () => ReactNode>> = {
  pdf: () => <PdfIcon width={ICON_SIZE} height={ICON_SIZE} />,
  doc: () => <WordIcon width={ICON_SIZE} height={ICON_SIZE} />,
  docx: () => <WordIcon width={ICON_SIZE} height={ICON_SIZE} />,
  ppt: () => <PptIcon width={ICON_SIZE} height={ICON_SIZE} />,
  pptx: () => <PptIcon width={ICON_SIZE} height={ICON_SIZE} />,
  mp3: () => <AudioIcon width={ICON_SIZE} height={ICON_SIZE} />,
  wav: () => <AudioIcon width={ICON_SIZE} height={ICON_SIZE} />,
  m4a: () => <AudioIcon width={ICON_SIZE} height={ICON_SIZE} />,
  flac: () => <AudioIcon width={ICON_SIZE} height={ICON_SIZE} />,
  mp4: () => <VideoIcon width={ICON_SIZE} height={ICON_SIZE} />,
  webm: () => <VideoIcon width={ICON_SIZE} height={ICON_SIZE} />,
  mov: () => <VideoIcon width={ICON_SIZE} height={ICON_SIZE} />,
  avi: () => <VideoIcon width={ICON_SIZE} height={ICON_SIZE} />,
};

/** 取文件名的扩展名，无扩展名时返回空串。 */
export function getFileExtension(fileName: string): string {
  const name = fileName.trim().toLowerCase();
  const index = name.lastIndexOf('.');
  return index > 0 && index < name.length - 1 ? name.slice(index + 1) : '';
}

/**
 * 通用文件图标，用于图标集尚未覆盖的格式。
 * A generic file icon for formats the icon set does not cover yet.
 */
export function GenericFileIcon({ size = ICON_SIZE }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
    >
      <path
        d="M9 1.5H4.5a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V5z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      <path d="M9 1.5V5h3.5" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}

/**
 * 按文件名选择页签图标。图标集未覆盖的格式回退到通用文件图标。
 * Picks the tab icon from the file name, falling back to the generic file icon
 * for formats the icon set does not cover yet.
 */
export function resolveFileIcon(fileName: string): ReactNode {
  const icon = ICON_BY_EXTENSION[getFileExtension(fileName)];
  return icon ? icon() : <GenericFileIcon />;
}

/**
 * 内容页签的内置图标，用于宿主未提供 `icon` 的场景。
 * The built-in icon of a content tab, used when the host supplies no `icon`.
 */
export function ContentTabIcon({ size = ICON_SIZE }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
    >
      <rect
        x="2"
        y="2.5"
        width="12"
        height="11"
        rx="1.5"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path d="M2 6h12" stroke="currentColor" strokeWidth="1.2" />
      <path d="M6.5 6v7.5" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}
