import WikiDiscussSvg from '../../../icons/svg/dashboards/discuss.svg?react';
import WikiHistorySvg from '../../../icons/svg/dashboards/history.svg?react';
import WikiSuccessSvg from '../../../icons/svg/dashboards/success.svg?react';
import WikiEditSvg from '../../../icons/svg/dashboards/edit.svg?react';
import ActivitySvg from '../../../icons/svg/dashboards/activity.svg?react';
import ScanSearchSvg from '../../../icons/svg/dashboards/scan-search.svg?react';
import FileInputSvg from '../../../icons/svg/dashboards/file-input.svg?react';
import type { ComponentPropsWithoutRef, ComponentType } from 'react';

import ArrowRightSvg from '../../../icons/svg/arrow-right.svg?react';
import AudioSvg from '../../../icons/svg/audio.svg?react';
import ChevronRightSvg from '../../../icons/svg/chevron-right.svg?react';
import DocumentSvg from '../../../icons/svg/file.svg?react';
import FolderSvg from '../../../icons/svg/folder.svg?react';
import FolderOpenSvg from '../../../icons/svg/folder-open.svg?react';
import PdfSvg from '../../../icons/svg/pdf.svg?react';
import PptSvg from '../../../icons/svg/ppt.svg?react';
import VideoSvg from '../../../icons/svg/video.svg?react';
import WikiWarningSvg from '../../../icons/svg/dashboards/warning.svg?react';
import WikiBookSvg from '../../../icons/svg/dashboards/wiki-book.svg?react';
import WordSvg from '../../../icons/svg/word.svg?react';
import ExperienceBoardSvg from '../../../icons/svg/dashboards/experience-board.svg?react';
import WikiBoardSvg from '../../../icons/svg/dashboards/wiki-board.svg?react';
import WikiConflictBoardSvg from '../../../icons/svg/dashboards/wiki-conflict-board.svg?react';

/**
 * 图标的属性。渲染为内联 SVG，接收标准 SVG 属性；`width` 与 `height` 默认 16
 *（ArrowRightIcon 为 14），
 * 无障碍描述用 `aria-label`（装饰性图标省略，SVG 本身带 `aria-hidden`）。
 *
 * Props of an icon. Icons render as inline SVG and accept standard SVG props;
 * `width` and `height` default to 16 (14 for ArrowRightIcon). Describe one with
 * `aria-label`; decorative icons omit it and the SVG stays `aria-hidden`.
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

/**
 * 装饰性图标的无障碍默认值：调用方没有给可访问名称时隐藏，给了就保留在可访问性
 * 树中。基线的 `<img alt="">` 默认就是装饰性图片，这里保持一致，同时允许调用方
 * 显式覆盖 `aria-hidden`。
 *
 * The accessible default for a decorative icon: hidden when the caller supplies
 * no name, kept in the accessibility tree when it does. The baseline `<img
 * alt="">` was decorative by default; this keeps that behaviour while letting a
 * caller override `aria-hidden` explicitly.
 */
function createIcon(Svg: IconComponent, name: string): IconComponent {
  function Icon({ 'aria-label': ariaLabel, ...props }: IconProps) {
    return (
      <Svg
        aria-hidden={ariaLabel ? undefined : true}
        aria-label={ariaLabel}
        {...props}
      />
    );
  }

  Icon.displayName = name;
  return Icon;
}

export const ArrowRightIcon = createIcon(ArrowRightSvg, 'ArrowRightIcon');
export const AudioIcon = createIcon(AudioSvg, 'AudioIcon');
export const ChevronRightIcon = createIcon(ChevronRightSvg, 'ChevronRightIcon');
export const DocumentIcon = createIcon(DocumentSvg, 'DocumentIcon');
export const FolderIcon = createIcon(FolderSvg, 'FolderIcon');
export const FolderOpenIcon = createIcon(FolderOpenSvg, 'FolderOpenIcon');
export const PdfIcon = createIcon(PdfSvg, 'PdfIcon');
export const PptIcon = createIcon(PptSvg, 'PptIcon');
export const VideoIcon = createIcon(VideoSvg, 'VideoIcon');
export const WordIcon = createIcon(WordSvg, 'WordIcon');
export const ExperienceBoardIcon = createIcon(
  ExperienceBoardSvg,
  'ExperienceBoardIcon',
);
export const WikiBoardIcon = createIcon(WikiBoardSvg, 'WikiBoardIcon');
export const WikiConflictBoardIcon = createIcon(
  WikiConflictBoardSvg,
  'WikiConflictBoardIcon',
);

export {
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from './file-type-icon.js';
export type { FileTypeIconComponent } from './file-type-icon.js';
export { VikingBotIcon } from './viking-bot-icons.js';
export type { VikingBotIconName } from './viking-bot-icons.js';

export const WikiWarningIcon = createIcon(WikiWarningSvg, 'WikiWarningIcon');
export const WikiBookIcon = createIcon(WikiBookSvg, 'WikiBookIcon');

export const FileInputIcon = createIcon(FileInputSvg, 'FileInputIcon');

export const ScanSearchIcon = createIcon(ScanSearchSvg, 'ScanSearchIcon');

export const ActivityIcon = createIcon(ActivitySvg, 'ActivityIcon');

export const WikiEditIcon = createIcon(WikiEditSvg, 'WikiEditIcon');

export const WikiSuccessIcon = createIcon(WikiSuccessSvg, 'WikiSuccessIcon');

export const WikiHistoryIcon = createIcon(WikiHistorySvg, 'WikiHistoryIcon');

export const WikiDiscussIcon = createIcon(WikiDiscussSvg, 'WikiDiscussIcon');
