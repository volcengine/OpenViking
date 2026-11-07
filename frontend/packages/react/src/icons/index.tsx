import type { ComponentPropsWithoutRef } from 'react';

import AudioIcon from '../../../icons/svg/audio.svg?react';
import ChevronRightIcon from '../../../icons/svg/chevron-right.svg?react';
import DocumentIcon from '../../../icons/svg/file.svg?react';
import FolderIcon from '../../../icons/svg/folder.svg?react';
import FolderOpenIcon from '../../../icons/svg/folder-open.svg?react';
import PdfIcon from '../../../icons/svg/pdf.svg?react';
import PptIcon from '../../../icons/svg/ppt.svg?react';
import VideoIcon from '../../../icons/svg/video.svg?react';
import WordIcon from '../../../icons/svg/word.svg?react';

/**
 * 图标的属性。渲染为内联 SVG，接收标准 SVG 属性；`width` 与 `height` 默认 16，
 * 无障碍描述用 `aria-label`（装饰性图标省略，SVG 本身带 `aria-hidden`）。
 *
 * Props of an icon. Icons render as inline SVG and accept standard SVG props;
 * `width` and `height` default to 16. Describe one with `aria-label`; decorative
 * icons omit it and the SVG stays `aria-hidden`.
 */
export type IconProps = Omit<ComponentPropsWithoutRef<'svg'>, 'children'>;

export {
  AudioIcon,
  ChevronRightIcon,
  DocumentIcon,
  FolderIcon,
  FolderOpenIcon,
};
export { PdfIcon, PptIcon, VideoIcon, WordIcon };
export {
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from './file-type-icon.js';
export type { FileTypeIconComponent } from './file-type-icon.js';
