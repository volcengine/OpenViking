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
import { swatches } from '../../../icons/library-swatches.json';
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
 * 五组渐变的色值放在主题基线的 `--ov-ui-library-swatch-*-default` 上，这里只按
 * 名字选下标：颜色因此仍可被宿主通过公开的 `--ov-ui-color-library-swatch-*`
 * 统一覆盖，而不是写死在内联样式里。亮暗主题沿用同一套默认值——这是设计稿的
 * 身份色，不随主题变化。
 *
 * The five gradients live in the theme baseline as
 * `--ov-ui-library-swatch-*-default`; this only picks an index by name, so a host
 * can still override every swatch through the public
 * `--ov-ui-color-library-swatch-*` variables instead of being stuck with inline
 * literals. Both themes share the same defaults — these are the design's identity
 * colours and do not follow the theme.
 */
/**
 * 兜底色值，与 CSS 侧同源：调色板只维护在 `packages/icons/library-swatches.json`，
 * 这里直接导入，`theme.css` 的 `--ov-ui-library-swatch-N-default` 由
 * `scripts/sync-library-swatches.mjs` 从同一份 JSON 生成。只改一处颜色即可同时
 * 更新「加载样式表」与「不加载样式表」两条路径。
 *
 * Last-resort values, same source as the CSS side: the palette lives only in
 * `packages/icons/library-swatches.json` and is imported here, while
 * `scripts/sync-library-swatches.mjs` generates `theme.css`'s
 * `--ov-ui-library-swatch-N-default` from that same JSON. Editing one colour
 * updates both the "stylesheet loaded" and "no stylesheet" paths.
 */
const LIBRARY_FALLBACKS: readonly string[] = swatches;

/** 用名字算出稳定的渐变下标。Derives a stable gradient index from the name. */
function gradientOf(name: string) {
  const hash = Array.from(name).reduce(
    (total, character) => total + character.charCodeAt(0),
    0,
  );
  return hash % LIBRARY_FALLBACKS.length;
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
  const index = gradientOf(name);
  // 变量名从 1 开始编号，和 theme.css 里的声明对齐。
  const swatch = index + 1;
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'block',
        width: 14,
        height: 14,
        borderRadius: '50%',
        // 公开 token → 基线私有默认值 → 兜底字面值，宿主因此能整组换色。
        background: `var(--ov-ui-color-library-swatch-${swatch}, var(--ov-ui-library-swatch-${swatch}-default, ${LIBRARY_FALLBACKS[index]}))`,
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
