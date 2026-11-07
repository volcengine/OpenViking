import type { ComponentPropsWithoutRef, ComponentType } from 'react';

import AudioSvg from '../../../icons/svg/audio.svg?react';
import ChevronRightSvg from '../../../icons/svg/chevron-right.svg?react';
import DocumentSvg from '../../../icons/svg/file.svg?react';
import FolderSvg from '../../../icons/svg/folder.svg?react';
import FolderOpenSvg from '../../../icons/svg/folder-open.svg?react';
import PdfSvg from '../../../icons/svg/pdf.svg?react';
import PptSvg from '../../../icons/svg/ppt.svg?react';
import VideoSvg from '../../../icons/svg/video.svg?react';
import WordSvg from '../../../icons/svg/word.svg?react';

/**
 * 图标的属性。渲染为内联 SVG，接收标准 SVG 属性；`width` 与 `height` 默认 16，
 * 无障碍描述用 `aria-label`（装饰性图标省略，SVG 本身带 `aria-hidden`）。
 *
 * Props of an icon. Icons render as inline SVG and accept standard SVG props;
 * `width` and `height` default to 16. Describe one with `aria-label`; decorative
 * icons omit it and the SVG stays `aria-hidden`.
 */
export type IconProps = Omit<ComponentPropsWithoutRef<'svg'>, 'children'>;

/**
 * 图标组件的公开类型。声明里只出现这个类型，不出现 `*.svg?react` 模块引用——
 * 发布包不含 SVG 源文件，声明若引用它们，独立消费者会报 TS2307。
 *
 * The public type of an icon component. Declarations mention only this type,
 * never a `*.svg?react` module: the published package ships no SVG sources, so a
 * declaration that referenced them would fail with TS2307 for a standalone
 * consumer.
 */
export type IconComponent = ComponentType<IconProps>;

export const AudioIcon: IconComponent = AudioSvg;
export const ChevronRightIcon: IconComponent = ChevronRightSvg;
export const DocumentIcon: IconComponent = DocumentSvg;
export const FolderIcon: IconComponent = FolderSvg;
export const FolderOpenIcon: IconComponent = FolderOpenSvg;
export const PdfIcon: IconComponent = PdfSvg;
export const PptIcon: IconComponent = PptSvg;
export const VideoIcon: IconComponent = VideoSvg;
export const WordIcon: IconComponent = WordSvg;

export {
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from './file-type-icon.js';
export type { FileTypeIconComponent } from './file-type-icon.js';
