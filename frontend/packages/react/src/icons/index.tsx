import type { ComponentPropsWithoutRef, ComponentType } from 'react';

import ArrowRightSvg from '../../../icons/svg/arrow-right.svg?react';
import AudioSvg from '../../../icons/svg/audio.svg?react';
import ChevronRightSvg from '../../../icons/svg/chevron-right.svg?react';
import DocumentSvg from '../../../icons/svg/file.svg?react';
import FolderSvg from '../../../icons/svg/folder.svg?react';
import FolderOpenSvg from '../../../icons/svg/folder-open.svg?react';
import PdfSvg from '../../../icons/svg/pdf.svg?react';
import SpaceSvg from '../../../icons/svg/scope-switcher/space.svg?react';
import UserSvg from '../../../icons/svg/scope-switcher/user.svg?react';
import PptSvg from '../../../icons/svg/ppt.svg?react';
import VideoSvg from '../../../icons/svg/video.svg?react';
import WordSvg from '../../../icons/svg/word.svg?react';

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
export const SpaceIcon = createIcon(SpaceSvg, 'SpaceIcon');
export const UserIcon = createIcon(UserSvg, 'UserIcon');

/**
 * 库的默认图标。库没有固定字形，设计稿里是一个按名称取色的圆形渐变底，因此这里
 * 画出的是色块而不是描边图形；同一个名字始终取到同一种渐变。
 *
 * The default library icon. A library has no fixed glyph — the design draws a round
 * gradient swatch picked by name — so this renders a colour block rather than an
 * outline. The same name always resolves to the same gradient.
 *
 * 这五个渐变是身份色而不是主题语义色：设计稿规定亮暗两种主题下用同一套渐变，
 * 因此这里写字面值而不取 `--ov-ui-color-*`。语义色仍走主题基线。
 *
 * These five gradients are identity colours rather than theme semantics: the design
 * keeps one set of gradients in both light and dark, so they are written as literals
 * here instead of `--ov-ui-color-*`. Semantic colours still come from the baseline.
 */
const LIBRARY_GRADIENTS = [
  'linear-gradient(145deg, #ffd8ec 8%, #ffabc8 46%, #d8cbff 100%)',
  'linear-gradient(145deg, #d9e8ff 8%, #b8a6ff 52%, #f3d6ff 100%)',
  'linear-gradient(145deg, #ffe6bc 8%, #ffaf96 48%, #ff8bc5 100%)',
  'linear-gradient(145deg, #eaf7ba 8%, #b5efb8 52%, #73d9c6 100%)',
  'linear-gradient(145deg, #fff1ba 8%, #ffc776 52%, #bd9cff 100%)',
];

/** 用名字算出稳定的渐变下标。Derives a stable gradient index from the name. */
function gradientOf(name: string) {
  const hash = Array.from(name).reduce(
    (total, character) => total + character.charCodeAt(0),
    0,
  );
  return LIBRARY_GRADIENTS[hash % LIBRARY_GRADIENTS.length]!;
}

/**
 * 库的默认图标：14 × 14 的圆形渐变块。装饰性图形，不进入可访问性树。
 *
 * `name` 决定取哪一种渐变，同一个名字始终一致；不传时取第一种。
 *
 * The default library icon: a 14 × 14 round gradient swatch. It is decorative and
 * stays out of the accessibility tree.
 *
 * `name` selects the gradient and always resolves the same way for one name; the
 * first gradient applies when it is omitted.
 */
export function LibraryIcon({ name = '' }: { name?: string }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'block',
        width: 14,
        height: 14,
        borderRadius: '50%',
        background: gradientOf(name),
      }}
    />
  );
}

export {
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from './file-type-icon.js';
export type { FileTypeIconComponent } from './file-type-icon.js';
export { VikingBotIcon } from './viking-bot-icons.js';
export type { VikingBotIconName } from './viking-bot-icons.js';
